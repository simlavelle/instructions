from app.models import ApplicationData, ImageQuality, LabelReading, Verdict
from app.verification.engine import review_floor, verify
from app.verification.warning import STANDARD_WARNING

APP = ApplicationData(
    brand_name="OLD TOM DISTILLERY",
    class_type="Kentucky Straight Bourbon Whiskey",
    alcohol_content="45% Alc./Vol. (90 Proof)",
    net_contents="750 mL",
    bottler="Old Tom Distillery, Louisville, KY",
)

LABEL = {
    "brand": "OLD TOM DISTILLERY",
    "class": "Kentucky Straight Bourbon Whiskey",
    "alcohol": "45% Alc./Vol. (90 Proof)",
    "net": "750 mL",
    "bottler": "Distilled and Bottled by Old Tom Distillery, Louisville, Kentucky",
    "warning": STANDARD_WARNING,
}


def reading(bold=True, **changes) -> LabelReading:
    lines = {**LABEL, **changes}
    return LabelReading(lines=[v for v in lines.values() if v], warning_header_bold=bold)


def run(r: LabelReading, *, issues=(), app=APP):
    return verify(app, r, ImageQuality(width=1, height=1, issues=list(issues)), total_ms=5)


def test_everything_matches():
    result = run(reading())
    assert result.verdict == Verdict.PASS
    assert result.headline == "Everything matches the application"


def test_one_mismatch_fails():
    result = run(reading(alcohol="40% Alc./Vol. (80 Proof)"))
    assert result.verdict == Verdict.FAIL
    assert result.counts["mismatch"] == 1
    assert result.headline == "1 problem found"


def test_review_only_is_review():
    assert run(reading(bold=False)).verdict == Verdict.REVIEW


def test_blank_application_fields_are_skipped_not_failed():
    result = run(reading(), app=ApplicationData(brand_name="OLD TOM DISTILLERY", class_type="  "))
    assert result.verdict == Verdict.PASS
    assert result.counts["skipped"] == 4


def test_country_checked_only_when_given():
    with_country = run(reading(), app=APP.model_copy(update={"country_of_origin": "France"}))
    assert any(c.key == "country_of_origin" and c.status.value == "missing" for c in with_country.checks)
    assert all(c.key != "country_of_origin" for c in run(reading()).checks)


def test_review_floor_is_lower_for_poor_images():
    assert review_floor(ImageQuality(width=1, height=1)) == 85
    assert review_floor(ImageQuality(width=1, height=1, issues=["glare"])) == 75


def test_poor_image_with_many_missing_fields_suggests_new_image():
    result = run(LabelReading(lines=["???"]), issues=["The image looks blurry."])
    assert any("clearer image" in n for n in result.notes)
