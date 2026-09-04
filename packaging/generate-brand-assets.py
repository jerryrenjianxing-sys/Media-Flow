from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "assets" / "brand"
PUBLIC = ROOT / "control_console" / "public"
INDIGO = (94, 106, 210, 255)
PERIWINKLE = (221, 225, 255, 255)
WHITE = (255, 255, 255, 255)
INK = (17, 18, 22, 255)
MUTED = (96, 101, 110, 255)


def cubic(p0, p1, p2, p3, steps=96):
    points = []
    for index in range(steps + 1):
        t = index / steps
        u = 1 - t
        points.append((
            u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
            u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1],
        ))
    return points


def mark(size: int, *, transparent: bool = False, monochrome: bool = False) -> Image.Image:
    scale = 8
    canvas = size * scale
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0) if transparent else INDIGO)
    draw = ImageDraw.Draw(image)
    if not transparent:
        radius = int(canvas * 0.235)
        mask = Image.new("L", (canvas, canvas), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, canvas - 1, canvas - 1), radius=radius, fill=255)
        image.putalpha(mask)
        draw = ImageDraw.Draw(image)
    primary = INDIGO if transparent else WHITE
    secondary = INDIGO if monochrome and transparent else (PERIWINKLE if not transparent else (142, 153, 242, 255))
    def pts(values):
        return [(x * canvas / 64, y * canvas / 64) for x, y in values]

    def stroke(values, color, width):
        scaled = pts(values)
        radius = width / 2
        for start, end in zip(scaled, scaled[1:]):
            draw.line((start, end), fill=color, width=width)
        for x, y in scaled:
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
    upper = []
    upper += cubic((11, 46), (11, 20), (19, 15), (27, 25))
    upper += cubic((27, 25), (29, 28), (31, 31), (32, 33))[1:]
    upper += cubic((32, 33), (40, 22), (44, 14), (50, 21))[1:]
    upper += cubic((50, 21), (53, 25), (53, 36), (53, 46))[1:]
    primary_width = max(2, int(canvas * 0.105))
    stroke(upper, primary, primary_width)
    lower = []
    lower += cubic((11, 30), (16, 18), (23, 18), (29, 28))
    lower += cubic((29, 28), (32, 33), (34, 37), (36, 40))[1:]
    lower += cubic((36, 40), (42, 49), (50, 44), (53, 33))[1:]
    secondary_width = max(2, int(canvas * 0.067))
    stroke(lower, secondary, secondary_width)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def font(size: int, bold: bool = False, cjk: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    segoe = Path(r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf")
    yahei = Path(r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc")
    candidates = [yahei, segoe] if cjk else [segoe, yahei]
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def installer_graphic(size: tuple[int, int], *, compact: bool) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size, (245, 246, 247))
    draw = ImageDraw.Draw(image)
    icon_size = 42 if compact else min(96, height // 3)
    icon = mark(icon_size)
    x = 18 if compact else 46
    y = (height - icon_size) // 2 if compact else 48
    image.paste(icon.convert("RGB"), (x, y), icon.getchannel("A"))
    title_size = 24 if compact else 38
    draw.text((x + icon_size + (14 if compact else 24), y + (2 if compact else 8)), "MediaFlow", font=font(title_size, True), fill=INK)
    if not compact:
        draw.text((x + icon_size + 26, y + 58), "媒体自动化平台", font=font(14, cjk=True), fill=MUTED)
        draw.text((48, height - 48), "正在准备 MediaFlow…", font=font(14, cjk=True), fill=MUTED)
    return image


def main() -> None:
    BRAND.mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256, 512]
    rendered = {size: mark(size) for size in sizes}
    for size, image in rendered.items():
        image.save(BRAND / f"mediaflow-{size}.png")
    rendered[256].save(BRAND / "mediaflow.ico", sizes=[(size, size) for size in sizes if size <= 256])
    mark(64, transparent=True).save(BRAND / "mediaflow-mark.png")
    mark(64, transparent=True, monochrome=True).save(BRAND / "mediaflow-tray.png")
    installer_graphic((493, 58), compact=True).save(BRAND / "mediaflow-msi-banner.bmp")
    installer_graphic((493, 312), compact=False).save(BRAND / "mediaflow-msi-logo.bmp")
    installer_graphic((560, 300), compact=False).save(BRAND / "mediaflow-installer-splash.png")
    (PUBLIC / "favicon.svg").write_text((BRAND / "mediaflow-app-icon.svg").read_text(encoding="utf-8"), encoding="utf-8")
    rendered[192].save(PUBLIC / "mediaflow-192.png") if 192 in rendered else mark(192).save(PUBLIC / "mediaflow-192.png")
    rendered[512].save(PUBLIC / "mediaflow-512.png")


if __name__ == "__main__":
    main()
