#!/usr/bin/env python3
"""味觉编译器：心情/场景 → 门店真实可点的风味组合。

只调用麦当劳 MCP 的只读工具（query-meals、list-nutrition-foods、calculate-price），
永远不下单。下单交给上层 Agent，在用户明确确认后调用 create-order。

用法：
  MCD_MCP_TOKEN=xxx python3 compile.py --mood "加班到十一点 emo了" --store 1950564
  python3 compile.py --mood "热死了" --menu menu.json --nutrition nutri.json   # 离线
"""
import argparse
import itertools
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
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


# ---------- 场景识别 ----------

def resolve_scene(mood, scenes, slang):
    if any(w in mood for w in slang["crisis"]):
        return None, "crisis"
    by_id = {s["id"]: s for s in scenes}
    if mood in by_id:
        return by_id[mood], mood
    hits = {}
    for sid, words in slang["map"].items():
        for w in words:
            if w.lower() in mood.lower():
                hits.setdefault(sid, []).append(w)
    for s in scenes:
        if s["label"] in mood and s["label"] not in hits.get(s["id"], []):
            hits.setdefault(s["id"], []).append(s["label"])
    if not hits:
        return by_id["light-snack"], "default"
    sid = max(hits, key=lambda k: len(hits[k]))
    return by_id[sid], "、".join(hits[sid])


# ---------- 打分 ----------

def parse_nutrition(body):
    rows = {}
    for line in body["data"].split("\n")[1:]:
        p = [x.strip() for x in line.split(",")]
        if len(p) >= 9:
            rows[p[0]] = {"kcal": int(p[3]), "protein": float(p[4])}
    return rows


def item_score(it, scene):
    tags = set(it["texture"]) | set(it["taste"]) | {it["temp"]}
    s = sum(w for t, w in scene["want"].items() if t in tags)
    s += sum(w for t, w in scene.get("avoid", {}).items() if t in tags)
    if it["kind"] in scene.get("prefer_kind", []):
        s += 1.5
    if scene.get("sort_by") == "protein" and it.get("protein"):
        s += it["protein"] / 10
    return s


def candidates(menu_body, tags, nutri, scene):
    menu = {v["name"]: (code, float(v["currentPrice"]))
            for code, v in menu_body["data"]["meals"].items()}
    out = []
    for it in tags["items"]:
        if it["name"] not in menu:
            continue
        code, price = menu[it["name"]]
        n = nutri.get(it["nutrition"] or "", {})
        c = dict(it, code=code, price=price, kcal=n.get("kcal"), protein=n.get("protein"))
        c["score"] = item_score(c, scene)
        out.append(c)
    return out


def slots_for(scene):
    heft = scene["heft"]
    if heft == 1:
        return [SIDE_KINDS | {"dessert"}, DRINK_KINDS]
    if heft == 2:
        return [MAIN_KINDS, SIDE_KINDS | {"dessert"}, DRINK_KINDS]
    return [MAIN_KINDS, SIDE_KINDS, DRINK_KINDS, {"dessert", "chicken"}]


def build_combos(cands, scene, top=3, per_slot=6):
    pools = []
    for kinds in slots_for(scene):
        pool = sorted((c for c in cands if c["kind"] in kinds), key=lambda c: -c["score"])
        if pool:
            pools.append(pool[:per_slot])
    cap = scene.get("portion_cap")
    combos = []
    for picks in itertools.product(*pools):
        if len({p["name"] for p in picks}) < len(picks):
            continue
        if cap and sum(p["heft"] for p in picks) > cap + len(picks) - 1:
            continue
        tags = set().union(*(set(p["texture"]) | set(p["taste"]) | {p["temp"]} for p in picks))
        coverage = sum(1 for t in scene["want"] if t in tags)
        combos.append((sum(p["score"] for p in picks) + coverage, picks))
    combos.sort(key=lambda x: -x[0])
    # 主力单品不重复，给用户真正不同的选择
    chosen, seen = [], set()
    for score, picks in combos:
        if picks[0]["name"] in seen:
            continue
        seen.add(picks[0]["name"])
        chosen.append((score, picks))
        if len(chosen) == top:
            break
    return chosen


def flavor_profile(picks):
    axes = ["咸香", "甜", "酸", "辣", "奶香", "焦香", "清爽", "浓郁"]
    n = len(picks)
    return {a: round(sum(a in p["taste"] for p in picks) / n, 2) for a in axes}


# ---------- 算价 ----------

