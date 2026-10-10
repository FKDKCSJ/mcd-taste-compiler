#!/usr/bin/env python3
"""味觉编译器：把一句话里的状态（时间/场合/情绪/强度）编译成门店真实可点的三种吃法。

只调用麦当劳 MCP 的只读工具（query-meals、list-nutrition-foods、calculate-price），
永远不下单。下单交给上层 Agent，在用户明确确认后调用 create-order。

用法：
  MCD_MCP_TOKEN=xxx python3 compile.py --mood "加班到十一点 emo了" --store 1950564 --hours 07:00-22:00
  python3 compile.py --mood "热死了" --menu menu.json --nutrition nutri.json   # 离线
"""
import argparse
import itertools
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MCP_URL = os.environ.get("MCD_MCP_URL", "https://mcp.mcd.cn")
CRISIS_EXIT = 3

# python.org 版 Python 不带系统证书，补上常见系统 CA 路径
SSL_CTX = ssl.create_default_context()
for _ca in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
    if os.path.exists(_ca):
        SSL_CTX.load_verify_locations(_ca)

MAIN_KINDS = {"main", "breakfast"}
SIDE_KINDS = {"side", "chicken"}
DRINK_KINDS = {"drink", "coffee"}


# ---------- MCP ----------

def mcp_call(tool, args=None):
    token = os.environ.get("MCD_MCP_TOKEN")
    if not token:
        sys.exit("缺少环境变量 MCD_MCP_TOKEN")
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": tool, "arguments": args or {}}}
    req = urllib.request.Request(
        MCP_URL, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    try:
        with urllib.request.urlopen(req, timeout=20, context=SSL_CTX) as r:
            raw = r.read().decode()
    except urllib.error.HTTPError as e:
        sys.exit({401: "MCP Token 无效或已过期", 429: "触发限流（600 次/分钟），稍后再试"}.get(e.code, f"MCP HTTP {e.code}"))
    except urllib.error.URLError as e:
        sys.exit(f"连不上 {MCP_URL}：{e.reason}")
    # 兼容 SSE：取最后一个 data: 行
    if raw.lstrip().startswith(("event:", "data:")):
        raw = [l[5:] for l in raw.splitlines() if l.startswith("data:")][-1]
    return unwrap(json.loads(raw))


def unwrap(resp):
    """MCP 返回的 text 里是说明文字 + 原始业务 JSON，抠出业务 JSON。"""
    if "result" in resp:
        text = resp["result"]["content"][0]["text"]
    elif "error" in resp:
        sys.exit(f"MCP 错误：{resp['error']}")
    else:
        return resp
    i = text.find('{"success"')
    if i < 0:
        sys.exit(f"无法解析 MCP 返回：{text[:200]}")
    body = json.JSONDecoder().raw_decode(text[i:])[0]
    if not body.get("success"):
        sys.exit(f"业务失败：{body.get('message')}")
    return body


def load_or_fetch(path, tool, args=None):
    if path:
        return unwrap(json.loads(Path(path).read_text()))
    return mcp_call(tool, args)



# ---------- 词法分析：把一句话拆成人的状态 ----------

STRONG = ["超级", "超", "巨", "太", "特别", "非常", "疯了", "爆", "死了", "麻了", "!!", "！！"]
MILD = ["有点", "有一点", "一点点", "稍微", "小小"]
TAG_TEXT = {"热": "热乎的", "冰": "冰的", "苦香": "咖啡味", "碳酸": "带气的", "顺滑": "顺口的"}
SITU_TEXT = {"单手": "单手能吃", "不掉渣": "不掉渣", "好分享": "好分享", "快吃完": "几口吃完"}
STRATEGIES = [
    ("follow", "顺着来", "顺着此刻的状态"),
    ("flip", "换个频道", "不按心情出牌，换种滋味"),
    ("minimal", "最省事", "少想、少动、少花"),
]


def split_clauses(mood):
    return [c for c in re.split(r"[，,。.!！?？~～、\s]+", mood) if c]


def parse_time(mood, now):
    m = re.search(r"(凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|半夜|夜里)?\s*(\d{1,2})\s*(?:点|:|：)", mood)
    if not m or int(m.group(2)) > 24:
        return None
    pre, h = m.group(1), int(m.group(2))
    if pre in ("下午", "傍晚", "晚上", "夜里") and h < 12:
        h += 12
    elif pre in ("凌晨", "半夜"):
        h %= 12
    elif not pre and h <= 12:
        # 没说上午下午：取离现在最近的那个解释（"加班到11点"在晚上说就是 23 点）
        h = min((h % 24, (h + 12) % 24), key=lambda x: min(abs(x - now.hour), 24 - abs(x - now.hour)))
    return h % 24, m.group(0)


def slot_of(h):
    for end, name in ((5, "凌晨"), (10, "早上"), (14, "中午"), (17, "下午"), (21, "晚上"), (24, "深夜")):
        if h < end:
            return name


def intensity(text):
    if any(w in text for w in MILD):
        return "轻微"
    if any(w in text for w in STRONG):
        return "很强"
    return None


def is_crisis(mood, slang):
    probe = mood
    for w in slang.get("crisis_exclude", []):
        probe = probe.replace(w, "")
    return any(w in probe for w in slang["crisis"])


def lex(mood, by_id, slang, now):
    """返回 (tokens, hits)。tokens 是给人看的逐句解读，hits 是 场景→命中词。"""
    t = parse_time(mood, now)
    hits, tokens = {}, []
    for c in split_clauses(mood):
        readings = {}
        for sid, words in slang["map"].items():
            ws = [w for w in words if w.lower() in c.lower()]
            ws = [w for w in ws if not any(w != o and w.lower() in o.lower() for o in ws)]
            if by_id[sid]["label"] in c:
                ws.append(by_id[sid]["label"])
            if ws:
                hits.setdefault(sid, set()).update(ws)
                readings.update(by_id[sid]["reading"])
        if t and t[1].strip() in c:
            readings = {"时间": f"{t[0]:02d}:00 {slot_of(t[0])}", **readings}
        lvl = intensity(c)
        if lvl:
            readings["强度"] = lvl
        if readings:
            tokens.append({"text": c, "readings": readings})
    return tokens, hits, t


# ---------- 语义分析：多个状态混合成一份"此刻需求" ----------

def blend(hits, by_id):
    if not hits:
        return [(by_id["light-snack"], 1.0)]
    top = sorted(hits, key=lambda k: -len(hits[k]))[:2]
    total = sum(len(hits[k]) for k in top)
    return [(by_id[k], len(hits[k]) / total) for k in top]


def make_need(mix, mood):
    want, avoid = {}, {}
    for s, w in mix:
        for t, v in s["want"].items():
            want[t] = want.get(t, 0) + v * w
        for t, v in s.get("avoid", {}).items():
            avoid[t] = avoid.get(t, 0) + v * w
    scenes = [s for s, _ in mix]
    heft = round(sum(s["heft"] * w for s, w in mix))
    lvl = intensity(mood)
    if lvl == "轻微":
        heft -= 1
    elif lvl == "很强" and "饿" in mood:
        heft += 1
    caps = [s["portion_cap"] for s in scenes if s.get("portion_cap")]
    return {
        "want": want, "avoid": avoid,
        "hard_avoid": {t: v for s in scenes for t, v in s.get("avoid", {}).items() if v <= -3},
        "flip": {t: v for t, v in mix[0][0].get("flip", {}).items()},
        "prefer_kind": sorted({k for s in scenes for k in s.get("prefer_kind", [])}),
        "situ": sorted({x for s in scenes for x in s.get("situ", [])}),
        "heft": max(1, min(3, heft)),
        "portion_cap": min(caps) if caps else None,
        "share": any(s.get("share") for s in scenes),
        "sort_by": next((s["sort_by"] for s in scenes if s.get("sort_by")), None),
    }


def describe_need(need):
    want = [TAG_TEXT.get(t, t) for t, v in sorted(need["want"].items(), key=lambda x: -x[1]) if v > 0][:4]
    want += [SITU_TEXT[x] for x in need["situ"]]
    avoid = [TAG_TEXT.get(t, t) for t, v in sorted(need["avoid"].items(), key=lambda x: x[1]) if v < 0][:3]
    return want, avoid


# ---------- 优化：按三种策略各出一套 ----------

def parse_nutrition(body):
    rows = {}
    for line in body["data"].split("\n")[1:]:
        p = [x.strip() for x in line.split(",")]
        if len(p) >= 9:
            rows[p[0]] = {"kcal": int(p[3]), "protein": float(p[4])}
    return rows


def candidates(menu_body, tags, nutri):
    menu = {v["name"]: (code, float(v["currentPrice"]))
            for code, v in menu_body["data"]["meals"].items()}
    out = []
    for it in tags["items"]:
        if it["name"] in menu:
            code, price = menu[it["name"]]
            n = nutri.get(it["nutrition"] or "", {})
            out.append(dict(it, code=code, price=price, kcal=n.get("kcal"), protein=n.get("protein")))
    return out


def tags_of(it):
    return set(it["texture"]) | set(it["taste"]) | {it["temp"]}


def strategy_need(need, strategy):
    if strategy == "follow":
        return dict(need, situ_w=1.0, price_w=0)
    if strategy == "flip":
        return dict(need, want=need["flip"], avoid=dict(need["hard_avoid"]), prefer_kind=[],
                    sort_by=None, situ_w=0.5, price_w=0)
    # minimal：口味权重减半，强调单手/快吃完，顺便省钱，只要两样
    return dict(need, want={t: v * 0.5 for t, v in need["want"].items()},
                situ=sorted(set(need["situ"]) | {"单手", "快吃完"}), situ_w=2.0,
                price_w=0.15, heft=1, share=False)


def item_score(it, need):
    tags = tags_of(it)
    s = sum(w for t, w in need["want"].items() if t in tags)
    s += sum(w for t, w in need["avoid"].items() if t in tags)
    s += sum(w for t, w in need["hard_avoid"].items() if t in tags)
    s += need["situ_w"] * len(set(it.get("situ", [])) & set(need["situ"]))
    s -= need["price_w"] * it["price"]
    if it["kind"] in need["prefer_kind"]:
        s += 1.5
    if need["sort_by"] == "protein" and it.get("protein"):
        s += it["protein"] / 10
    return s


def slots_for(heft):
    if heft == 1:
        return [MAIN_KINDS | SIDE_KINDS | {"dessert"}, DRINK_KINDS]
    if heft == 2:
        return [MAIN_KINDS, SIDE_KINDS | {"dessert"}, DRINK_KINDS]
    return [MAIN_KINDS, SIDE_KINDS, DRINK_KINDS, {"dessert"}]


def best_combo(cands, need, used, per_slot=6):
    pools = []
    for kinds in slots_for(need["heft"]):
        pool = sorted((c for c in cands if c["kind"] in kinds and c["heft"] <= need["heft"] + 1),
                      key=lambda c: (c["name"] in used, -item_score(c, need)))
        if pool:
            pools.append(pool[:per_slot])
    best = None
    for picks in itertools.product(*pools):
        names = [p["name"] for p in picks]
        if len(set(names)) < len(names):
            continue
        cap = need["portion_cap"]
        if cap and sum(p["heft"] for p in picks) > cap + len(picks) - 1:
            continue
        covered = set().union(*(tags_of(p) for p in picks))
        score = sum(item_score(p, need) for p in picks)
        score += sum(1 for t, v in need["want"].items() if v > 0 and t in covered)
        score -= 3 * len(set(names) & used)  # 三套之间尽量不重样
        if best is None or score > best[0]:
            best = (score, list(picks))
    return best


def why(it, need):
    tags = tags_of(it)
    return {"taste": [TAG_TEXT.get(t, t) for t, v in need["want"].items() if v > 0 and t in tags],
            "situ": [SITU_TEXT[x] for x in it.get("situ", []) if x in need["situ"]]}


def flavor_profile(picks):
    axes = ["咸香", "甜", "酸", "辣", "奶香", "焦香", "清爽", "浓郁"]
    return {a: round(sum(a in p["taste"] for p in picks) / len(picks), 2) for a in axes}


def price_combo(picks, qty, store, be_type, be_code):
    order_type = 2 if be_type in (2, 6) else 1
    args = {"storeCode": store, "beType": be_type, "orderType": order_type,
            "items": [{"productCode": p["code"], "quantity": qty} for p in picks]}
    if be_code:
        args["beCode"] = be_code
    d = mcp_call("calculate-price", args)["data"]
    return {k: d[k] / 100 for k in ("productOriginalPrice", "productPrice", "price", "discount", "deliveryPrice") if isinstance(d.get(k), (int, float))}


# ---------- 链接前检查：时段与营业时间 ----------

def check_warnings(mix, cands, hours, now):
    warns = []
    wants_breakfast = any("breakfast" in s.get("prefer_kind", []) for s, _ in mix)
    if wants_breakfast and not any(c["kind"] == "breakfast" for c in cands):
        warns.append(f"现在是{slot_of(now.hour)}，这家店不供应早餐，先用别的顶一顶")
    if hours:
        try:
            o, c = [datetime.strptime(x.strip(), "%H:%M").time() for x in hours.split("-")]
        except ValueError:
            return warns
        t = now.time()
        is_open = o <= t < c if o < c else (t >= o or t < c)
        if not is_open:
            warns.append(f"这家店已打烊（营业 {hours}），换一家 24 小时店或改外送")
        elif o < c:
            left = (datetime.combine(now.date(), c) - datetime.combine(now.date(), t)).seconds // 60
            if left <= 30:
                warns.append(f"这家店还有 {left} 分钟打烊，抓紧下单")
    return warns


# ---------- 输出 ----------

def text_card(res):
    lines = [f'$ taste-compiler --mood "{res["mood"]}"', ""]
    for i, tk in enumerate(res["lexer"]):
        head = "[词法分析]  " if i == 0 else " " * 12
        reading = " · ".join(f"{k}：{v}" for k, v in tk["readings"].items())
        lines.append(f'{head}"{tk["text"]}"  → {reading}')
    if not res["lexer"]:
        lines.append("[词法分析]  没认出特别的状态，按「随便吃点」处理")
    lines.append(f'[语义分析]  此刻想要：{"、".join(res["needs"]["want"]) or "随意"}')
    if res["needs"]["avoid"]:
        lines.append(f'            此刻不要：{"、".join(res["needs"]["avoid"])}')
    mix = " + ".join(f'{s["label"]} {round(s["weight"] * 100)}%' for s in res["scenes"])
    lines += [f"[链接]      {mix}", f"            {res['slot']}有 {len(res['plans'])} 种处理方式，选一个 ↓", ""]
    for p in res["plans"]:
        price = p.get("price", {}).get("price", p["estimate"])
        kcal = f'{p["kcal"]} kcal' if p["kcal"] is not None else "热量部分暂无"
        q = f"×{p['qty']} " if p["qty"] > 1 else ""
        names = " + ".join(q + it["name"] for it in p["items"])
        reasons = list(dict.fromkeys(r for it in p["items"] for r in it["why"]["situ"] + it["why"]["taste"]))[:4]
        lines += [f' ▸ --{p["strategy"]:<8} {p["label"]}：{p["hint"]}',
                  f"   {names}", f"   ¥{price:g} · {kcal} · 对上了：{'、'.join(reasons) or '—'}", ""]
    for w in res["warnings"]:
        lines.append(f"⚠ {w}")
    lines += [f'// {res["scene"]["quip"]}', "// 价格以下单页为准；仅供娱乐参考，不构成营养建议"]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="味觉编译器")
    ap.add_argument("--mood", required=True, help="用户原话（心情/场景/网络用语），或场景 id")
    ap.add_argument("--store", help="门店 storeCode（在线模式必填）")
    ap.add_argument("--hours", help="门店营业时间，如 07:00-22:00（来自 query-nearby-stores）")
    ap.add_argument("--be-type", type=int, default=1, help="1 到店 / 2 麦乐送 / 5 得来速")
    ap.add_argument("--be-code", help="得来速/外送必填")
    ap.add_argument("--menu", help="离线：query-meals 原始返回文件")
    ap.add_argument("--nutrition", help="离线：list-nutrition-foods 原始返回文件")
    ap.add_argument("--now", help="调试：指定当前时间，如 '2026-10-10 23:05'")
    ap.add_argument("--no-price", action="store_true", help="不调用 calculate-price")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    a = ap.parse_args()

    tags = json.loads((ROOT / "data/flavor-tags.json").read_text())
    scenes = json.loads((ROOT / "data/scenes.json").read_text())["scenes"]
    slang = json.loads((ROOT / "data/slang.json").read_text())
    by_id = {s["id"]: s for s in scenes}
    now = datetime.strptime(a.now, "%Y-%m-%d %H:%M") if a.now else datetime.now()

    if is_crisis(a.mood, slang):
        msg = {"crisis": True, "message": "听起来你现在很难受。食物帮不上这个忙，请找人聊聊：全国心理援助热线 12356，或北京心理危机研究与干预中心 010-82951332。"}
        print(json.dumps(msg, ensure_ascii=False) if a.format == "json" else msg["message"])
        sys.exit(CRISIS_EXIT)

    if a.mood in by_id:
        tokens, hits, t = [], {a.mood: {by_id[a.mood]["label"]}}, None
    else:
        tokens, hits, t = lex(a.mood, by_id, slang, now)
    mix = blend(hits, by_id)
    need = make_need(mix, a.mood)

    if not a.menu and not a.store:
        sys.exit("缺少门店：请先问用户在哪（城市+地标/商圈），用 query-nearby-stores 查到 storeCode 后重试")
    order_type = 2 if a.be_type in (2, 6) else 1
    menu_args = {"storeCode": a.store, "beType": a.be_type, "orderType": order_type}
    if a.be_code:
        menu_args["beCode"] = a.be_code
    menu = load_or_fetch(a.menu, "query-meals", menu_args)
    nutri = parse_nutrition(load_or_fetch(a.nutrition, "list-nutrition-foods"))
    cands = candidates(menu, tags, nutri)

    plans, used = [], set()
    for key, label, hint in STRATEGIES:
        sneed = strategy_need(need, key)
        found = best_combo(cands, sneed, used)
        if not found:
            continue
        score, picks = found
        used |= {p["name"] for p in picks}
        qty = 2 if sneed["share"] else 1
        kcals = [p["kcal"] for p in picks]
        plan = {"strategy": key, "label": label, "hint": hint, "score": round(score, 2), "qty": qty,
                "items": [dict(p, why=why(p, sneed)) for p in picks],
                "estimate": round(sum(p["price"] for p in picks) * qty, 1),
                "kcal": sum(kcals) * qty if None not in kcals else None,
                "profile": flavor_profile(picks)}
        if a.store and not a.no_price:
            plan["price"] = price_combo(picks, qty, a.store, a.be_type, a.be_code)
        plans.append(plan)
    if not plans:
        sys.exit("这家店当前时段没有能匹配的单品，换个门店或场景试试")

    hour = t[0] if t else now.hour
    want, avoid = describe_need(need)
    res = {
        "mood": a.mood, "now": now.strftime("%Y-%m-%d %H:%M"), "slot": "此刻" if not t else slot_of(hour),
        "lexer": tokens,
        "scenes": [{"id": s["id"], "label": s["label"], "weight": round(w, 2)} for s, w in mix],
        "needs": {"want": want, "avoid": avoid},
        "plans": plans,
        "warnings": check_warnings(mix, cands, a.hours, now),
        # 兼容 card.py 旧字段
        "scene": mix[0][0], "matched_by": "、".join(sorted(hits.get(mix[0][0]["id"], []))) or "default",
        "results": plans,
    }
    if a.format == "json":
        keep = ("name", "code", "price", "kcal", "protein", "texture", "taste", "temp", "situ", "why")
        for p in plans:
            p["items"] = [{k: it[k] for k in keep if k in it} for it in p["items"]]
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(text_card(res))


if __name__ == "__main__":
    main()
