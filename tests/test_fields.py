import pytest

from app.models import Status
from app.verification.fields import (
    compare_alcohol,
    compare_net_contents,
    compare_text,
    find_span,
    parse_alcohol,
    parse_volume,
)
from app.verification.normalize import canonical, loose, loose_address

# --------------------------------------------------------------------------- normalization


def test_canonical_fixes_typography():
    assert canonical("Stone’s  Throw – Gin") == "Stone's Throw - Gin"


def test_loose_ignores_case_accents_and_punctuation():
    assert loose("STONE'S THROW") == loose("Stone's Throw")
    assert loose("Côtes du Rhône") == "cotes du rhone"


def test_loose_address_expands_state_abbreviations():
    assert loose_address("Louisville, KY") == loose_address("Louisville, Kentucky")


# --------------------------------------------------------------------------- text fields


def check(expected, found=None, lines=(), **kw):
    """Compare ``expected`` against a label whose text is ``found`` plus any other ``lines``."""
    label = ([found] if found else []) + list(lines)
    return compare_text("brand_name", "Brand name", expected, label, **kw)


def test_exact_match():
    assert check("Old Tom Distillery", "Old Tom Distillery").status == Status.MATCH


def test_capitalization_only_is_a_match_with_explanation():
    # Dave's example from the interviews.
    result = check("Stone's Throw", "STONE'S THROW")
    assert result.status == Status.MATCH
    assert "capitalization" in result.message


def test_punctuation_and_spacing_only_is_a_match():
    assert check("River Bend", "RIVER.BEND").status == Status.MATCH
    assert check("Copper Kettle", "COPPERKETTLE").status == Status.MATCH


def test_near_miss_goes_to_review_with_diff():
    result = check("Golden Fields", "GOLDEN FIELD")
    assert result.status == Status.REVIEW
    assert result.diff


def test_different_name_is_a_mismatch():
    result = check("Old Tom Distillery", "Olde Tom Spirits")
    assert result.status == Status.MISMATCH
    assert result.found == "Olde Tom Spirits"


def test_unrelated_name_is_not_found():
    # OCR doesn't know which line is the brand, so nothing resembling it means "not on the label".
    assert check("Old Tom Distillery", "Blue Ridge Distilling").status == Status.MISSING


def test_review_floor_softens_misreads_on_bad_images():
    # Glare hid several letters: the normal floor fails it, the poor-image floor sends it to a person.
    assert check("OLD TOM DISTILLERY", "OLD TC DIST LL Y").status == Status.MISMATCH
    assert check("OLD TOM DISTILLERY", "OLD TC DIST LL Y", review_floor=75).status == Status.REVIEW


def test_two_misread_letters_are_nearly_identical():
    assert check("OLD TOM DISTILLERY", "OLD TCM DISTILL RY").status == Status.REVIEW


def test_glued_words_still_found():
    result = check("Stone's Throw", None, ["SMALL BATCH", "STONE'STHROW", "London Dry Gin"])
    assert result.status == Status.MATCH


def test_blank_application_field_is_skipped():
    assert check(None, "anything").status == Status.SKIPPED


def test_value_not_on_label_is_missing():
    assert check("Old Tom Distillery", None, ["45% Alc./Vol.", "750 mL"]).status == Status.MISSING


def test_found_among_other_label_text():
    result = check("Old Tom Distillery", None, ["EST. 1870", "OLD TOM DISTILLERY", "Bourbon"])
    assert result.status == Status.MATCH
    assert result.found == "OLD TOM DISTILLERY"


def test_preferred_lines_are_searched_before_small_print():
    # The brand also appears inside the bottler's name; the large-type brand line must be compared.
    lines = ["GOLDEN FIELD", "Wheat Ale", "Brewed by Golden Fields Brewing Co., Fargo, ND"]
    assert check("Golden Fields", None, lines).status == Status.MATCH  # found in small print
    assert check("Golden Fields", None, lines, preferred_lines=["GOLDEN FIELD"]).status == Status.REVIEW


def test_bottler_ignores_leading_phrase_and_state_abbreviation():
    result = compare_text(
        "bottler", "Bottler", "Old Tom Distillery, Louisville, KY",
        ["Distilled and Bottled by Old Tom Distillery, Louisville, Kentucky"], address=True,
    )
    assert result.status == Status.MATCH


def test_find_span_skips_ornament_tokens():
    span = find_span("London Dry Gin", ["■ London Dry Gin"])
    assert span.text == "London Dry Gin"


# --------------------------------------------------------------------------- alcohol


@pytest.mark.parametrize(
    "text, abv, proof",
    [
        ("45% Alc./Vol. (90 Proof)", 45, 90),
        ("Alc. 13.5% by Vol.", 13.5, None),
        ("ALC 6,5% BY VOL", 6.5, None),
        ("40% Alc./Vol.(8o Proof)", 40, 80),  # OCR read a zero as the letter o
        ("100% Agave", None, None),
    ],
)
def test_parse_alcohol(text, abv, proof):
    parsed = parse_alcohol(text)
    if abv is None:
        assert parsed is None
    else:
        assert (parsed.abv, parsed.proof) == (abv, proof)


def test_alcohol_match_and_statement_trimmed():
    result = compare_alcohol("45%", ["45% Alc./Vol. (90 Proof)     750 mL"])
    assert result.status == Status.MATCH
    assert result.found == "45% Alc./Vol. (90 Proof)"


def test_alcohol_mismatch_explains_both_values():
    result = compare_alcohol("45% Alc./Vol. (90 Proof)", ["40% Alc./Vol. (80 Proof)"])
    assert result.status == Status.MISMATCH
    assert "40%" in result.message and "45%" in result.message


def test_alcohol_proof_must_agree_with_abv():
    result = compare_alcohol("45%", ["45% Alc./Vol. (86 Proof)"])
    assert result.status == Status.MISMATCH
    assert "proof" in result.message


def test_alcohol_ignores_other_percentages():
    result = compare_alcohol("40%", ["100% AGAVE", "40% Alc./Vol. (80 Proof)"])
    assert result.status == Status.MATCH


def test_alcohol_missing():
    assert compare_alcohol("45%", ["750 mL"]).status == Status.MISSING


# --------------------------------------------------------------------------- net contents


@pytest.mark.parametrize(
    "text, ml",
    [("750 mL", 750), ("750ML", 750), ("1 L", 1000), ("1.75 Liters", 1750), ("75 cl", 750),
     ("12 FL. OZ.", 354.882), ("16 fl oz", 473.176), ("1 PINT", 473.176)],
)
def test_parse_volume(text, ml):
    assert parse_volume(text).ml == pytest.approx(ml, rel=1e-3)


def test_net_contents_exact_and_equivalent_units():
    assert compare_net_contents("750 mL", ["750 mL"]).status == Status.MATCH
    equivalent = compare_net_contents("355 mL", ["6.5% Alc./Vol.  12 FL. OZ."])
    assert equivalent.status == Status.MATCH
    assert "same volume" in equivalent.message


def test_net_contents_mismatch():
    assert compare_net_contents("750 mL", ["1 L"]).status == Status.MISMATCH


def test_net_contents_does_not_confuse_zip_or_proof():
    assert compare_net_contents("750 mL", ["Louisville, KY 40202", "(90 Proof)"]).status == Status.MISSING
