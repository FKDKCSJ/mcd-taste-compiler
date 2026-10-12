#!/usr/bin/env python3
"""味觉编译卡片：把 compile.py --format json 的输出渲染成竖版 9:16 的 HTML 分享卡片。

版式（自上而下）：
  1. 用户原话（大字，主角）
  2. 词法分析：逐句解读，像编译日志
  3. 语义分析：场景混合 + 想要/不要 + 黄色警告条
  4. 三种策略：--follow 顺着来 / --flip 换个频道 / --minimal 最省事
  5. 场景梗 + 免责小字

用法：
  python3 scripts/compile.py --mood "加班到11点，牛马麻了" --store 1950564 --format json > out/result.json
  python3 scripts/card.py out/result.json --mood "加班到11点，牛马麻了" -o out/card.html --open

只用标准库：手写 HTML + 内联 SVG 风味雷达图，不引入任何第三方依赖。
"""
import argparse
import html
import json
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AXES = ["咸香", "甜", "酸", "辣", "奶香", "焦香", "清爽", "浓郁"]
RED, YELLOW = "#DA291C", "#FFC72C"
CARD_W, CARD_H = 540, 960  # 9:16

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#070708;display:flex;justify-content:center;align-items:flex-start;
  min-height:100vh;padding:24px 0;font-family:"SF Mono",Menlo,Consolas,"DejaVu Sans Mono",
  "PingFang SC","Hiragino Sans GB","Microsoft YaHei",monospace}
