"""Create the bundled Windows icon or a private local-photo variant.

This runs only as part of a local package build.  It does not read user files,
open a camera, or contact Codex.
"""

from argparse import ArgumentParser
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps


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


def draw_photo_icon(photo_path: Path, size: int) -> Image.Image:
    with Image.open(photo_path) as source:
        source = ImageOps.exif_transpose(source).convert("RGB")
        inset = max(3, size // 28)
        portrait = ImageOps.fit(
            source,
            (size - inset * 2, size - inset * 2),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.38),
        ).convert("RGBA")

    image = Image.new("RGBA", (size, size), "#F8DDEC")
    mask = Image.new("L", portrait.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(
        (0, 0, portrait.width - 1, portrait.height - 1),
        radius=max(2, size // 7),
        fill=255,
    )
    image.paste(portrait, (inset, inset), mask)
    return image


def create_icon(*, photo_path: Path | None = None, output_path: Path = OUTPUT) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = draw_photo_icon(photo_path, 256) if photo_path else draw_icon(256)
    image.save(
        output_path,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    return output_path


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--photo", type=Path)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    arguments = parser.parse_args()
    if arguments.photo and not arguments.photo.is_file():
        parser.error(f"photo does not exist: {arguments.photo}")
    create_icon(photo_path=arguments.photo, output_path=arguments.output)
