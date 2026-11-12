r"""Coverage chart for the README (docs/images/coverage-light.svg / -dark.svg). Update ROWS and rerun:
    python docs\make_coverage_chart.py

Rows: share of each main series' CAFD ids that have BMW untrimmed names."""
import os
from xml.sax.saxutils import escape

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images")
# main series, cars, ids with BMW names, CAFD ids in psdzdata
ROWS = [
    ("G029", "Z4", 57, 58),
    ("G042", "2 Series Coupé, M2", 53, 54),
    ("G020", "3/4 Series, i4, M3/M4", 89, 93),
    ("J029", "Supra", 48, 51),
    ("G015", "8 Series", 70, 79),
    ("G001", "X3, X4", 78, 94),
    ("G008", "iX3", 67, 82),
    ("G005", "X5, X6, X5 M", 78, 98),
    ("G007", "X7", 71, 92),
    ("G030", "5/6 Series, M5", 73, 99),
    ("G038", "5 Series long (China)", 62, 84),
    ("G011", "7 Series (G11/G12)", 73, 108),
    ("G060", "5 Series (G60)", 4, 7),
    ("G070", "7 Series (G70)", 3, 7),
    ("U006", "2 Series Active Tourer (U06)", 3, 8),
]

THEMES = {
    "light": dict(surface="#fcfcfb", ink="#0b0b0b", ink2="#52514e", muted="#898781", grid="#e1e0d9",
                  axis="#c3c2b7", bar="#2a78d6", border="#e1e0d9"),
    "dark": dict(surface="#1a1a19", ink="#f0efec", ink2="#c3c2b7", muted="#898781", grid="#2c2c2a",
                 axis="#383835", bar="#3987e5", border="#2c2c2a"),
}

W, PAD = 760, 24
LABEL_W = 250          # series code + cars
BAR_X = PAD + LABEL_W
BAR_W = 360            # 100 %
ROW_H, BAR_H = 26, 14
TOP = 84
FONT = "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"


def bar_path(x, y, w, h, r=4):
    """Bar anchored square at the baseline (left), rounded at the data end (right)."""
    r = min(r, w / 2, h / 2)
    return (f"M{x},{y} H{x + w - r} Q{x + w},{y} {x + w},{y + r} V{y + h - r} "
            f"Q{x + w},{y + h} {x + w - r},{y + h} H{x} Z")


def svg(t):
    c = THEMES[t]
    H = TOP + len(ROWS) * ROW_H + 74
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
         f'font-family="{FONT}" role="img" aria-labelledby="t d">',
         '<title id="t">BMW names by main series</title>',
         '<desc id="d">Share of each main series\' CAFD ids that have BMW untrimmed names in OpenLauncher. '
         + escape("; ".join(f"{s} {cars}: {n} of {tot}" for s, cars, n, tot in ROWS)) + '</desc>',
         f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="10" fill="{c["surface"]}" stroke="{c["border"]}"/>',
         f'<text x="{PAD}" y="34" font-size="17" font-weight="600" fill="{c["ink"]}">BMW names by main series</text>',
         f'<text x="{PAD}" y="56" font-size="13" fill="{c["ink2"]}">Share of each series\' CAFDs that come with BMW\'s '
         f'own group, function and value names</text>']
    # gridlines + axis labels at 0/25/50/75/100 %
    y0, y1 = TOP - 6, TOP + len(ROWS) * ROW_H - 4
    for p in (0, 25, 50, 75, 100):
        x = BAR_X + BAR_W * p / 100
        o.append(f'<line x1="{x}" y1="{y0}" x2="{x}" y2="{y1}" stroke="{c["axis"] if p == 0 else c["grid"]}" stroke-width="1"/>')
        o.append(f'<text x="{x}" y="{y1 + 16}" font-size="11" fill="{c["muted"]}" text-anchor="middle">{p}%</text>')
    for i, (s, cars, n, tot) in enumerate(ROWS):
        y = TOP + i * ROW_H
        cy = y + BAR_H / 2 + 4.5
        o.append(f'<text x="{PAD}" y="{cy}" font-size="13" font-weight="600" fill="{c["ink"]}">{s}</text>')
        o.append(f'<text x="{PAD + 46}" y="{cy}" font-size="13" fill="{c["ink2"]}">{escape(cars)}</text>')
        w = BAR_W * n / tot
        o.append(f'<path d="{bar_path(BAR_X + 1, y, w - 1, BAR_H)}" fill="{c["bar"]}"><title>{s} {escape(cars)}: '
                 f'{n} of {tot} CAFD ids</title></path>')
        o.append(f'<text x="{BAR_X + w + 8}" y="{cy}" font-size="12" fill="{c["ink"]}">{round(100 * n / tot)}%'
                 f'<tspan fill="{c["muted"]}">  {n} of {tot}</tspan></text>')
    fy = y1 + 38
    o.append(f'<text x="{PAD}" y="{fy}" font-size="11.5" fill="{c["ink2"]}">On the CAFD versions these series share '
             f'with the G20, about 89% of functions are named.</text>')
    o.append(f'<text x="{PAD}" y="{fy + 16}" font-size="11.5" fill="{c["ink2"]}">G60, G70 and U06 list only 7-8 CAFD ids '
             f'in psdzdata, so each id moves their share a lot. Data: OpenLauncher v2.1.0.</text>')
    o.append('</svg>')
    return "\n".join(o) + "\n"


os.makedirs(OUT, exist_ok=True)
for t in THEMES:
    with open(os.path.join(OUT, f"coverage-{t}.svg"), "w", encoding="utf-8", newline="\n") as f:
        f.write(svg(t))
print("ok")