body.shot{padding:0;background:#0E0F12}
body.shot .card{border-radius:0;border:none;box-shadow:none}
.card{width:540px;height:960px;background:#0E0F12;border:1px solid #26282E;border-radius:14px;
  overflow:hidden;display:flex;flex-direction:column;box-shadow:0 18px 60px rgba(0,0,0,.6)}
.bar{height:30px;flex:0 0 30px;background:#16181D;border-bottom:1px solid #26282E;
  display:flex;align-items:center;gap:6px;padding:0 12px}
.dot{width:10px;height:10px;border-radius:50%}
.dot.r{background:#DA291C}.dot.y{background:#FFC72C}.dot.g{background:#4E5157}
.bar-t{margin-left:8px;color:#6E737D;font-size:10.5px;letter-spacing:.4px}
.term{flex:1;padding:14px 18px 6px;display:flex;flex-direction:column;gap:7px;overflow:hidden}

/* 1. 用户原话 */
.quote{flex:0 0 auto;position:relative;padding:9px 12px 9px 14px;background:#15171C;border:1px solid #2A2D33;
  border-left:4px solid #FFC72C;border-radius:8px}
.quote .cmd{font-size:10.5px;color:#6E737D;margin-bottom:4px}
.quote .cmd b{color:#FFC72C}
.quote .words{font-size:26px;line-height:1.24;font-weight:700;color:#F3F5F8;letter-spacing:.5px;
  word-break:break-word}
.quote .words i{font-style:normal;color:#FFC72C}

/* 2/3. 编译日志 */
.log{flex:0 0 auto;font-size:11px;color:#C9CDD4;line-height:1.5}
.log .k{color:#7E848E}
.log .v{color:#E4E6EA}
.log .sect{color:#FFC72C;font-weight:700}
.log .dim{color:#6E737D}
.log .ok{color:#3FB950}
.mix{color:#8FD3A0}
.warn{flex:0 0 auto;background:#3A2B06;border:1px solid #7A5A0B;border-left:3px solid #FFC72C;
  border-radius:6px;padding:6px 10px;font-size:10.5px;color:#FFD666;line-height:1.45}
.hr{flex:0 0 auto;color:#5A5E66;font-size:9.5px;letter-spacing:3px;text-align:center;margin:2px 0}

/* 4. 三种策略 */
.plan{flex:1 1 0;display:flex;flex-direction:column;justify-content:center;
  border:1px solid #26282E;border-left:3px solid #DA291C;border-radius:8px;
  background:#131418;padding:10px 14px}
.plan.follow{border-left-color:#DA291C}
.plan.flip{border-left-color:#FFC72C}
.plan.minimal{border-left-color:#4E5157}
.p-top{display:flex;gap:12px;align-items:center}
.p-main{flex:1;min-width:0}
.p-head{display:flex;align-items:baseline;gap:8px}
.p-cmd{font-size:12.5px;font-weight:700;color:#FFC72C;letter-spacing:.2px}
.p-name{font-size:16px;font-weight:700;color:#F3F5F8}
.p-price{margin-left:auto;font-size:18px;color:#FF3B30;font-weight:700}
.p-kcal{font-size:10px;color:#8A8F98}
.p-hint{font-size:10px;color:#7E848E;margin-top:5px}
.p-items{font-size:12px;color:#E4E6EA;line-height:1.55;margin-top:9px}
.p-items .q{color:#FFC72C;font-size:10px;margin-right:2px}
.p-items .sep{color:#4E5157;margin:0 5px}
.p-items .t{color:#7E848E;font-size:9.5px;margin-left:3px}
.why{font-size:10.5px;color:#8FD3A0;line-height:1.5;margin-top:8px}
.why b{color:#5F6773;font-weight:400}
.radar{flex:0 0 78px;height:78px}
.foot{flex:0 0 auto;padding:8px 18px 12px;border-top:1px dashed #26282E;
  color:#6E737D;font-size:9.5px;line-height:1.55}
.foot b{color:#DA291C;font-weight:700}
"""


def radar_svg(profile, size=78):
    """8 轴风味雷达图，纯手写 SVG。profile: {轴名: 0~1}"""
    cx = cy = size / 2
    r = size / 2 - 13
    n = len(AXES)

    def pt(i, ratio):
        a = -math.pi / 2 + i * 2 * math.pi / n
        return cx + r * ratio * math.cos(a), cy + r * ratio * math.sin(a)

    def ring(ratio):
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(i, ratio) for i in range(n)))

    out = [f'<svg class="radar" viewBox="-16 -2 110 82" xmlns="http://www.w3.org/2000/svg">']
    for ratio in (0.5, 1.0):
        out.append(f'<polygon points="{ring(ratio)}" fill="none" stroke="#33363D" '
                   f'stroke-width="{1 if ratio == 1 else 0.6}"/>')
    for i in range(n):
        x, y = pt(i, 1.0)
        out.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="#2A2D33" stroke-width="0.6"/>')
    vals = [(profile.get(a, 0) or 0) for a in AXES]
    if any(vals):
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(i, max(v, 0.08)) for i, v in enumerate(vals)))
        out.append(f'<polygon points="{pts}" fill="{RED}" fill-opacity="0.45" stroke="{RED}" stroke-width="1.5"/>')
    for i, a in enumerate(AXES):
        rad = -math.pi / 2 + i * 2 * math.pi / n
        lx, ly = cx + (r + 6) * math.cos(rad), cy + (r + 6) * math.sin(rad)
        color = YELLOW if vals[i] >= 0.5 else "#9AA0A8"
        if math.cos(rad) > 0.4:
            anchor, lx = "start", cx + r + 4
        elif math.cos(rad) < -0.4:
            anchor, lx = "end", cx - r - 4
        else:
            anchor = "middle"
        out.append(f'<text x="{lx:.1f}" y="{ly + 2.4:.1f}" font-size="7.6" text-anchor="{anchor}" '
                   f'fill="{color}" font-family="inherit">{a}</text>')
    out.append("</svg>")
    return "".join(out)


def esc(s):
    return html.escape(str(s), quote=True)


def reasons(items, limit=4):
    """从单品 why 里汇总「对上了」的理由，去重、保序。"""
    out = []
    for it in items:
        w = it.get("why") or {}
        for r in list(w.get("situ", [])) + list(w.get("taste", [])):
            if r not in out:
                out.append(r)
    return out[:limit]


def plan_block(p, show_radar=False):
    price = (p.get("price") or {}).get("price")
    price = p.get("estimate") if price is None else price
    kcal = p.get("kcal")
    kcal_txt = f"{int(round(kcal))} kcal" if isinstance(kcal, (int, float)) else "部分暂无"
    q = p.get("qty", 1)
    qty = f'<span class="q">{q}×</span>' if q > 1 else ""
    items = []
    for it in p["items"]:
        tags = "/".join(list(it.get("taste", []))[:2])
        q_ = f'<span class="q">{q}×</span>' if q > 1 else ""
        items.append(f'{q_}{esc(it["name"])}' + (f'<span class="t">{esc(tags)}</span>' if tags else ""))
    rs = reasons(p["items"])
    why = "、".join(esc(r) for r in rs) if rs else "—"
    radar = radar_svg(p["profile"]) if show_radar else ""
    return (
        f'<div class="plan {esc(p["strategy"])}"><div class="p-top">'
        f'<div class="p-main">'
        f'<div class="p-head"><span class="p-cmd">--{esc(p["strategy"])}</span>'
        f'<span class="p-name">{esc(p["label"])}</span>'
        f'<span class="p-price">¥{price:g}</span>'
        f'<span class="p-kcal">{kcal_txt}</span></div>'
        f'<div class="p-hint">{esc(p["hint"])}</div>'
        f'<div class="p-items">{qty}{"<span class=\"sep\">·</span>".join(items)}</div>'
        f'<div class="why"><b>对上了：</b>{why}</div>'
        f'</div>{radar}</div></div>')


def render(data, mood=None, shot=False):
    plans = data.get("plans") or data["results"]
    scene = data["scene"]
    quote = mood or data.get("mood") or scene["label"]
    lex = data.get("lexer") or []
    mix = data.get("scenes") or [{"label": scene["label"], "weight": 1.0}]
    needs = data.get("needs") or {"want": [], "avoid": []}
    warns = data.get("warnings") or []

    # 1. 原话（主角）
    head = (f'<div class="quote"><div class="cmd">$ taste-compiler --mood <b>"{esc(quote)}"</b></div>'
            f'<div class="words"><i>“</i>{esc(quote)}<i>”</i></div></div>')

    # 2. 词法分析
    lines = []
    for i, tk in enumerate(lex):
        lead = '<span class="sect">[词法分析]</span> ' if i == 0 else " " * 12
        rd = " · ".join(f'<span class="k">{esc(k)}</span> <span class="v">{esc(v)}</span>'
                        for k, v in tk["readings"].items())
        lines.append(f'<div class="log">{lead}"{esc(tk["text"])}" → {rd}</div>')
    if not lex:
        lines.append('<div class="log"><span class="sect">[词法分析]</span> '
                     '没认出特别的状态，按「随便吃点」处理</div>')

    # 3. 语义分析
    mix_txt = " + ".join(f'{s["label"]} {round(s["weight"] * 100)}%' for s in mix)
    lines.append(f'<div class="log"><span class="sect">[语义分析]</span> '
                 f'<span class="k">场景混合</span> <span class="mix">{esc(mix_txt)}</span></div>')
    lines.append(f'<div class="log">{" " * 12}<span class="k">此刻想要</span> '
                 f'<span class="v">{esc("、".join(needs.get("want") or []) or "随意")}</span></div>')
    if needs.get("avoid"):
        lines.append(f'<div class="log">{" " * 12}<span class="k">此刻不要</span> '
                     f'<span class="v">{esc("、".join(needs["avoid"]))}</span></div>')
    for w in warns:
        lines.append(f'<div class="warn">⚠ {esc(w)}</div>')

    # 4. 三种策略
    blocks = [plan_block(p, show_radar=(p["strategy"] == "follow")) for p in plans]

    # 5. 收尾
    tail = '<div class="hr">—— 三种吃法，选一个 ——</div>'
    body = (head + "".join(lines) + tail + "".join(blocks)
            + f'<div class="log dim">// {esc(scene["quip"])}</div>')

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width={CARD_W}, initial-scale=1">
<title>味觉编译卡片 · {esc(scene["label"])}</title>
<style>{CSS}</style>
</head>
<body class="{'shot' if shot else 'page'}">
<div class="card">
  <div class="bar"><span class="dot r"></span><span class="dot y"></span><span class="dot g"></span>
    <span class="bar-t">taste-compiler — 编译输出 · {esc(scene["id"])}</span></div>
  <div class="term">{body}</div>
  <div class="foot"><b>仅供娱乐参考，不构成营养建议；非麦当劳官方产品。</b><br>
    价格以下单页为准 · 风味标签为人工标注 · mcd-taste-compiler 生成</div>
</div>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description="味觉编译卡片（9:16 HTML）")
    ap.add_argument("input", nargs="?", help="compile.py --format json 的输出文件，缺省读 stdin")
    ap.add_argument("--mood", help="用户原话，显示在卡片最上方")
    ap.add_argument("-o", "--out", default="out/card.html")
    ap.add_argument("--open", action="store_true", help="生成后用系统默认浏览器打开")
    ap.add_argument("--shot", action="store_true", help="截图模式：去掉页面留白，卡片正好铺满 9:16（配 headless Chrome 截图用）")
    a = ap.parse_args()

    raw = Path(a.input).read_text() if a.input else sys.stdin.read()
    out = Path(a.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(json.loads(raw), a.mood, a.shot))
    print(f"已生成 {out}（{CARD_W}×{CARD_H}，9:16）")
    if a.open:
        opener = "open" if sys.platform == "darwin" else ("start" if sys.platform.startswith("win") else "xdg-open")
        subprocess.run([opener, str(out)], check=False)


if __name__ == "__main__":
    main()
