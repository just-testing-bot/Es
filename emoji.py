from __future__ import annotations

import io
import logging
from typing import List, Tuple, Optional

from PIL import Image, ImageDraw, ImageFont
from telegram import InputSticker

logger = logging.getLogger(__name__)

# Default font candidates; will fallback to PIL default if not available
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def list_available_fonts() -> List[str]:
    available = []
    for path in FONT_CANDIDATES:
        try:
            ImageFont.truetype(path, 64)
            available.append(path)
        except Exception:
            continue
    if not available:
        available.append("DEFAULT")
    return available


def render_text_emoji(
    text: str,
    font_path: Optional[str],
    canvas_size: Tuple[int, int] = (512, 512),
    text_color: Tuple[int, int, int, int] = (0, 0, 0, 255),
    background_mode: str = "none",  # none | translucent | background_only
    background_color: Tuple[int, int, int, int] = (255, 255, 255, 128),
) -> bytes:
    width, height = canvas_size

    if background_mode == "none":
        img = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    elif background_mode == "translucent":
        img = Image.new("RGBA", (width, height), background_color)
    elif background_mode == "background_only":
        # Will draw background only, text transparent by writing transparent text area
        img = Image.new("RGBA", (width, height), background_color)
    else:
        img = Image.new("RGBA", (width, height), (255, 255, 255, 0))

    draw = ImageDraw.Draw(img)

    # Pick font
    if font_path and font_path != "DEFAULT":
        try:
            font = ImageFont.truetype(font_path, size=380)
        except Exception:
            font = ImageFont.load_default()
    else:
        try:
            # Try DejaVu as default if installed
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size=380)
        except Exception:
            font = ImageFont.load_default()

    # Adjust font size to fit
    max_w, max_h = int(width * 0.9), int(height * 0.9)
    font_size = min(width, height)
    while font_size > 10:
        try:
            if font_path and font_path != "DEFAULT":
                font = ImageFont.truetype(font_path, size=font_size)
            else:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size=font_size)
        except Exception:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), text, font=font, align="center")
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if tw <= max_w and th <= max_h:
            break
        font_size -= 8

    # Center text
    bbox = draw.textbbox((0, 0), text, font=font, align="center")
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (width - tw) // 2
    ty = (height - th) // 2

    if background_mode == "background_only":
        # Erase text area to transparent
        text_mask = Image.new("L", (tw, th), 0)
        mask_draw = ImageDraw.Draw(text_mask)
        mask_draw.text((0, 0), text, font=font, fill=255)
        # Create a transparent overlay where text is
        transparent_layer = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        # Paste it using mask to clear text region
        img.paste(transparent_layer, (tx, ty), text_mask)
    else:
        draw.text((tx, ty), text, font=font, fill=text_color)

    # Export as PNG bytes
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


def pil_image_bytes_to_input_sticker(image_bytes: bytes, emojis: List[str]) -> InputSticker:
    # PNG static sticker for custom emoji packs is acceptable for Bot API when sticker_format='static'
    return InputSticker(sticker=image_bytes, format="static", emoji_list=emojis)