"""Reads the text on a label image with on-device OCR.

Uses RapidOCR (PP-OCR models on ONNX Runtime). The models ship inside the
rapidocr package, so reading needs no network access and no account: label
images never leave the computer running the app. It returns text lines only;
the rules engine finds each field in those lines.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

from app.imaging import enhance_for_ocr, to_bgr
from app.models import LabelReading

log = logging.getLogger(__name__)

# Every image is scaled and padded onto one fixed square canvas before OCR, so
# the text detector always sees the same input shape: its working memory is
# allocated once instead of growing with each new image size over a batch.
# That memory scales with canvas area (~430 MB at 1280 px, ~290 MB at 1024);
# 1280 leaves room for warning-statement fine print in real photos.
CANVAS = 1280


@dataclass
class _Pass:
    lines: list[str]
    boxes: list[np.ndarray]
    scores: list[float]
    image: np.ndarray
    label: str

    @property
    def strength(self) -> float:
        """Confident characters read; used to pick the best of several attempts."""
        return sum(len(t) * s for t, s in zip(self.lines, self.scores) if s >= 0.8)


class LabelOCR:
    def __init__(self, threads: int = 4) -> None:
        self._engine = None
        self._threads = threads
        self._lock = threading.Lock()

    def warm_up(self) -> None:
        """Load the models (a few seconds) so the first real request is fast."""
        with self._lock:
            self._load()
        # Warm up at the real input shape: a small image would be upscaled by
        # the detector and reserve far more memory than real labels need.
        self._ocr(np.full((CANVAS, CANVAS, 3), 255, np.uint8))

    def _load(self):
        if self._engine is None:
            from rapidocr import RapidOCR

            _disable_ort_memory_patterns()
            self._engine = RapidOCR(params={
                # "error": its warnings (e.g. no text found on a blank image) mean nothing to agents.
                "Global.log_level": "error",
                # The per-line 180-degree classifier is off: in testing it flipped
                # clean, upright lines and turned them into garbage. Whole-image
                # rotation is handled in read() instead.
                "Global.use_cls": False,
                # One line at a time: batching six long warning-statement lines
                # cost ~400 MB extra and was no faster.
                "Rec.rec_batch_num": 1,
                "EngineConfig.onnxruntime.intra_op_num_threads": self._threads,
            })
        return self._engine

    def _ocr(self, bgr: np.ndarray, label: str = "original") -> _Pass:
        with self._lock:
            result = self._load()(bgr)
        if result.txts is None:
            return _Pass([], [], [], bgr, label)
        keep = [i for i, t in enumerate(result.txts) if not _is_ornament(t)]
        return _Pass(
            [_split_glued(result.txts[i]) for i in keep], [result.boxes[i] for i in keep],
            [float(result.scores[i]) for i in keep], bgr, label,
        )

    def read(self, img: Image.Image, *, hard_to_read: bool) -> LabelReading:
        """Read the label, retrying with lighting correction or rotation when the first pass is weak."""
        started = time.perf_counter()
        bgr = to_canvas(to_bgr(img))
        best = self._ocr(bgr)
        notes: list[str] = []

        mean_conf = float(np.mean(best.scores)) if best.scores else 0.0
        if hard_to_read or mean_conf < 0.9 or best.strength < 150:
            enhanced = self._ocr(enhance_for_ocr(bgr), "enhanced")
            if enhanced.strength > best.strength * 1.05:
                best = enhanced
                notes.append("Lighting and glare correction was applied to read this image.")

        if best.strength < 60:
            for code, name in ((cv2.ROTATE_90_CLOCKWISE, "rotated 90°"), (cv2.ROTATE_180, "rotated 180°"),
                               (cv2.ROTATE_90_COUNTERCLOCKWISE, "rotated 270°")):
                attempt = self._ocr(cv2.rotate(bgr, code), name)
                if attempt.strength > best.strength * 1.5:
                    best = attempt
            if best.label.startswith("rotated"):
                notes.append(f"The image appeared to be sideways or upside down; it was read {best.label}.")

        bold, basis = estimate_header_bold(best)
        return LabelReading(
            lines=best.lines,
            line_heights=[_box_height(b) for b in best.boxes],
            warning_header_bold=bold,
            warning_bold_basis=basis,
            notes=notes,
            elapsed_ms=round((time.perf_counter() - started) * 1000),
        )


def _disable_ort_memory_patterns() -> None:
    """Stop ONNX Runtime caching a memory plan for every input shape.

    Recognition runs once per text line and every line has a different width,
    so the cached plans pile up over a batch (about 200 MB more for detection
    alone in testing). RapidOCR doesn't expose the switch, so wrap its
    session-options factory. Pinned to rapidocr 3.9.x; if the internals move,
    this quietly does nothing and OCR still works, just with more memory.
    """
    try:
        from rapidocr.inference_engine.onnxruntime.main import OrtInferSession
    except ImportError:
        return
    original = getattr(OrtInferSession, "_init_sess_opts", None)
    if original is None or getattr(original, "patched_by_label_verifier", False):
        return

    def init_sess_opts(cfg):
        opts = original(cfg)
        opts.enable_mem_pattern = False
        return opts

    init_sess_opts.patched_by_label_verifier = True
    OrtInferSession._init_sess_opts = staticmethod(init_sess_opts)


def to_canvas(bgr: np.ndarray, side: int = CANVAS) -> np.ndarray:
    """Scale the longer edge to ``side`` and pad to a square with the image's border colour."""
    h, w = bgr.shape[:2]
    scale = side / max(h, w)
    if scale != 1:
        interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
        bgr = cv2.resize(bgr, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=interpolation)
    h, w = bgr.shape[:2]
    edge = np.concatenate([bgr[0], bgr[-1], bgr[:, 0], bgr[:, -1]])
    fill = [int(v) for v in np.median(edge, axis=0)]
    return cv2.copyMakeBorder(bgr, 0, side - h, 0, side - w, cv2.BORDER_CONSTANT, value=fill)


