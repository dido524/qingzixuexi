"""Create the bundled Windows icon from the reviewable SVG colour palette.

This runs only as part of a local package build.  It does not read user files,
open a camera, or contact Codex.
"""

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets" / "qingzi-learning-assistant.ico"


def draw_icon(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), "#E8F5F1")
    draw = ImageDraw.Draw(image)
    radius = size // 5
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill="#E8F5F1")
    draw.ellipse((int(size * .65), int(size * .14), int(size * .86), int(size * .35)), fill="#FFD86B")
    draw.polygon([(int(size*.16), int(size*.40)), (size//2, int(size*.37)), (size//2, int(size*.76)), (int(size*.16), int(size*.79))], fill="#3E9B8A")
    draw.polygon([(size//2, int(size*.37)), (int(size*.84), int(size*.40)), (int(size*.84), int(size*.79)), (size//2, int(size*.76))], fill="#56B6A2")
    line = max(2, size // 38)
    draw.line((size//2, int(size*.39), size//2, int(size*.75)), fill="#FFFDF7", width=line)
    for y in (.49, .59):
        draw.line((int(size*.26), int(size*y), int(size*.43), int(size*(y+.01))), fill="#FFFDF7", width=line)
        draw.line((int(size*.57), int(size*(y+.01)), int(size*.74), int(size*y)), fill="#FFFDF7", width=line)
    return image


if __name__ == "__main__":
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    image = draw_icon(256)
    image.save(OUTPUT, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
