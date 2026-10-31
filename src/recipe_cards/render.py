from pathlib import Path
import math
import qrcode
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .storage import atomic

GREEN = "#102c24"
WHITE = "#faf9f3"
MUTED = "#c6d0c8"
GOLD = "#dfb972"
Image.MAX_IMAGE_PIXELS = 24_000_000


def font(cfg, size, bold=False):
    configured = cfg.font_bold if bold else cfg.font
    names = [configured] if configured else []
    names += ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf"]
    for path in names:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def wrap(value, face, width):
    """Pixel wrapping, including single words longer than the text column."""
    lines, line = [], ""
    for word in value.split():
        if line and face.getlength(line + " " + word) <= width:
            line += " " + word
            continue
        if line:
            lines.append(line)
            line = ""
        while word and face.getlength(word) > width:
            n = 1
            while n < len(word) and face.getlength(word[:n + 1]) <= width:
                n += 1
            lines.append(word[:n])
            word = word[n:]
        line = word
    if line:
        lines.append(line)
    return lines


def ellipsize(value, face, width):
    while value and face.getlength(value + "…") > width:
        value = value[:-1]
    return value.rstrip() + "…"


def fit(value, cfg, width, height, maximum=100, minimum=64):
    for size in range(maximum, minimum - 1, -2):
        face = font(cfg, size, True)
        lines = wrap(value, face, width)
        if len(lines) * int(size * 1.12) <= height:
            return face, lines, int(size * 1.12)
    face = font(cfg, minimum, True)
    leading = int(minimum * 1.12)
    count = max(1, height // leading)
    lines = wrap(value, face, width)[:count]
    lines[-1] = ellipsize(lines[-1], face, width)
    return face, lines, leading


def crop_photo(path, size):
    with Image.open(path) as original:
        if original.width * original.height > 24_000_000:
            raise ValueError("Photograph exceeds 24 megapixels")
        image = ImageOps.exif_transpose(original).convert("RGB")
    # Centered subject crop; honor EXIF, never distort the source aspect ratio.
    return ImageOps.fit(image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.47))


def star(draw, x, y, radius, fill):
    points = []
    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5
        r = radius if i % 2 == 0 else radius * .44
        points.append((x + math.cos(angle) * r, y + math.sin(angle) * r))
    draw.polygon(points, fill=fill)


def render(cfg, recipe, path):
    canvas = Image.new("RGB", (cfg.width, cfg.height), GREEN)
    canvas.paste(crop_photo(recipe.image, (960, 1200)), (960, 0))
    draw = ImageDraw.Draw(canvas)
    heading = "GOOD FOOD · " + recipe.category.upper()
    draw.text((72, 70), heading, font=font(cfg, 32, True), fill=GOLD)
    if recipe.demonstration:
        draw.text((72, 123), "DEMONSTRATION · FICTIONAL RECIPE", font=font(cfg, 24, True), fill=MUTED)
    face, lines, leading = fit(recipe.title, cfg, 816, 415, maximum=108, minimum=64)
    y = 215
    for line in lines:
        draw.text((72, y), line, font=face, fill=WHITE, anchor="lt")
        y += leading
    desc_face = font(cfg, 36)
    lines = wrap(recipe.description, desc_face, 816)
    if len(lines) > 2:
        lines = lines[:2]
        lines[-1] = ellipsize(lines[-1], desc_face, 816)
    for i, line in enumerate(lines):
        draw.text((72, 660 + i * 47), line, font=desc_face, fill=MUTED, anchor="lt")
    metrics = []
    if recipe.total:
        metrics.append("Total " + recipe.total)
    else:
        if recipe.prep:
            metrics.append("Prep " + recipe.prep)
        if recipe.cook:
            metrics.append("Cook " + recipe.cook)
    if recipe.difficulty:
        metrics.append(recipe.difficulty)
    metric_face = font(cfg, 33, True)
    # At most two rows; source fields remain intact in cache/status.
    metric_lines = wrap("  ·  ".join(metrics), metric_face, 816)[:2]
    for i, line in enumerate(metric_lines):
        draw.text((72, 792 + i * 43), line, font=metric_face, fill=WHITE, anchor="lt")
    if recipe.rating is not None:
        # Fractional star fills accompany the exact source numeric value.
        for i in range(5):
            x, y = 94 + 45 * i, 925
            star(draw, x, y, 18, "#465e52")
            fraction = max(0, min(1, recipe.rating - i))
            if fraction:
                layer = Image.new("RGB", (42, 42), GREEN)
                star(ImageDraw.Draw(layer), 21, 21, 18, GOLD)
                canvas.paste(layer.crop((0, 0, round(42 * fraction), 42)), (x - 21, y - 21))
        draw.text((322, 906), f"{recipe.rating:g}/5  ({recipe.rating_count:,})", font=font(cfg, 30), fill=WHITE)
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=4, border=4)
    qr.add_data(recipe.url)
    qr.make(fit=True)
    matrix = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    # Integer scale, four-module quiet zone. No resampling blur.
    # matrix already has 4 px/module; nearest resizing retains module edges.
    modules = len(qr.get_matrix())
    px = max(3, 196 // modules)
    matrix = matrix.resize((modules * px, modules * px), Image.Resampling.NEAREST)
    canvas.paste(matrix, (888 - matrix.width, 1170 - matrix.height))
    draw.text((72, 1070), "bbcgoodfood.com", font=font(cfg, 32, True), fill=WHITE)
    draw.text((72, 1120), "Scan for the recipe", font=font(cfg, 26), fill=MUTED)
    if recipe.credit:
        # Credit on photo has an opaque backing for contrast.
        credit_face = font(cfg, 18)
        credit_lines = wrap(recipe.credit, credit_face, 880)
        if len(credit_lines) > 2:
            raise ValueError("Photo credit does not fit; use a shorter approved credit")
        top = 1190 - len(credit_lines) * 24 - 16
        draw.rectangle((980, top, 1900, 1189), fill=GREEN)
        for i, line in enumerate(credit_lines):
            draw.text((995, top + 8 + i * 24), line, font=credit_face, fill=WHITE, anchor="lt")
    atomic(path, lambda f: canvas.save(f, format="PNG"))
    return canvas
