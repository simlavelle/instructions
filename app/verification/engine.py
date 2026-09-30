"""Turns an application plus a label reading into a verdict.

OCR only reads the label. Every pass/fail decision is made here, by plain
rules that can be read, tested, and explained to an agent.
"""

from __future__ import annotations

from collections import Counter

from app.models import ApplicationData, FieldCheck, ImageQuality, LabelReading, Status, Verdict, VerificationResult
from app.verification.fields import compare_alcohol, compare_net_contents, compare_text
from app.verification.warning import check_warning


def review_floor(quality: ImageQuality) -> float:
    """How similar a misread must be to go to review instead of failing.

    On a glary, blurry or small image, OCR misreads more, so near-misses get
    more room: a person can settle a misread in seconds, while a false
    rejection costs the applicant a resubmission.
    """
    return 75 if quality.issues else 85


def run_checks(app: ApplicationData, reading: LabelReading, quality: ImageQuality) -> list[FieldCheck]:
    lines = reading.lines
    floor = review_floor(quality)
    checks = [
        compare_text(
            "brand_name", "Brand name", app.brand_name, lines,
            preferred_lines=reading.prominent_lines(), review_floor=floor,
        ),
        compare_text("class_type", "Class / type", app.class_type, lines, review_floor=floor),
        compare_alcohol(app.alcohol_content, lines),
        compare_net_contents(app.net_contents, lines),
        compare_text(
            "bottler", "Bottler / producer name and address", app.bottler, lines, address=True, review_floor=floor,
        ),
    ]
    if app.country_of_origin:
        checks.append(
            compare_text("country_of_origin", "Country of origin", app.country_of_origin, lines, review_floor=floor)
        )
    checks.append(check_warning(reading))
    return checks


def verify(app: ApplicationData, reading: LabelReading, quality: ImageQuality, total_ms: int) -> VerificationResult:
    app = app.cleaned()
    checks = run_checks(app, reading, quality)
    counts = Counter(c.status.value for c in checks)
    n_fail = counts[Status.MISMATCH.value] + counts[Status.MISSING.value]
    n_review = counts[Status.REVIEW.value]

    if n_fail:
        verdict = Verdict.FAIL
        headline = f"{n_fail} problem{'s' if n_fail != 1 else ''} found"
    elif n_review:
        verdict = Verdict.REVIEW
        headline = f"No problems found, but {n_review} item{'s' if n_review != 1 else ''} need{'s' if n_review == 1 else ''} a quick look"
    else:
        verdict = Verdict.PASS
        headline = "Everything matches the application"

    notes = list(reading.notes)
    if counts[Status.MISSING.value] >= 3 and quality.issues:
        notes.append(
            "Several items could not be found and the image has quality problems. "
            "Consider asking the applicant for a clearer image before rejecting."
        )

    return VerificationResult(
        verdict=verdict,
        headline=headline,
        counts=dict(counts),
        checks=checks,
        elapsed_ms=total_ms,
        image_quality=quality,
        notes=notes,
        transcript=reading.lines,
    )
