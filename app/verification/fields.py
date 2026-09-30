"""Comparators for the individual label fields.

OCR returns lines of text with no idea which line is which field, so every
comparator takes the application value and searches the label's lines for it.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from app.models import DiffToken, FieldCheck, Status
from app.verification.normalize import canonical, casefolded, loose, loose_address

# ---------------------------------------------------------------------------
# Locating a value in the transcription
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"\S+")
_HAS_ALNUM = re.compile(r"[A-Za-z0-9]")
_EDGE_PUNCT = " ,;:.-|/\\*•·"


@dataclass(frozen=True)
class Span:
    text: str
    score: float  # 0-100 similarity to what we were looking for


def find_span(expected: str, lines: list[str], *, max_join: int = 3, address: bool = False) -> Span | None:
    """Find the run of words on the label that best matches ``expected``.

    Windows may cross line breaks (up to ``max_join`` lines) because labels
    often wrap a name or address over two lines.
    """
    norm = loose_address if address else loose
    target = norm(expected)
    n = len(target.split())
    if not n:
        return None

    def score(text: str) -> float:
        found = norm(text)
        # OCR often drops the space between words ("STONE'STHROW"), so also compare without spaces.
        return max(fuzz.ratio(target, found), fuzz.ratio(target.replace(" ", ""), found.replace(" ", "")))

    best: Span | None = None
    for i in range(len(lines)):
        joined = " ".join(lines[i : i + max_join])
        tokens = [t for t in _TOKEN.finditer(joined)]
        for start in range(len(tokens)):
            if not _HAS_ALNUM.search(tokens[start].group()):
                continue
            for size in range(1, n + 2):
                end = start + size
                if end > len(tokens):
                    break
                if not _HAS_ALNUM.search(tokens[end - 1].group()):
                    continue
                text = joined[tokens[start].start() : tokens[end - 1].end()].strip(_EDGE_PUNCT)
                s = score(text)
                # On a tie prefer the shorter span: less stray text around the value.
                if best is None or (s, -len(text)) > (best.score, -len(best.text)):
                    best = Span(text, s)
    return best


def _pick(
    expected: str, lines: list[str], *, address: bool = False, preferred_lines: list[str] | None = None,
) -> str | None:
    """Find the label's version of a field.

    Looks in ``preferred_lines`` first (large type, for a brand name), then
    anywhere on the label. The order matters: a brand name usually also appears
    inside the bottler's name in small print, and matching that instead would
    hide a real difference in the brand name itself.
    """
    for pool in (preferred_lines or [], lines):
        span = find_span(expected, pool, address=address) if pool else None
        if span and span.score >= 60:
            return span.text
    return None


def word_diff(expected: str, found: str) -> list[DiffToken]:
    """Word-level diff, case-insensitive, for showing agents what differs."""
    exp_words = expected.split()
    found_words = found.split()
    matcher = difflib.SequenceMatcher(
        None, [loose(w) for w in exp_words], [loose(w) for w in found_words], autojunk=False
    )
    out: list[DiffToken] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            out.extend(DiffToken(op="equal", expected=e, found=f) for e, f in zip(exp_words[i1:i2], found_words[j1:j2]))
        elif op == "delete":
            out.extend(DiffToken(op="missing", expected=e) for e in exp_words[i1:i2])
        elif op == "insert":
            out.extend(DiffToken(op="extra", found=f) for f in found_words[j1:j2])
        else:
            out.append(DiffToken(op="changed", expected=" ".join(exp_words[i1:i2]), found=" ".join(found_words[j1:j2])))
    return out


# ---------------------------------------------------------------------------
# Free-text fields: brand, class/type, bottler, country of origin
# ---------------------------------------------------------------------------


def compare_text(
    key: str,
    name: str,
    expected: str | None,
    lines: list[str],
    *,
    address: bool = False,
    preferred_lines: list[str] | None = None,
    review_floor: float = 85,
) -> FieldCheck:
    """Compare a free-text field.

    ``review_floor`` is the similarity (0-100) above which a difference is sent
    for review rather than failed. The engine lowers it for poor images, so
    misreads go to a person instead of being rejected.
    """
    if not expected:
        return FieldCheck(key=key, name=name, status=Status.SKIPPED, message="Not provided in the application, so not checked.")

    found = _pick(expected, lines, address=address, preferred_lines=preferred_lines)
    if not found:
        return FieldCheck(
            key=key, name=name, status=Status.MISSING, expected=expected,
            message=f"Could not find the {name.lower()} on the label.",
        )

    check = FieldCheck(key=key, name=name, status=Status.MATCH, expected=expected, found=found, message="")

    if canonical(expected) == canonical(found):
        check.message = "Matches the application exactly."
        return check
    if casefolded(expected) == casefolded(found):
        check.message = "Matches. Only the capitalization differs, which is normal label styling."
        return check

    norm = loose_address if address else loose
    if norm(expected).replace(" ", "") == norm(found).replace(" ", ""):
        if address and loose(expected) != loose(found):
            check.message = "Matches. The label uses a state abbreviation (or the full name) where the application doesn't."
            return check
        check.message = "Matches. Only punctuation, spacing or accents differ."
        return check

    score = max(
        fuzz.ratio(norm(expected), norm(found)),
        fuzz.ratio(norm(expected).replace(" ", ""), norm(found).replace(" ", "")),
    )
    check.diff = word_diff(expected, found)
    if score >= 90:
        check.status = Status.REVIEW
        check.message = "Nearly identical. This could be a small typo on the label or a misread. Please compare."
    elif score >= review_floor:
        check.status = Status.REVIEW
        check.message = "Close, but parts may not have been read correctly. Please compare by eye."
    else:
        check.status = Status.MISMATCH
        check.message = "The label does not match the application."
    return check


# ---------------------------------------------------------------------------
# Alcohol content
# ---------------------------------------------------------------------------

_ABV = re.compile(r"(?<![\d.,])(\d{1,2}(?:[.,]\d{1,2})?)\s*%")
_PROOF = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{1,2})?)\s*°?\s*proof", re.IGNORECASE)
_ALC_WORDS = re.compile(r"alc|vol|abv|alcohol", re.IGNORECASE)
# The whole alcohol statement, e.g. "Alc. 13.5% by Vol." or "45% Alc./Vol. (90 Proof)", for display.
_ALC_STATEMENT = re.compile(
    r"(?:alc(?:ohol)?\.?\s*)?(?<![\d.,])\d{1,2}(?:[.,]\d{1,2})?\s*%"
    r"(?:\s*(?:alc(?:ohol)?\.?\s*(?:/|by)?\s*vol(?:ume)?\.?|abv|by\s+vol(?:ume)?\.?))?"
    r"(?:\s*\(?\s*\d{1,3}(?:[.,]\d{1,2})?\s*°?\s*proof\s*\)?)?",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d{1,3}(?:[.,]\d{1,2})?")


def _num(s: str) -> float:
    return float(s.replace(",", "."))


_LETTER_O_IN_NUMBER = re.compile(r"(?<=\d)[oO]|[oO](?=\d)")


def fix_digits(text: str) -> str:
    """OCR often reads a zero as the letter O inside numbers ("8o Proof")."""
    return _LETTER_O_IN_NUMBER.sub("0", text)


@dataclass(frozen=True)
class Alcohol:
    abv: float | None
    proof: float | None

    def describe(self) -> str:
        parts = []
        if self.abv is not None:
            parts.append(f"{self.abv:g}%")
        if self.proof is not None:
            parts.append(f"{self.proof:g} proof")
        return " / ".join(parts)


def parse_alcohol(text: str | None, *, lenient: bool = False) -> Alcohol | None:
    """Pull ABV and proof out of a statement like "45% Alc./Vol. (90 Proof)".

    ``lenient`` accepts a bare number as the ABV ("45"), for application input.
    """
    if not text:
        return None
    text = fix_digits(text)
    abv_m = _ABV.search(text)
    proof_m = _PROOF.search(text)
    abv = _num(abv_m.group(1)) if abv_m else None
    proof = _num(proof_m.group(1)) if proof_m else None
    if abv is None and lenient and proof is None:
        bare = _NUMBER.search(text)
        abv = _num(bare.group(0)) if bare else None
    if abv is None and proof is None:
        return None
    return Alcohol(abv, proof)


def _alcohol_statements(lines: list[str]) -> list[str]:
    """Candidate alcohol statements, best first."""
    candidates: list[tuple[int, str]] = []
    lines = [fix_digits(line) for line in lines]
    for i, line in enumerate(lines):
        if not (_ABV.search(line) or _PROOF.search(line)):
            continue
        text = line
        # "45% Alc./Vol." and "(90 Proof)" are often on separate lines
        if i + 1 < len(lines) and _PROOF.search(lines[i + 1]) and not _PROOF.search(line):
            text = f"{line} {lines[i + 1]}"
        weight = 2 if _ALC_WORDS.search(text) else 0
        candidates.append((weight, text))
    candidates.sort(key=lambda c: -c[0])
    return [c[1] for c in candidates]


def compare_alcohol(expected: str | None, lines: list[str]) -> FieldCheck:
    key, name = "alcohol_content", "Alcohol content"
    if not expected:
        return FieldCheck(key=key, name=name, status=Status.SKIPPED, message="Not provided in the application, so not checked.")
    want = parse_alcohol(expected, lenient=True)
    if want is None:
        return FieldCheck(
            key=key, name=name, status=Status.REVIEW, expected=expected,
            message="Couldn't read a percentage in the application value, so this needs a manual check.",
        )

    statements = _alcohol_statements(lines)
    if not statements:
        return FieldCheck(
            key=key, name=name, status=Status.MISSING, expected=expected,
            message="No alcohol content statement found on the label.",
        )

    # Prefer a statement whose ABV agrees with the application, in case the label has other percentages.
    parsed = [(s, parse_alcohol(s)) for s in statements]
    found_text, got = next(((s, a) for s, a in parsed if a and want.abv is not None and a.abv == want.abv), parsed[0])
    assert got is not None
    statement = _ALC_STATEMENT.search(found_text)
    if statement and got.abv is not None:
        found_text = statement.group(0).strip()
    check = FieldCheck(key=key, name=name, status=Status.MATCH, expected=expected, found=found_text, message="")

    problems: list[str] = []
    if want.abv is not None and got.abv is not None and abs(want.abv - got.abv) > 1e-6:
        problems.append(f"The label says {got.abv:g}% but the application says {want.abv:g}%.")
    if want.abv is not None and got.abv is None:
        problems.append("The label gives proof but no percentage by volume.")
    if got.abv is not None and got.proof is not None and abs(got.proof - 2 * got.abv) > 0.5:
        problems.append(f"The label's proof ({got.proof:g}) doesn't agree with its ABV ({got.abv:g}% is {2 * got.abv:g} proof).")
    if want.proof is not None and got.proof is not None and abs(want.proof - got.proof) > 1e-6:
        problems.append(f"The label says {got.proof:g} proof but the application says {want.proof:g} proof.")

    if problems:
        check.status = Status.MISMATCH
        check.message = " ".join(problems)
    else:
        check.message = f"Matches ({got.describe()})."
    return check


# ---------------------------------------------------------------------------
# Net contents
# ---------------------------------------------------------------------------

_UNITS: list[tuple[str, float, str]] = [
    (r"m\.?\s?l\.?|millilit(?:er|re)s?", 1.0, "metric"),
    (r"c\.?\s?l\.?|centilit(?:er|re)s?", 10.0, "metric"),
    (r"lit(?:er|re)s?|ltr|l\.?", 1000.0, "metric"),
    (r"(?:u\.?s\.?\s)?fl\.?\s?oz\.?|fluid\s+ounces?|oz\.?", 29.5735, "us"),
    (r"pints?|pt\.?", 473.176, "us"),
    (r"quarts?|qt\.?", 946.353, "us"),
    (r"gallons?|gal\.?", 3785.41, "us"),
]
_NET = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(" + "|".join(f"(?:{u[0]})" for u in _UNITS) + r")(?![a-z])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Volume:
    ml: float
    system: str
    text: str


def parse_volume(text: str | None) -> Volume | None:
    if not text:
        return None
    m = _NET.search(fix_digits(text))
    if not m:
        return None
    amount = _num(m.group(1))
    unit_text = m.group(2)
    for pattern, factor, system in _UNITS:
        if re.fullmatch(pattern, unit_text, re.IGNORECASE):
            return Volume(amount * factor, system, m.group(0))
    return None


def compare_net_contents(expected: str | None, lines: list[str]) -> FieldCheck:
    key, name = "net_contents", "Net contents"
    if not expected:
        return FieldCheck(key=key, name=name, status=Status.SKIPPED, message="Not provided in the application, so not checked.")
    want = parse_volume(expected)
    if want is None:
        return FieldCheck(
            key=key, name=name, status=Status.REVIEW, expected=expected,
            message="Couldn't read a volume (like 750 mL) in the application value, so this needs a manual check.",
        )

    candidates = [fix_digits(line) for line in lines]
    found: list[tuple[str, Volume]] = []
    for c in candidates:
        for m in _NET.finditer(c):
            vol = parse_volume(m.group(0))
            if vol:
                found.append((m.group(0), vol))
    if not found:
        return FieldCheck(
            key=key, name=name, status=Status.MISSING, expected=expected,
            message="No net contents statement (like 750 mL) found on the label.",
        )

    def agrees(v: Volume) -> bool:
        tolerance = 0.01 if v.system == want.system else want.ml * 0.015
        return abs(v.ml - want.ml) <= tolerance

    exact = next(((t, v) for t, v in found if agrees(v) and v.system == want.system), None)
    if exact:
        return FieldCheck(key=key, name=name, status=Status.MATCH, expected=expected, found=exact[0], message="Matches.")
    equivalent = next(((t, v) for t, v in found if agrees(v)), None)
    if equivalent:
        return FieldCheck(
            key=key, name=name, status=Status.MATCH, expected=expected, found=equivalent[0],
            message=f"Matches. {equivalent[1].text} is the same volume as {want.text} (about {want.ml:.0f} mL).",
        )
    text, vol = found[0]
    return FieldCheck(
        key=key, name=name, status=Status.MISMATCH, expected=expected, found=text,
        message=f"The label says {vol.text} (about {vol.ml:.0f} mL) but the application says {want.text}.",
    )
