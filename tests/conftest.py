import json
from pathlib import Path

import pytest

SAMPLES_DIR = Path(__file__).resolve().parents[1] / "app" / "static" / "samples"


@pytest.fixture(scope="session")
def samples() -> list[dict]:
    return json.loads((SAMPLES_DIR / "samples.json").read_text())


@pytest.fixture(scope="session")
def ocr():
    from app.ocr import LabelOCR

    reader = LabelOCR()
    reader.warm_up()
    return reader
