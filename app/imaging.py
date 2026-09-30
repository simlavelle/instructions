"""Image loading, quality checks, and clean-up for hard-to-read photos."""

from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from app.models import ImageQuality

MAX_BYTES = 20 * 1024 * 1024
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "BMP", "TIFF", "GIF"}


class ImageError(ValueError):
    """Raised with a message that can be shown to the agent as-is."""


def load_image(data: bytes) -> Image.Image:
    if not data:
        raise ImageError("The file is empty. Please choose the label image again.")
    if len(data) > MAX_BYTES:
        raise ImageError(f"The image is larger than {MAX_BYTES // (1024 * 1024)} MB. Please use a smaller file.")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ImageError("That file isn't an image we can open. Please use a JPG or PNG file.") from exc
    if img.format not in ALLOWED_FORMATS:
        raise ImageError(f"{img.format} images aren't supported. Please use a JPG or PNG file.")
    # Phone photos store rotation in EXIF; apply it so text is upright.
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        background = Image.new("RGB", img.size, "white")
        rgba = img.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[3])
        img = background
    return img


def resized(img: Image.Image, max_side: int) -> Image.Image:
    scale = max_side / max(img.size)
    if scale >= 1:
        return img
    return img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)


def to_bgr(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


def assess_quality(img: Image.Image) -> ImageQuality:
    """Cheap checks that explain *why* a label might be hard to read."""
    issues: list[str] = []
    if min(img.size) < 500:
        issues.append("The image is small (low resolution), so fine print may be unreadable.")

    gray = cv2.cvtColor(np.asarray(resized(img, 1200)), cv2.COLOR_RGB2GRAY)
    sharpness = cv2.Laplacian(gray, cv2.CV_64F).var()
    if sharpness < 60:
        issues.append("The image looks blurry.")
    brightness = float(gray.mean())
    if brightness < 60:
        issues.append("The image is very dark.")
    elif brightness > 235:
        issues.append("The image is very bright or washed out.")
    if gray.std() < 25:
        issues.append("The image has low contrast.")
    if _glare_fraction(np.asarray(resized(img, 1200))) > 0.005:
        issues.append("There is glare on part of the label.")
    return ImageQuality(width=img.width, height=img.height, issues=issues)


def _glare_fraction(rgb: np.ndarray) -> float:
    """Share of the image covered by glare.

    Glare is a solid, blown-out, colourless blob whose edge fades gradually
    into the surface around it. Bright label paper, white frames and the
    insides of large letters are also blown out, but they are hollow or have a
    hard edge against dark ink, so they are ignored.
    """
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    value = hsv[..., 2]
    mask = ((value > 245) & (hsv[..., 1] < 30)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    total = value.size
    glare = 0
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        if not total * 0.001 <= area <= total * 0.25:
            continue
        if area / (stats[i, cv2.CC_STAT_WIDTH] * stats[i, cv2.CC_STAT_HEIGHT]) < 0.5:
            continue  # hollow shape such as a frame
        blob = (labels == i).astype(np.uint8)
        edge = cv2.dilate(blob, np.ones((5, 5), np.uint8)) - blob
        if value[edge.astype(bool)].mean() >= 215:  # soft falloff, not a hard edge against ink
            glare += area
    return glare / total


def enhance_for_ocr(bgr: np.ndarray) -> np.ndarray:
    """Even out lighting and tame glare before a second OCR attempt.

    CLAHE lifts text out of shadows and dim areas; blown-out highlights are
    inpainted from their surroundings so the detector doesn't see a hole.
    """
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l_channel, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    lab = cv2.merge((clahe.apply(l_channel), a, b))
    out = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    highlights = ((hsv[..., 2] > 245) & (hsv[..., 1] < 25)).astype(np.uint8) * 255
    highlights = cv2.morphologyEx(highlights, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    if 0 < highlights.mean() / 255 < 0.25:
        highlights = cv2.dilate(highlights, np.ones((5, 5), np.uint8))
        out = cv2.inpaint(out, highlights, 5, cv2.INPAINT_TELEA)

    # Mild unsharp mask helps slightly soft phone photos.
    blurred = cv2.GaussianBlur(out, (0, 0), 1.5)
    return cv2.addWeighted(out, 1.5, blurred, -0.5, 0)
