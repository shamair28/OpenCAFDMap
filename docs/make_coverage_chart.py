r"""Coverage chart for the README (docs/images/coverage-light.svg / -dark.svg). Update GROUPS and rerun:
    python docs\make_coverage_chart.py

Per chassis family: a summary bar (unique CAFD ids across the family), then one bar per main series.
A CAFD id counts as covered when the package has BMW untrimmed names for it."""
import os
from xml.sax.saxutils import escape

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images")
DATA = "definitions 2026.11.13.5"
# (family, ids with BMW names, unique CAFD ids, [(main series, cars, ids with names, CAFD ids), ...])
GROUPS = [
    ("G-chassis", 96, 143, [
        ("G029", "Z4", 57, 58),
        ("G042", "2 Series Coupé, M2", 53, 54),
        ("G020", "3/4 Series, i4, M3/M4", 89, 93),
        ("J029", "Supra", 48, 51),
        ("G015", "8 Series", 70, 79),
        ("G001", "X3, X4", 78, 94),
        ("G008", "iX3", 67, 82),
        ("G005", "X5, X6, X5 M", 78, 98),
        ("G007", "X7", 71, 92),
        ("G011", "7 Series (G11/G12)", 80, 108),
        ("G038", "5 Series long (China)", 62, 84),
        ("G030", "5/6 Series, M5", 73, 99),
        ("G060", "5 Series (G60)", 4, 7),
        ("G070", "7 Series (G70)", 3, 7),
        ("U006", "2 Series Active Tourer (U06)", 3, 8),
    ]),
    ("F-chassis", 131, 254, [
        ("F030", "3/4 Series F30-F36, M3/M4", 94, 94),
        ("F020", "1/2 Series F20-F23, M2", 76, 80),
        ("F040", "1 Series F40, 2 Gran Coupé", 44, 54),
        ("F015", "X5/X6 F15/F16, X5 M/X6 M", 60, 87),
        ("F025", "X3/X4 F25/F26", 57, 87),
        ("F045", "2 Active/Gran Tourer", 33, 56),
        ("F056", "MINI F54-F60", 34, 64),
        ("F010", "5/6 Series F10-F13, M5/M6", 64, 127),
        ("F001", "7 Series F01/F02", 61, 130),
    ]),
]

THEMES = {
    "light": dict(surface="#fcfcfb", ink="#0b0b0b", ink2="#52514e", muted="#898781", grid="#e1e0d9",
                  axis="#c3c2b7", bar="#2a78d6", sum="#1b4f8f", border="#e1e0d9", band="#f3f2ee"),
    "dark": dict(surface="#1a1a19", ink="#f0efec", ink2="#c3c2b7", muted="#898781", grid="#2c2c2a",
                 axis="#383835", bar="#3987e5", sum="#8bbcf5", border="#2c2c2a", band="#232322"),
}

W, PAD = 760, 24
LABEL_W = 250          # series code + cars
BAR_X = PAD + LABEL_W
BAR_W = 360            # 100 %
ROW_H, BAR_H = 26, 14
SUM_H, SUM_BAR = 34, 18
GAP = 18               # space before each family
TOP = 84
FONT = "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"


def bar_path(x, y, w, h, r=4):
    """Bar anchored square at the baseline (left), rounded at the data end (right)."""
    w = max(w, 2)
    r = min(r, w / 2, h / 2)
    return (f"M{x},{y} H{x + w - r} Q{x + w},{y} {x + w},{y + r} V{y + h - r} "
            f"Q{x + w},{y + h} {x + w - r},{y + h} H{x} Z")


def layout():
    """[(kind, y, label, sub, n, tot)] and the bottom y of the plot area."""
    items, y = [], TOP
    for gi, (fam, n, tot, rows) in enumerate(GROUPS):
        if gi:
            y += GAP
        items.append(("sum", y, fam, "all CAFD ids", n, tot))
        y += SUM_H
        for s, cars, rn, rt in rows:
            items.append(("row", y, s, cars, rn, rt))
            y += ROW_H
    return items, y


