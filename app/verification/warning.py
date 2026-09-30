"""Government Health Warning Statement check (27 CFR part 16).

The statement must appear word for word, "GOVERNMENT WARNING" must be in
capital letters and bold type. The rest of the statement may be in any case.
"""

from __future__ import annotations

import difflib

from rapidfuzz import fuzz

from app.models import DiffToken, FieldCheck, LabelReading, Status, SubCheck
from app.verification.normalize import canonical, loose

STANDARD_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink alcoholic "
    "beverages during pregnancy because of the risk of birth defects. (2) Consumption of alcoholic "
    "beverages impairs your ability to drive a car or operate machinery, and may cause health problems."
)

_SEVERITY = [Status.MATCH, Status.SKIPPED, Status.REVIEW, Status.MISMATCH, Status.MISSING]


def worst(statuses: list[Status]) -> Status:
    return max(statuses, key=_SEVERITY.index, default=Status.MATCH)


def locate_warning(lines: list[str]) -> str | None:
    """Find the warning in the OCR lines; returns the text from its start onward."""
    text = canonical(" ".join(lines))
    low = text.lower()
    for anchor in ("government warning", "according to the surgeon general"):
        idx = low.find(anchor)
        if idx < 0:
            alignment = fuzz.partial_ratio_alignment(anchor, low)
            idx = alignment.dest_start if alignment and alignment.score >= 85 else -1
        if idx >= 0:
            # Back up to the start of the word the anchor landed in.
            while idx > 0 and not low[idx - 1].isspace():
                idx -= 1
            return text[idx:]
    return None


def _tokens(text: str) -> list[tuple[str, str]]:
    """(original, comparison key) pairs; pure punctuation tokens are dropped."""
    pairs = [(tok, loose(tok).replace(" ", "")) for tok in text.split()]
    return [(tok, key) for tok, key in pairs if key]


def check_warning(reading: LabelReading) -> FieldCheck:
    name = "Government warning"
    found_text = locate_warning(reading.lines)
    if not found_text:
        return FieldCheck(
            key="government_warning", name=name, status=Status.MISSING, expected=STANDARD_WARNING,
            message="No government health warning statement was found on the label. It is required on all alcohol beverages.",
        )

    exp = _tokens(STANDARD_WARNING)
    got = _tokens(found_text)
    exp_keys = [k for _, k in exp]

    def align(tokens: list[tuple[str, str]]) -> list[tuple[str, int, int, int, int]]:
        return difflib.SequenceMatcher(None, exp_keys, [k for _, k in tokens], autojunk=False).get_opcodes()

    # The label's text runs on past the warning; cut it after the last word that lines up.
    last = max((j2 for op, _, _, _, j2 in align(got) if op == "equal"), default=len(got))
    got = got[:last]
    opcodes = align(got)
    found_display = " ".join(tok for tok, _ in got)

    diff: list[DiffToken] = []
    header_tokens: dict[int, str] = {}
    changed_pairs: list[tuple[str, str]] = []
    n_missing = n_extra = 0
    for op, i1, i2, j1, j2 in opcodes:
        if op == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                e_tok, g_tok = exp[i][0], got[j][0]
                if i < 2:
                    header_tokens[i] = g_tok
                    if e_tok.rstrip(":") != g_tok.rstrip(":"):  # header must also match in case
                        diff.append(DiffToken(op="changed", expected=e_tok, found=g_tok))
                        continue
                diff.append(DiffToken(op="equal", expected=e_tok, found=g_tok))
        elif op == "delete":
            n_missing += i2 - i1
            diff.extend(DiffToken(op="missing", expected=exp[i][0]) for i in range(i1, i2))
        elif op == "insert":
            n_extra += j2 - j1
            diff.extend(DiffToken(op="extra", found=got[j][0]) for j in range(j1, j2))
        else:
            e = " ".join(t for t, _ in exp[i1:i2])
            g = " ".join(t for t, _ in got[j1:j2])
            changed_pairs.append((e, g))
            diff.append(DiffToken(op="changed", expected=e, found=g))

    subs = [
        _header_caps(header_tokens),
        _wording(n_missing, n_extra, changed_pairs),
        _header_bold(reading),
    ]
    status = worst([s.status for s in subs])
    problems = [s.message for s in subs if s.status != Status.MATCH]
    message = " ".join(problems) if problems else "Present, word for word, with the header in capital letters and bold."
    return FieldCheck(
        key="government_warning", name=name, status=status, expected=STANDARD_WARNING,
        found=found_display, message=message, diff=diff, sub_checks=subs,
    )


def _header_caps(header_tokens: dict[int, str]) -> SubCheck:
    name = "“GOVERNMENT WARNING:” in capital letters"
    if len(header_tokens) < 2:
        return SubCheck(name=name, status=Status.MISSING, message="The words “GOVERNMENT WARNING” were not found at the start of the statement.")
    first, second = header_tokens[0], header_tokens[1]
    printed = f"{first} {second}"
    if not (first.isupper() and second.rstrip(":").isupper()):
        return SubCheck(
            name=name, status=Status.MISMATCH,
            message=f"“GOVERNMENT WARNING” must be in capital letters. The label shows “{printed}”.",
        )
    if not second.endswith(":"):
        # A small colon is easy for OCR to miss, so a person confirms it.
        return SubCheck(
            name=name, status=Status.REVIEW,
            message="The colon after “GOVERNMENT WARNING” appears to be missing. Please confirm by eye.",
        )
    return SubCheck(name=name, status=Status.MATCH, message="Header is in capital letters.")


def _wording(n_missing: int, n_extra: int, changed: list[tuple[str, str]]) -> SubCheck:
    name = "Wording is exact"
    if not (n_missing or n_extra or changed):
        return SubCheck(name=name, status=Status.MATCH, message="Wording matches the required statement word for word.")
    n_changed = sum(len(e.split()) for e, _ in changed)
    total = n_missing + n_extra + n_changed
    summary = f"{total} word{'s' if total != 1 else ''} differ{'s' if total == 1 else ''} from the required statement."
    # A letter or two off in fine print is more likely an OCR misread than an altered warning.
    looks_like_misread = n_missing + n_extra <= 2 and all(fuzz.ratio(loose(e), loose(g)) >= 75 for e, g in changed)
    if looks_like_misread:
        return SubCheck(
            name=name, status=Status.REVIEW,
            message=f"{summary} The differences are small and may be misreads of fine print. Please compare.",
        )
    return SubCheck(name=name, status=Status.MISMATCH, message=f"The wording is not exact: {summary}")


def _header_bold(reading: LabelReading) -> SubCheck:
    name = "“GOVERNMENT WARNING:” in bold"
    basis = f" ({reading.warning_bold_basis})" if reading.warning_bold_basis else ""
    if reading.warning_header_bold is True:
        return SubCheck(name=name, status=Status.MATCH, message=f"Header appears to be in bold type{basis}.")
    if reading.warning_header_bold is False:
        return SubCheck(
            name=name, status=Status.REVIEW,
            message=f"The header does not appear to be bold{basis}. Please confirm by eye.",
        )
    return SubCheck(name=name, status=Status.REVIEW, message="Couldn't tell whether the header is bold. Please confirm by eye.")
