"""End-to-end regression over the sample labels.

Each sample was built to exercise one situation from the stakeholder
interviews; this checks the whole pipeline (image -> OCR -> rules -> verdict)
gives the intended answer and stays within the 5-second budget.
"""

import json
import time

import pytest

from app.imaging import assess_quality, load_image
from app.models import ApplicationData
from app.verification.engine import verify
from tests.conftest import SAMPLES_DIR

SAMPLES = json.loads((SAMPLES_DIR / "samples.json").read_text())


@pytest.mark.parametrize("sample", SAMPLES, ids=[s["id"] for s in SAMPLES])
def test_sample_verdict(sample, ocr):
    started = time.perf_counter()
    img = load_image((SAMPLES_DIR / sample["file"]).read_bytes())
    quality = assess_quality(img)
    reading = ocr.read(img, hard_to_read=bool(quality.issues))
    elapsed = time.perf_counter() - started
    result = verify(ApplicationData(**sample["application"]), reading, quality, round(elapsed * 1000))

    problems = [f"{c.name}: {c.status.value} ({c.message})" for c in result.checks if c.status.value != "match"]
    assert result.verdict.value in sample["acceptable"], "\n".join(problems)
    assert elapsed < 5, f"took {elapsed:.1f}s"


def test_title_case_warning_is_caught(ocr):
    img = load_image((SAMPLES_DIR / "copper-kettle-rum.png").read_bytes())
    sample = next(s for s in SAMPLES if s["id"] == "copper-kettle-rum")
    result = verify(ApplicationData(**sample["application"]), ocr.read(img, hard_to_read=False), assess_quality(img), 0)
    warning = next(c for c in result.checks if c.key == "government_warning")
    assert "capital letters" in warning.message


def test_bold_header_estimates(ocr):
    bold = ocr.read(load_image((SAMPLES_DIR / "old-tom-bourbon.png").read_bytes()), hard_to_read=False)
    plain = ocr.read(load_image((SAMPLES_DIR / "blue-ridge-rye.png").read_bytes()), hard_to_read=False)
    assert bold.warning_header_bold is True
    assert plain.warning_header_bold is False