def svg(t):
    c = THEMES[t]
    items, bottom = layout()
    H = bottom + 94
    desc = "; ".join(f"{fam}: {n} of {tot} CAFD ids (" + ", ".join(f"{s} {rn} of {rt}" for s, _, rn, rt in rows) + ")"
                     for fam, n, tot, rows in GROUPS)
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
         f'font-family="{FONT}" role="img" aria-labelledby="t d">',
         '<title id="t">BMW names by chassis</title>',
         f'<desc id="d">Share of CAFD ids that have BMW untrimmed names in OpenLauncher. {escape(desc)}</desc>',
         f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="10" fill="{c["surface"]}" stroke="{c["border"]}"/>',
         f'<text x="{PAD}" y="34" font-size="17" font-weight="600" fill="{c["ink"]}">BMW names by chassis</text>',
         f'<text x="{PAD}" y="56" font-size="13" fill="{c["ink2"]}">Share of CAFDs that come with BMW\'s own group, '
         f'function and value names</text>']
    # family bands behind the summary rows
    for kind, y, *_ in items:
        if kind == "sum":
            o.append(f'<rect x="{PAD - 8}" y="{y - 7}" width="{W - 2 * PAD + 16}" height="{SUM_BAR + 14}" rx="6" '
                     f'fill="{c["band"]}"/>')
    y0, y1 = TOP - 10, bottom - 4
    for p in (0, 25, 50, 75, 100):
        x = BAR_X + BAR_W * p / 100
        o.append(f'<line x1="{x}" y1="{y0}" x2="{x}" y2="{y1}" stroke="{c["axis"] if p == 0 else c["grid"]}" '
                 f'stroke-width="1"/>')
        o.append(f'<text x="{x}" y="{y1 + 16}" font-size="11" fill="{c["muted"]}" text-anchor="middle">{p}%</text>')
    for kind, y, label, sub, n, tot in items:
        h = SUM_BAR if kind == "sum" else BAR_H
        cy = y + h / 2 + 4.5
        pct = round(100 * n / tot)
        w = BAR_W * n / tot
        if kind == "sum":
            o.append(f'<text x="{PAD}" y="{cy}" font-size="14.5" font-weight="700" fill="{c["ink"]}">{label}</text>')
            o.append(f'<text x="{PAD + 92}" y="{cy}" font-size="12.5" fill="{c["ink2"]}">{sub}</text>')
            fill, size, weight = c["sum"], 13.5, "700"
        else:
            o.append(f'<text x="{PAD}" y="{cy}" font-size="13" font-weight="600" fill="{c["ink"]}">{label}</text>')
            o.append(f'<text x="{PAD + 46}" y="{cy}" font-size="13" fill="{c["ink2"]}">{escape(sub)}</text>')
            fill, size, weight = c["bar"], 12, "400"
        o.append(f'<path d="{bar_path(BAR_X + 1, y, w - 1, h)}" fill="{fill}"><title>{label} {escape(sub)}: '
                 f'{n} of {tot} CAFD ids</title></path>')
        o.append(f'<text x="{BAR_X + w + 8}" y="{cy}" font-size="{size}" font-weight="{weight}" fill="{c["ink"]}">'
                 f'{pct}%<tspan font-weight="400" fill="{c["muted"]}">  {n} of {tot}</tspan></text>')
    fy = y1 + 38
    o.append(f'<text x="{PAD}" y="{fy}" font-size="11.5" fill="{c["ink2"]}">Across all G020 and F030 CAFD versions, '
             f'about 89% of functions carry verified BMW names; OpenLauncher dynamically calculates the rest.</text>')
    o.append(f'<text x="{PAD}" y="{fy + 16}" font-size="11.5" fill="{c["ink2"]}">Family totals count each CAFD id once. '
             f'G60, G70 and U06 list only 7-8 CAFD ids in psdzdata; G045 and F070 (2-4 ids) are left out.</text>')
    o.append(f'<text x="{PAD}" y="{fy + 32}" font-size="11.5" fill="{c["muted"]}">Data: {DATA}.</text>')
    o.append('</svg>')
    return "\n".join(o) + "\n"


os.makedirs(OUT, exist_ok=True)
for t in THEMES:
    with open(os.path.join(OUT, f"coverage-{t}.svg"), "w", encoding="utf-8", newline="\n") as f:
        f.write(svg(t))
print("ok")
