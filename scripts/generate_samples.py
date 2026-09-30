"""Render synthetic test labels plus the matching application data.

Each sample exercises one situation from the stakeholder interviews (exact
match, capitalization differences, wrong ABV, title-case warning, altered
warning wording, non-bold header, bad photo, ...). The output doubles as the
demo set in the UI and as the regression set in tests/test_samples.py.

    python scripts/generate_samples.py

Writes to app/static/samples/. Uses macOS or Windows system fonts when present
and DejaVu on Linux, so exact pixels differ between machines.
"""

from __future__ import annotations

import csv
import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parents[1] / "app" / "static" / "samples"

STANDARD_BODY = (
    "(1) According to the Surgeon General, women should not drink alcoholic beverages during pregnancy "
    "because of the risk of birth defects. (2) Consumption of alcoholic beverages impairs your ability "
    "to drive a car or operate machinery, and may cause health problems."
)

# --------------------------------------------------------------------------- fonts

_FONT_DIRS = [Path("/System/Library/Fonts/Supplemental"), Path("/System/Library/Fonts"),
              Path("C:/Windows/Fonts"),
              Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/dejavu")]
# macOS name, then Windows name, then DejaVu (Linux).
_FALLBACKS = {
    "regular": ["Arial.ttf", "arial.ttf", "DejaVuSans.ttf"],
    "bold": ["Arial Bold.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"],
    "serif": ["Georgia.ttf", "georgia.ttf", "DejaVuSerif.ttf"],
    "serif_italic": ["Georgia Italic.ttf", "georgiai.ttf", "DejaVuSerif-Italic.ttf", "DejaVuSerif.ttf"],
    "serif_bold": ["Georgia Bold.ttf", "georgiab.ttf", "DejaVuSerif-Bold.ttf"],
    "didot": ["Didot.ttc", "georgiab.ttf", "DejaVuSerif-Bold.ttf"],
    "copperplate": ["Copperplate.ttc", "georgiab.ttf", "DejaVuSerif-Bold.ttf"],
    "bodoni": ["Bodoni 72.ttc", "georgiab.ttf", "DejaVuSerif-Bold.ttf"],
    "impact": ["Impact.ttf", "impact.ttf", "DejaVuSans-Bold.ttf"],
    "futura": ["Futura.ttc", "arialbd.ttf", "DejaVuSans-Bold.ttf"],
    "caslon": ["BigCaslon.ttf", "georgiab.ttf", "DejaVuSerif-Bold.ttf"],
}


def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    for name in _FALLBACKS[kind]:
        for d in _FONT_DIRS:
            if (d / name).exists():
                return ImageFont.truetype(str(d / name), size)
    return ImageFont.load_default(size)


# --------------------------------------------------------------------------- label spec


@dataclass
class LabelSpec:
    brand: str
    class_type: str
    abv: str | None
    net: str
    bottler: str
    brand_font: str = "didot"
    tagline: str = ""
    extra: str = ""
    country: str = ""
    warning_header: str | None = "GOVERNMENT WARNING:"  # None = no warning at all
    warning_body: str = STANDARD_BODY
    warning_bold: bool = True
    bg: str = "#f3ead7"
    ink: str = "#1f1a14"
    accent: str = "#8a5a2b"
    size: tuple[int, int] = (1200, 1340)


@dataclass
class Sample:
    id: str
    title: str
    description: str
    expect: str
    label: LabelSpec
    application: dict
    photo: str = ""  # "", "angled", "dim"
    ext: str = "png"
    extra_meta: dict = field(default_factory=dict)


def _fit(draw: ImageDraw.ImageDraw, text: str, kind: str, max_size: int, max_width: int) -> ImageFont.FreeTypeFont:
    size = max_size
    while size > 16:
        f = font(kind, size)
        if draw.textlength(text, font=f) <= max_width:
            return f
        size -= 2
    return font(kind, size)


def _center(draw, y: int, text: str, f, fill, width: int) -> int:
    w = draw.textlength(text, font=f)
    draw.text(((width - w) / 2, y), text, font=f, fill=fill)
    box = draw.textbbox((0, 0), text, font=f)
    return y + (box[3] - box[1])


def _wrap(draw, text: str, f, max_width: int) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=f) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def render_label(spec: LabelSpec) -> Image.Image:
    W, H = spec.size
    img = Image.new("RGB", spec.size, spec.bg)
    d = ImageDraw.Draw(img)
    d.rectangle([28, 28, W - 28, H - 28], outline=spec.accent, width=8)
    d.rectangle([48, 48, W - 48, H - 48], outline=spec.accent, width=2)

    y = 110
    if spec.tagline:
        y = _center(d, y, spec.tagline, font("serif", 36), spec.accent, W) + 40
    for line in spec.brand.split("\n"):
        f = _fit(d, line, spec.brand_font, 150, W - 200)
        y = _center(d, y, line, f, spec.ink, W) + 28

    # ornament
    y += 16
    d.line([(200, y), (W / 2 - 30, y)], fill=spec.accent, width=3)
    d.line([(W / 2 + 30, y), (W - 200, y)], fill=spec.accent, width=3)
    d.polygon([(W / 2, y - 16), (W / 2 + 16, y), (W / 2, y + 16), (W / 2 - 16, y)], fill=spec.accent)
    y += 50

    f_class = font("serif_italic", 54)
    for line in _wrap(d, spec.class_type, f_class, W - 240):
        y = _center(d, y, line, f_class, spec.ink, W) + 18
    if spec.extra:
        y = _center(d, y + 20, spec.extra, font("serif", 36), spec.accent, W) + 10

    # Bottom section is laid out upward from the warning block.
    f_warn = font("regular", 23)
    f_warn_head = font("bold" if spec.warning_bold else "regular", 23)
    warn_lines: list[list[tuple[str, ImageFont.FreeTypeFont]]] = []
    if spec.warning_header is not None:
        words = [(w, f_warn_head) for w in spec.warning_header.split()] + [(w, f_warn) for w in spec.warning_body.split()]
        line: list[tuple[str, ImageFont.FreeTypeFont]] = []
        width = 0.0
        for word, f in words:
            w = d.textlength(word + " ", font=f)
            if line and width + w > W - 190:
                warn_lines.append(line)
                line, width = [], 0.0
            line.append((word, f))
            width += w
        warn_lines.append(line)
    line_h = 31
    warn_top = H - 90 - line_h * len(warn_lines)
    for i, line in enumerate(warn_lines):
        x = 95.0
        for word, f in line:
            d.text((x, warn_top + i * line_h), word, font=f, fill=spec.ink)
            x += d.textlength(word + " ", font=f)

    y_b = warn_top - 40
    f_small = font("serif", 30)
    lower: list[tuple[str, ImageFont.FreeTypeFont]] = []
    lower += [(ln, f_small) for ln in _wrap(d, spec.bottler, f_small, W - 260)]
    if spec.country:
        lower.append((spec.country, font("serif_bold", 32)))
    for text, f in reversed(lower):
        box = d.textbbox((0, 0), text, font=f)
        y_b -= (box[3] - box[1]) + 14
        _center(d, y_b, text, f, spec.ink, W)

    y_b -= 50
    f_stat = font("serif_bold", 44)
    stat_line = "     ".join(t for t in [spec.abv, spec.net] if t)
    box = d.textbbox((0, 0), stat_line, font=f_stat)
    y_b -= box[3] - box[1]
    _center(d, y_b, stat_line, f_stat, spec.ink, W)
    return img


