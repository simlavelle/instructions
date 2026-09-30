from app.models import LabelReading, Status
from app.verification.warning import STANDARD_WARNING, check_warning

BODY = STANDARD_WARNING.removeprefix("GOVERNMENT WARNING: ")


def run(text=None, lines=(), bold=True):
    """Check a label whose OCR text is ``lines`` followed by ``text``."""
    reading = LabelReading(lines=list(lines) + ([text] if text else []), warning_header_bold=bold)
    return check_warning(reading)


def sub(result, fragment):
    return next(s for s in result.sub_checks if fragment in s.name)


def test_exact_warning_passes():
    result = run(STANDARD_WARNING)
    assert result.status == Status.MATCH
    assert all(t.op == "equal" for t in result.diff)


def test_body_may_be_all_caps():
    assert run(STANDARD_WARNING.upper()).status == Status.MATCH


def test_title_case_header_fails():
    # Jenny's example from the interviews.
    result = run("Government Warning: " + BODY)
    assert result.status == Status.MISMATCH
    assert sub(result, "capital").status == Status.MISMATCH
    assert "Government Warning" in sub(result, "capital").message


def test_missing_colon_needs_review():
    # OCR easily misses a small colon, so a person confirms it.
    assert sub(run("GOVERNMENT WARNING " + BODY), "capital").status == Status.REVIEW


def test_changed_wording_fails_with_word_diff():
    text = STANDARD_WARNING.replace("may cause health problems", "may be bad for you")
    result = run(text)
    assert result.status == Status.MISMATCH
    changed = [t for t in result.diff if t.op != "equal"]
    assert any("health" in t.expected for t in changed)


def test_missing_sentence_fails():
    text = STANDARD_WARNING.replace(" because of the risk of birth defects", "")
    result = run(text)
    assert sub(result, "Wording").status == Status.MISMATCH
    assert [t.expected for t in result.diff if t.op == "missing"] == ["because", "of", "the", "risk", "of", "birth", "defects."]


def test_no_warning_is_missing():
    result = run(None, lines=["OLD TOM DISTILLERY", "750 mL"])
    assert result.status == Status.MISSING


def test_warning_located_in_ocr_lines_and_trailing_text_trimmed():
    lines = ["750 mL", STANDARD_WARNING[:80], STANDARD_WARNING[80:], "Please recycle"]
    result = run(None, lines=lines)
    assert result.status == Status.MATCH
    assert "recycle" not in result.found


def test_small_ocr_misreads_go_to_review_not_fail():
    text = STANDARD_WARNING.replace("machinery", "machinerv").replace("pregnancy", "preqnancy")
    result = run(text)
    assert sub(result, "Wording").status == Status.REVIEW


def test_header_not_bold_needs_review():
    result = run(STANDARD_WARNING, bold=False)
    assert result.status == Status.REVIEW
    assert sub(result, "bold").status == Status.REVIEW


def test_unknown_bold_needs_review():
    assert sub(run(STANDARD_WARNING, bold=None), "bold").status == Status.REVIEW
