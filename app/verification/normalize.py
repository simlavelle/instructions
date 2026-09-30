"""Text normalization used by every comparison.

Three levels, from strictest to loosest:

* ``canonical`` - fixes typography only (curly quotes, dashes, spacing).
* ``casefolded`` - canonical, ignoring capitalization.
* ``loose`` - letters and digits only, ignoring capitalization, accents and punctuation.

Comparing at each level in turn lets the rules say *how* two strings differ,
e.g. "STONE'S THROW" vs "Stone's Throw" differ only in capitalization.
"""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

_TYPOGRAPHY = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "ʼ": "'",
        "`": "'",
        "´": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        " ": " ",
    }
)

_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^0-9a-z]+")

# Postal abbreviations, so "Louisville, KY" matches "Louisville, Kentucky".
US_STATES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california",
    "co": "colorado", "ct": "connecticut", "de": "delaware", "fl": "florida", "ga": "georgia",
    "hi": "hawaii", "id": "idaho", "il": "illinois", "in": "indiana", "ia": "iowa",
    "ks": "kansas", "ky": "kentucky", "la": "louisiana", "me": "maine", "md": "maryland",
    "ma": "massachusetts", "mi": "michigan", "mn": "minnesota", "ms": "mississippi",
    "mo": "missouri", "mt": "montana", "ne": "nebraska", "nv": "nevada", "nh": "new hampshire",
    "nj": "new jersey", "nm": "new mexico", "ny": "new york", "nc": "north carolina",
    "nd": "north dakota", "oh": "ohio", "ok": "oklahoma", "or": "oregon", "pa": "pennsylvania",
    "ri": "rhode island", "sc": "south carolina", "sd": "south dakota", "tn": "tennessee",
    "tx": "texas", "ut": "utah", "vt": "vermont", "va": "virginia", "wa": "washington",
    "wv": "west virginia", "wi": "wisconsin", "wy": "wyoming", "dc": "district of columbia",
}


def canonical(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_TYPOGRAPHY)
    return _WS.sub(" ", text).strip()


def casefolded(text: str) -> str:
    return canonical(text).casefold()


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def loose(text: str) -> str:
    return _NON_ALNUM.sub(" ", strip_accents(casefolded(text))).strip()


def loose_address(text: str) -> str:
    """Loose form with state abbreviations expanded (only safe for addresses)."""
    words = [US_STATES.get(w, w) for w in loose(text).split()]
    return " ".join(words)


def similarity(a: str, b: str) -> float:
    """0-100 similarity of the loose forms."""
    return fuzz.ratio(loose(a), loose(b))


def words(text: str) -> list[str]:
    """Word tokens with punctuation removed, preserving original capitalization."""
    return re.findall(r"[0-9A-Za-z]+(?:'[A-Za-z]+)?", canonical(text))