def _box_height(box: np.ndarray) -> float:
    pts = np.asarray(box, dtype=np.float32)
    return float((np.linalg.norm(pts[0] - pts[3]) + np.linalg.norm(pts[1] - pts[2])) / 2)


_GLUED = re.compile(r"(?<=[a-z])(?=[A-Z][a-z])")


def _split_glued(text: str) -> str:
    """Put back spaces OCR dropped between words: "Product ofFrance" -> "Product of France"."""
    return _GLUED.sub(" ", text)


def _is_ornament(text: str) -> bool:
    """Decorations (diamonds, rules, flourishes) that the recognizer reads as a stray symbol."""
    return len(text.strip()) <= 3 and not re.search(r"[A-Za-z0-9]", text)


# ---------------------------------------------------------------------------
# Bold detection for "GOVERNMENT WARNING:"
# ---------------------------------------------------------------------------


def estimate_header_bold(p: _Pass) -> tuple[bool | None, str | None]:
    """Compare stroke thickness of the header words with the warning text after them.

    Bold type has noticeably thicker strokes than regular type. Returns
    (None, None) when there isn't enough to measure.
    """
    idx = next((i for i, t in enumerate(p.lines) if "GOVERNMENT" in t.upper() and "WARN" in t.upper()), None)
    if idx is None:
        return None, None
    try:
        line = _crop_line(p.image, p.boxes[idx])
        words = _word_columns(line)
        if len(words) >= 4:
            header = _stroke_ratio(line[:, words[0][0] : words[1][1]])
            body = _stroke_ratio(line[:, words[2][0] : words[-1][1]])
        elif idx + 1 < len(p.lines):  # header sits on its own line
            header = _stroke_ratio(line)
            body = _stroke_ratio(_crop_line(p.image, p.boxes[idx + 1]))
        else:
            return None, None
    except (cv2.error, ValueError, IndexError):
        log.debug("bold estimate failed", exc_info=True)
        return None, None
    if not header or not body:
        return None, None
    ratio = header / body
    basis = f"estimated from letter stroke thickness, {ratio:.1f}× the text that follows"
    if ratio >= 1.25:
        return True, basis
    if ratio <= 1.12:
        return False, basis
    return None, None


def _crop_line(bgr: np.ndarray, box: np.ndarray) -> np.ndarray:
    """Straighten a (possibly tilted) text-line box and return a binary ink mask, upscaled."""
    pts = np.asarray(box, dtype=np.float32)
    width = int(max(np.linalg.norm(pts[0] - pts[1]), np.linalg.norm(pts[3] - pts[2])))
    height = int(max(np.linalg.norm(pts[0] - pts[3]), np.linalg.norm(pts[1] - pts[2])))
    if width < 10 or height < 6:
        raise ValueError("line too small")
    dst = np.array([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32)
    crop = cv2.warpPerspective(bgr, cv2.getPerspectiveTransform(pts, dst), (width, height))
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    # Normalise every line to the same height so stroke widths are comparable across lines.
    scale = 60 / height
    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Text is the minority colour; flip so ink is white whether text is dark-on-light or light-on-dark.
    if ink.mean() > 127:
        ink = 255 - ink
    return ink


def _word_columns(ink: np.ndarray) -> list[tuple[int, int]]:
    """Horizontal extents of words, split at gaps wider than a letter space."""
    cols = ink.max(axis=0) > 0
    runs: list[list[int]] = []
    start = None
    for x, on in enumerate(cols):
        if on and start is None:
            start = x
        elif not on and start is not None:
            runs.append([start, x])
            start = None
    if start is not None:
        runs.append([start, len(cols)])
    gap = ink.shape[0] * 0.15
    words: list[list[int]] = []
    for run in runs:
        if words and run[0] - words[-1][1] < gap:
            words[-1][1] = run[1]
        else:
            words.append(run)
    return [(a, b) for a, b in words]


def _stroke_ratio(ink: np.ndarray) -> float | None:
    """Average stroke width in pixels: ink area / half its outline length.

    For thin strokes area ~ length x width and outline ~ 2 x length.
    """
    mask = (ink > 0).astype(np.uint8)
    if mask.sum() < 50:
        return None
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    outline = sum(cv2.arcLength(c, True) for c in contours)
    return 2 * float(mask.sum()) / outline if outline else None
