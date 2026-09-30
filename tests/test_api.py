import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from tests.conftest import SAMPLES_DIR

OLD_TOM = {
    "brand_name": "OLD TOM DISTILLERY",
    "class_type": "Kentucky Straight Bourbon Whiskey",
    "alcohol_content": "45% Alc./Vol. (90 Proof)",
    "net_contents": "750 mL",
    "bottler": "Old Tom Distillery, Louisville, KY",
}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def post(client, image_bytes, application, name="label.png"):
    return client.post(
        "/api/verify",
        files={"image": (name, image_bytes, "image/png")},
        data={"application": application if isinstance(application, str) else json.dumps(application)},
    )


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_index_served_with_security_headers(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Label Verification" in response.text
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_verify_sample_label(client):
    response = post(client, (SAMPLES_DIR / "old-tom-bourbon.png").read_bytes(), OLD_TOM)
    assert response.status_code == 200
    result = response.json()
    assert result["verdict"] == "pass"
    assert [c["key"] for c in result["checks"]] == [
        "brand_name", "class_type", "alcohol_content", "net_contents", "bottler", "government_warning",
    ]
    assert result["elapsed_ms"] < 5000


def test_not_an_image(client):
    response = post(client, b"%PDF-1.4 not an image", OLD_TOM, name="label.pdf")
    assert response.status_code == 400
    assert "isn't an image" in response.json()["detail"]


def test_empty_file(client):
    assert post(client, b"", OLD_TOM).status_code == 400


def test_missing_brand_name(client):
    response = post(client, _png(), {"class_type": "Vodka"})
    assert response.status_code == 422
    assert response.json()["detail"] == "Brand name is required."


def test_bad_json(client):
    response = post(client, _png(), "{not json")
    assert response.status_code == 422


def test_blank_image_reports_missing_items_not_a_crash(client):
    result = post(client, _png(), OLD_TOM).json()
    assert result["verdict"] == "fail"
    assert result["counts"]["missing"] >= 5


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(buf, format="PNG")
    return buf.getvalue()
