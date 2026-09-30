"""Data shapes shared by the OCR reader, the rules engine, and the API."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Status(str, Enum):
    """Outcome of a single check. Ordered from best to worst."""

    MATCH = "match"  # label agrees with the application
    REVIEW = "review"  # probably fine, but a person should look
    MISMATCH = "mismatch"  # label disagrees with the application
    MISSING = "missing"  # required item not found on the label
    SKIPPED = "skipped"  # nothing to compare (field left blank in the application)


class Verdict(str, Enum):
    PASS = "pass"
    REVIEW = "review"
    FAIL = "fail"


class ApplicationData(BaseModel):
    """What the applicant declared on the COLA form. Only brand name is required."""

    brand_name: str = Field(min_length=1, max_length=200)
    class_type: str | None = Field(default=None, max_length=200)
    alcohol_content: str | None = Field(default=None, max_length=100)
    net_contents: str | None = Field(default=None, max_length=100)
    bottler: str | None = Field(default=None, max_length=400, description="Name and address of bottler/producer/importer")
    country_of_origin: str | None = Field(default=None, max_length=100, description="Required for imports only")

    def cleaned(self) -> ApplicationData:
        """Treat blank strings as 'not provided'."""
        data = {k: (v.strip() or None) if isinstance(v, str) else v for k, v in self.model_dump().items()}
        data["brand_name"] = data["brand_name"] or self.brand_name
        return ApplicationData(**data)


class LabelReading(BaseModel):
    """What OCR read off the label image: text lines, top to bottom."""

    lines: list[str] = Field(default_factory=list)
    line_heights: list[float] = Field(default_factory=list)  # text height of each line, in pixels
    warning_header_bold: bool | None = None  # None means it couldn't be measured
    warning_bold_basis: str | None = None  # how the bold judgement was made, shown to the agent
    notes: list[str] = Field(default_factory=list)  # e.g. "the image was read rotated 90°"
    elapsed_ms: int = 0

    def prominent_lines(self, share: float = 0.5) -> list[str]:
        """Lines set in large type, where a brand name normally is."""
        if len(self.line_heights) != len(self.lines) or not self.lines:
            return self.lines
        tallest = max(self.line_heights)
        return [t for t, h in zip(self.lines, self.line_heights) if h >= tallest * share]


class DiffToken(BaseModel):
    op: str  # "equal" | "missing" | "extra" | "changed"
    expected: str = ""
    found: str = ""


class SubCheck(BaseModel):
    name: str
    status: Status
    message: str


class FieldCheck(BaseModel):
    key: str
    name: str
    status: Status
    expected: str | None = None
    found: str | None = None
    message: str
    diff: list[DiffToken] | None = None
    sub_checks: list[SubCheck] | None = None


class ImageQuality(BaseModel):
    width: int
    height: int
    issues: list[str] = Field(default_factory=list)


class VerificationResult(BaseModel):
    verdict: Verdict
    headline: str
    counts: dict[str, int]
    checks: list[FieldCheck]
    elapsed_ms: int
    image_quality: ImageQuality
    notes: list[str] = Field(default_factory=list)
    transcript: list[str] = Field(default_factory=list)