def price_combo(picks, qty, store, be_type, be_code):
    order_type = 2 if be_type in (2, 6) else 1
    args = {"storeCode": store, "beType": be_type, "orderType": order_type,
            "items": [{"productCode": p["code"], "quantity": qty} for p in picks]}
    if be_code:
        args["beCode"] = be_code
    d = mcp_call("calculate-price", args)["data"]
    return {k: d[k] / 100 for k in ("productOriginalPrice", "productPrice", "price", "discount", "deliveryPrice") if isinstance(d.get(k), (int, float))}


# ---------- 输出 ----------

def text_card(scene, why, results):
    lines = [f"$ taste-compiler --scene {scene['id']}",
             f"✔ 识别场景：{scene['label']}（依据：{why}）", ""]
    for i, r in enumerate(results, 1):
        price = r.get("price", {}).get("price", r["estimate"])
        kcal = r["kcal"] if r["kcal"] is not None else "部分暂无"
        lines.append(f"[方案 {i}]  ¥{price:g}  ·  {kcal} kcal")
        for p in r["items"]:
            q = f"×{r['qty']} " if r["qty"] > 1 else ""
            lines.append(f"  {q}{p['name']}  {'/'.join(p['texture'] + p['taste'])}·{p['temp']}")
        bar = "  ".join(f"{k}{'█' * round(v * 4)}" for k, v in r["profile"].items() if v)
        lines += [f"  风味：{bar}", ""]
    lines.append(f"// {scene['quip']}")
    lines.append("// 价格以下单页为准；仅供娱乐参考，不构成营养建议")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="味觉编译器")
    ap.add_argument("--mood", required=True, help="心情/场景描述，或场景 id")
    ap.add_argument("--store", help="门店 storeCode（在线模式必填）")
    ap.add_argument("--be-type", type=int, default=1, help="1 到店 / 2 麦乐送 / 5 得来速")
    ap.add_argument("--be-code", help="得来速/外送必填")
    ap.add_argument("--menu", help="离线：query-meals 原始返回文件")
    ap.add_argument("--nutrition", help="离线：list-nutrition-foods 原始返回文件")
    ap.add_argument("--no-price", action="store_true", help="不调用 calculate-price")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--format", choices=["text", "json"], default="text")
    a = ap.parse_args()

    tags = json.loads((ROOT / "data/flavor-tags.json").read_text())
    scenes = json.loads((ROOT / "data/scenes.json").read_text())["scenes"]
    slang = json.loads((ROOT / "data/slang.json").read_text())

    scene, why = resolve_scene(a.mood, scenes, slang)
    if scene is None:
        msg = {"crisis": True, "message": "听起来你现在很难受。食物帮不上这个忙，请找人聊聊：全国心理援助热线 12356，或北京心理危机研究与干预中心 010-82951332。"}
        print(json.dumps(msg, ensure_ascii=False) if a.format == "json" else msg["message"])
        sys.exit(CRISIS_EXIT)

    if not a.menu and not a.store:
        sys.exit("在线模式需要 --store（用 query-nearby-stores 查 storeCode）")
    order_type = 2 if a.be_type in (2, 6) else 1
    menu_args = {"storeCode": a.store, "beType": a.be_type, "orderType": order_type}
    if a.be_code:
        menu_args["beCode"] = a.be_code
    menu = load_or_fetch(a.menu, "query-meals", menu_args)
    nutri = parse_nutrition(load_or_fetch(a.nutrition, "list-nutrition-foods"))

    cands = candidates(menu, tags, nutri, scene)
    qty = 2 if scene.get("share") else 1
    results = []
    for score, picks in build_combos(cands, scene, a.top):
        kcals = [p["kcal"] for p in picks]
        r = {"score": round(score, 2), "qty": qty, "items": picks,
             "estimate": round(sum(p["price"] for p in picks) * qty, 1),
             "kcal": sum(kcals) * qty if None not in kcals else None,
             "profile": flavor_profile(picks)}
        if a.store and not a.no_price:
            r["price"] = price_combo(picks, qty, a.store, a.be_type, a.be_code)
        results.append(r)

    if not results:
        sys.exit("这家店当前时段没有能匹配的单品，换个门店或场景试试")
    if a.format == "json":
        slim = [{**r, "items": [{k: p[k] for k in ("name", "code", "price", "kcal", "protein", "texture", "taste", "temp")} for p in r["items"]]} for r in results]
        print(json.dumps({"scene": scene, "matched_by": why, "results": slim}, ensure_ascii=False, indent=1))
    else:
        print(text_card(scene, why, results))


if __name__ == "__main__":
    main()
