#!/usr/bin/env python3
"""味觉编译卡片：把 compile.py --format json 的输出渲染成竖版 9:16 的 HTML 分享卡片。

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
.term{flex:1;padding:16px 18px 8px;display:flex;flex-direction:column;gap:9px;overflow:hidden}
.log{font-size:11.5px;color:#C9CDD4;line-height:1.55}
.prompt{color:#FFC72C;font-weight:700}
.str{color:#8FD3A0}
.ok{color:#3FB950}
.dim{color:#6E737D}
.hr{color:#3A3D44;font-size:10px;letter-spacing:2px;margin:2px 0 0}
.scheme{flex:1 1 0;display:flex;flex-direction:column;border:1px solid #26282E;border-left:3px solid #DA291C;
  border-radius:8px;background:#131418;padding:9px 11px}
.s-head{display:flex;align-items:baseline;gap:9px;margin-bottom:7px}
.tag{font-size:10.5px;color:#0E0F12;background:#FFC72C;border-radius:3px;padding:1.5px 6px;font-weight:700}
.price{font-size:17px;color:#FF3B30;font-weight:700;letter-spacing:.3px}
.kcal{font-size:11px;color:#8A8F98}
.s-body{display:flex;gap:10px;align-items:center;flex:1}
.items{flex:1;display:flex;flex-direction:column;gap:5px;min-width:0}
.item{font-size:11.5px;color:#E4E6EA;line-height:1.35;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.item em{font-style:normal;color:#7E848E;font-size:10px;margin-left:5px}
.q{color:#FFC72C;font-size:10.5px;margin-right:4px}
.radar{flex:0 0 118px;height:118px}
.foot{flex:0 0 auto;padding:10px 18px 14px;border-top:1px dashed #26282E;
  color:#6E737D;font-size:9.5px;line-height:1.6}
.foot b{color:#DA291C;font-weight:700}
"""


def radar_svg(profile, size=118):
    """8 轴风味雷达图，纯手写 SVG。profile: {轴名: 0~1}"""
    cx = cy = size / 2
    r = size / 2 - 16
    n = len(AXES)

    def pt(i, ratio):
        a = -math.pi / 2 + i * 2 * math.pi / n
        return cx + r * ratio * math.cos(a), cy + r * ratio * math.sin(a)

    def ring(ratio):
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(i, ratio) for i in range(n)))

    out = [f'<svg class="radar" viewBox="-13 -2 144 122" xmlns="http://www.w3.org/2000/svg">']
    for ratio in (0.25, 0.5, 0.75, 1.0):
        out.append(f'<polygon points="{ring(ratio)}" fill="none" stroke="#33363D" '
                   f'stroke-width="{1 if ratio == 1 else 0.6}"/>')
    for i in range(n):
        x, y = pt(i, 1.0)
        out.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="#2A2D33" stroke-width="0.6"/>')
    vals = [(profile.get(a, 0) or 0) for a in AXES]
    if any(vals):
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(i, max(v, 0.06)) for i, v in enumerate(vals)))
        out.append(f'<polygon points="{pts}" fill="{RED}" fill-opacity="0.45" stroke="{RED}" stroke-width="1.6"/>')
        for i, v in enumerate(vals):
            if v > 0:
                x, y = pt(i, v)
                out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.7" fill="{YELLOW}"/>')
    for i, a in enumerate(AXES):
        rad = -math.pi / 2 + i * 2 * math.pi / n
        lx, ly = cx + (r + 7) * math.cos(rad), cy + (r + 7) * math.sin(rad)
        v = vals[i]
        color = YELLOW if v >= 0.6 else "#9AA0A8"
        if math.cos(rad) > 0.4:      # 右侧：左对齐，避免出界
            anchor, lx = "start", cx + r + 5
        elif math.cos(rad) < -0.4:   # 左侧：右对齐
            anchor, lx = "end", cx - r - 5
        else:                        # 上/下：居中
            anchor = "middle"
        out.append(f'<text x="{lx:.1f}" y="{ly + 2.6:.1f}" font-size="8.4" text-anchor="{anchor}" '
                   f'fill="{color}" font-family="inherit">{a}</text>')
    out.append("</svg>")
    return "".join(out)


def esc(s):
    return html.escape(str(s), quote=True)


def render(data, mood=None, shot=False):
    scene, why, results = data["scene"], data.get("matched_by", ""), data["results"]
    logs = [f'<div class="log"><span class="prompt">$</span> taste-compiler --mood '
            f'<span class="str">"{esc(mood or scene["label"])}"</span></div>',
            f'<div class="log ok">✔ 识别场景：{esc(scene["label"])}（依据：{esc(why)}）</div>',
            f'<div class="log dim">// 候选池 = 本店当前时段在售单品 ∩ 风味标签表 · 共 {len(results)} 套方案</div>',
            '<div class="hr">—— 编译输出 ————————————————</div>']

    blocks = []
    for i, r in enumerate(results, 1):
        price = (r.get("price") or {}).get("price")
        price = r.get("estimate") if price is None else price
        kcal = r["kcal"]
        kcal_txt = f'{int(round(kcal))} kcal' if isinstance(kcal, (int, float)) else "热量：部分暂无"
        q = r.get("qty", 1)
        qty_html = '<span class="q">×%d</span>' % q if q > 1 else ""
        items = "".join(
            '<div class="item">%s%s<em>%s·%s</em></div>' % (
                qty_html, esc(p["name"]),
                esc("/".join(p["texture"] + p["taste"])), esc(p["temp"]))
            for p in r["items"])
        blocks.append(
            f'<div class="scheme"><div class="s-head"><span class="tag">方案 {i}</span>'
            f'<span class="price">¥{price:g}</span><span class="kcal">{kcal_txt}</span></div>'
            f'<div class="s-body"><div class="items">{items}</div>'
            f'{radar_svg(r["profile"])}</div></div>')

    body = "".join(logs) + "".join(blocks)
    body += (f'<div class="log dim">// {esc(scene["quip"])}</div>'
             f'<div class="log dim">// 风味雷达：{esc("、".join(AXES))}（8 轴，越靠外越强）</div>')

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
    ap.add_argument("--mood", help="用户原话，显示在终端提示行里")
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
