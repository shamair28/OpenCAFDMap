#!/usr/bin/env python3
"""Draws OpenLauncher's icon: E-Sys's trimmed "." names (white dots) turning into readable labels
(teal bars). Writes assets/openlauncher.png (256 px) and assets/openlauncher.ico (16-256 px).

    python assets/make_icon.py
"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
S = 1024  # draw large, scale down for crisp small sizes


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def draw():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # background: rounded square, vertical navy -> blue gradient
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    top, bottom = (16, 42, 84), (10, 92, 168)
    for y in range(S):
        gd.line([(0, y), (S, y)], fill=lerp(top, bottom, y / (S - 1)) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([24, 24, S - 24, S - 24], radius=210, fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    # three rows: dot (trimmed name) -> label bar (restored name)
    rows = [(300, 520), (512, 400), (724, 470)]  # (y centre, bar length)
    dot_r, bar_h, x_dot, x_bar = 62, 104, 236, 360
    for i, (y, length) in enumerate(rows):
        d.ellipse([x_dot - dot_r, y - dot_r, x_dot + dot_r, y + dot_r], fill=(255, 255, 255, 255))
        colour = (46, 214, 196, 255) if i != 1 else (255, 196, 61, 255)  # middle row: amber accent
        d.rounded_rectangle([x_bar, y - bar_h // 2, x_bar + length, y + bar_h // 2], radius=bar_h // 2,
                            fill=colour)
    return img


def main():
    big = draw()
    big.resize((256, 256), Image.LANCZOS).save(os.path.join(HERE, "openlauncher.png"), optimize=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [big.resize((n, n), Image.LANCZOS) for n in sizes]
    frames[-1].save(os.path.join(HERE, "openlauncher.ico"), sizes=[(n, n) for n in sizes],
                    append_images=frames[:-1])
    print("wrote openlauncher.png and openlauncher.ico")


if __name__ == "__main__":
    main()