# --------------------------------------------------------------------------- photo simulation


def photograph(label: Image.Image, style: str, seed: int = 7) -> Image.Image:
    """Make a flat label look like a quick phone photo of a bottle."""
    rng = np.random.default_rng(seed)
    lab = np.asarray(label).astype(np.float32) / 255.0
    h, w, _ = lab.shape

    # Curvature shading: darker toward the left/right edges like a cylinder.
    xs = np.linspace(-1, 1, w)
    shade = 0.62 + 0.38 * np.cos(xs * math.pi / 2.4) ** 1.5
    lab *= shade[None, :, None]

    if style == "angled":
        # Vertical glare streak over the upper part of the label.
        glare = np.zeros((h, w), np.float32)
        cx = int(w * 0.68)
        cv2.ellipse(glare, (cx, int(h * 0.36)), (int(w * 0.035), int(h * 0.25)), 0, 0, 360, 1.0, -1)
        glare = cv2.GaussianBlur(glare, (0, 0), 22)
        lab = lab + glare[..., None] * 1.3
    if style == "dim":
        lab *= 0.42
        lab += np.array([0.03, 0.015, 0.0], np.float32)  # warm indoor light

    lab = np.clip(lab, 0, 1)
    canvas_w, canvas_h = int(w * 1.35), int(h * 1.3)
    yy = np.linspace(0, 1, canvas_h)[:, None, None]
    background = (np.array([0.24, 0.17, 0.11]) * (0.7 + 0.3 * yy)).astype(np.float32)
    background = np.broadcast_to(background, (canvas_h, canvas_w, 3)).copy()
    background += rng.normal(0, 0.02, background.shape).astype(np.float32)

    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    ox, oy = (canvas_w - w) / 2, (canvas_h - h) / 2
    if style == "angled":
        dst = np.float32([[ox + 60, oy + 10], [ox + w - 10, oy + 70], [ox + w + 20, oy + h - 40], [ox + 20, oy + h + 10]])
    else:
        dst = np.float32([[ox + 15, oy], [ox + w, oy + 20], [ox + w - 10, oy + h], [ox, oy + h - 15]])
    M = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(lab, M, (canvas_w, canvas_h))
    mask = cv2.warpPerspective(np.ones((h, w), np.float32), M, (canvas_w, canvas_h))[..., None]
    out = warped * mask + background * (1 - mask)
    out += rng.normal(0, 0.015 if style == "angled" else 0.03, out.shape).astype(np.float32)
    img = Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8))
    img = img.filter(ImageFilter.GaussianBlur(1.1 if style == "angled" else 0.8))
    return img.resize((img.width * 3 // 4, img.height * 3 // 4), Image.LANCZOS)


# --------------------------------------------------------------------------- samples

OLD_TOM = LabelSpec(
    brand="OLD TOM\nDISTILLERY", class_type="Kentucky Straight Bourbon Whiskey",
    abv="45% Alc./Vol. (90 Proof)", net="750 mL", tagline="EST. 1870",
    bottler="Distilled and Bottled by Old Tom Distillery, Louisville, Kentucky", extra="Aged 6 Years",
)
OLD_TOM_APP = {
    "brand_name": "OLD TOM DISTILLERY", "class_type": "Kentucky Straight Bourbon Whiskey",
    "alcohol_content": "45% Alc./Vol. (90 Proof)", "net_contents": "750 mL",
    "bottler": "Old Tom Distillery, Louisville, KY",
}

SAMPLES = [
    Sample(
        "old-tom-bourbon", "Bourbon: everything matches",
        "The example label from the brief. The application abbreviates Kentucky as KY, which is accepted.",
        "pass", OLD_TOM, OLD_TOM_APP,
    ),
    Sample(
        "stones-throw-gin", "Gin: brand name in capitals",
        "The label says STONE'S THROW and the application says Stone's Throw. Same name, so it matches.",
        "pass",
        LabelSpec(brand="STONE'S THROW", brand_font="copperplate", class_type="London Dry Gin",
                  abv="47% Alc./Vol. (94 Proof)", net="750 mL", tagline="SMALL BATCH",
                  bottler="Distilled and Bottled by Stone's Throw Spirits Co., Portland, OR",
                  bg="#e9f0ee", ink="#15302b", accent="#2f6b5f"),
        {"brand_name": "Stone's Throw", "class_type": "London Dry Gin", "alcohol_content": "47%",
         "net_contents": "750 mL", "bottler": "Stone's Throw Spirits Co., Portland, Oregon"},
    ),
    Sample(
        "river-bend-vodka", "Vodka: wrong alcohol content",
        "The label says 40% (80 proof) but the application says 45% (90 proof).",
        "fail",
        LabelSpec(brand="RIVER BEND", brand_font="futura", class_type="Vodka", abv="40% Alc./Vol. (80 Proof)",
                  net="1 L", tagline="DISTILLED 5 TIMES", bottler="Bottled by River Bend Spirits, Austin, TX",
                  bg="#eef2f7", ink="#10243e", accent="#3a6ea5"),
        {"brand_name": "River Bend", "class_type": "Vodka", "alcohol_content": "45% Alc./Vol. (90 Proof)",
         "net_contents": "1 L", "bottler": "River Bend Spirits, Austin, TX"},
    ),
    Sample(
        "copper-kettle-rum", "Rum: warning header in title case",
        "The warning starts “Government Warning:” instead of all capitals. Must be rejected.",
        "fail",
        LabelSpec(brand="COPPER KETTLE", brand_font="bodoni", class_type="Spiced Rum",
                  abv="35% Alc./Vol. (70 Proof)", net="750 mL", tagline="CARIBBEAN RECIPE",
                  bottler="Produced and Bottled by Copper Kettle Rum Co., Key West, Florida",
                  warning_header="Government Warning:", bg="#f6e7d7", ink="#3b1d0e", accent="#b0602a"),
        {"brand_name": "Copper Kettle", "class_type": "Spiced Rum", "alcohol_content": "35% Alc./Vol. (70 Proof)",
         "net_contents": "750 mL", "bottler": "Copper Kettle Rum Co., Key West, FL"},
    ),
    Sample(
        "harbor-light-ipa", "Beer: warning wording changed",
        "Words are missing from the warning. Also shows 12 FL. OZ. on the label matching 355 mL in the application.",
        "fail",
        LabelSpec(brand="HARBOR LIGHT", brand_font="impact", class_type="India Pale Ale", abv="6.5% Alc./Vol.",
                  net="12 FL. OZ.", tagline="BREWED ON THE COAST",
                  bottler="Brewed and Canned by Harbor Light Brewing, Portland, Maine",
                  warning_body=("(1) According to the Surgeon General, women should not drink alcoholic beverages "
                                "during pregnancy. (2) Consumption of alcoholic beverages impairs your ability to "
                                "drive a car or operate machinery."),
                  bg="#e8eef4", ink="#0f2233", accent="#d17a22"),
        {"brand_name": "Harbor Light", "class_type": "India Pale Ale", "alcohol_content": "6.5%",
         "net_contents": "355 mL", "bottler": "Harbor Light Brewing, Portland, ME"},
    ),
    Sample(
        "blue-ridge-rye", "Rye: warning header not bold",
        "“GOVERNMENT WARNING:” is in regular type instead of bold. Flagged for the agent to confirm.",
        "review",
        LabelSpec(brand="BLUE RIDGE", brand_font="caslon", class_type="Straight Rye Whiskey",
                  abv="50% Alc./Vol. (100 Proof)", net="750 mL", tagline="BOTTLED IN BOND",
                  bottler="Distilled and Bottled by Blue Ridge Distilling, Asheville, NC", warning_bold=False,
                  bg="#eceae4", ink="#1c2530", accent="#44607a"),
        {"brand_name": "Blue Ridge", "class_type": "Straight Rye Whiskey",
         "alcohol_content": "50% Alc./Vol. (100 Proof)", "net_contents": "750 mL",
         "bottler": "Blue Ridge Distilling, Asheville, NC"},
    ),
    Sample(
        "maison-leblanc-wine", "Imported wine: country of origin",
        "An imported French wine. Checks the importer and “Product of France”.",
        "pass",
        LabelSpec(brand="Maison Leblanc", brand_font="didot", class_type="Côtes du Rhône Rouge",
                  abv="13.5% Alc. by Vol.", net="750 mL", tagline="GRAND VIN", extra="2021",
                  bottler="Imported by Leblanc Imports LLC, New York, NY", country="Product of France",
                  bg="#f7f3ea", ink="#3a1020", accent="#7d1f3a"),
        {"brand_name": "Maison Leblanc", "class_type": "Côtes du Rhône Rouge", "alcohol_content": "13.5%",
         "net_contents": "750 mL", "bottler": "Leblanc Imports LLC, New York, NY", "country_of_origin": "France"},
    ),
    Sample(
        "golden-fields-wheat", "Beer: brand name one letter off",
        "The label says GOLDEN FIELD, the application says Golden Fields. Flagged as a near match for the agent.",
        "review",
        LabelSpec(brand="GOLDEN FIELD", brand_font="futura", class_type="Wheat Ale", abv="5.2% Alc./Vol.",
                  net="16 FL. OZ.", tagline="HARVEST SERIES",
                  bottler="Brewed and Canned by Golden Fields Brewing Co., Fargo, ND",
                  bg="#fbf1d6", ink="#3d2c05", accent="#c4901a"),
        {"brand_name": "Golden Fields", "class_type": "Wheat Ale", "alcohol_content": "5.2%",
         "net_contents": "16 fl oz", "bottler": "Golden Fields Brewing Co., Fargo, ND"},
    ),
    Sample(
        "summit-tequila-no-warning", "Tequila: no government warning",
        "The health warning is missing entirely.",
        "fail",
        LabelSpec(brand="SUMMIT", brand_font="copperplate", class_type="Tequila Blanco", abv="40% Alc./Vol. (80 Proof)",
                  net="750 mL", tagline="100% AGAVE", bottler="Imported by Summit Spirits Group, San Diego, CA",
                  country="Product of Mexico", warning_header=None, bg="#eef4f1", ink="#12302a", accent="#3f8f7b"),
        {"brand_name": "Summit", "class_type": "Tequila Blanco", "alcohol_content": "40%", "net_contents": "750 mL",
         "bottler": "Summit Spirits Group, San Diego, CA", "country_of_origin": "Mexico"},
    ),
    Sample(
        "old-tom-bourbon-photo", "Photo at an angle with glare",
        "The bourbon label photographed on a bottle: tilted, with a glare streak across the name. "
        "Text hidden by glare is sent for review rather than failed.",
        "pass", OLD_TOM, OLD_TOM_APP, photo="angled", ext="jpg",
    ),
    Sample(
        "stones-throw-gin-dim", "Dim, noisy photo",
        "The gin label photographed in poor light.",
        "pass",
        LabelSpec(brand="STONE'S THROW", brand_font="copperplate", class_type="London Dry Gin",
                  abv="47% Alc./Vol. (94 Proof)", net="750 mL", tagline="SMALL BATCH",
                  bottler="Distilled and Bottled by Stone's Throw Spirits Co., Portland, OR",
                  bg="#e9f0ee", ink="#15302b", accent="#2f6b5f"),
        {"brand_name": "Stone's Throw", "class_type": "London Dry Gin", "alcohol_content": "47%",
         "net_contents": "750 mL", "bottler": "Stone's Throw Spirits Co., Portland, Oregon"},
        photo="dim", ext="jpg",
    ),
]

CSV_FIELDS = ["filename", "brand_name", "class_type", "alcohol_content", "net_contents", "bottler", "country_of_origin"]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    random.seed(1)
    manifest = []
    for s in SAMPLES:
        img = render_label(s.label)
        if s.photo:
            img = photograph(img, s.photo)
        filename = f"{s.id}.{s.ext}"
        if s.ext == "jpg":
            img.save(OUT / filename, quality=82)
        else:
            img.save(OUT / filename, optimize=True)
        # A compliant label in a poor photo may reasonably be sent for review, never failed.
        acceptable = [s.expect, "review"] if s.photo and s.expect == "pass" else [s.expect]
        manifest.append({"id": s.id, "file": filename, "title": s.title, "description": s.description,
                         "expect": s.expect, "acceptable": acceptable, "application": s.application})
        print(f"wrote {filename}")

    (OUT / "samples.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    with open(OUT / "batch-example.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for m in manifest:
            writer.writerow({"filename": m["file"], **{k: m["application"].get(k, "") for k in CSV_FIELDS[1:]}})
    with open(OUT / "batch-template.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerow({"filename": "my-label.jpg", **{k: OLD_TOM_APP.get(k, "") for k in CSV_FIELDS[1:]}})
    print("wrote samples.json, batch-example.csv, batch-template.csv")


if __name__ == "__main__":
    main()
