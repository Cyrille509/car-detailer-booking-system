import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

# country -> (format, template for the normalized code)
POSTAL_FORMATS = {
    # customers may send ZIP+4; only the five-digit ZIP is kept
    "US": (re.compile(r"([0-9]{5})(?:[- ]?[0-9]{4})?"), r"\1"),
}


class ServiceAreaError(ValueError):
    # a ValueError, so settings validation reports it like any other bad field
    pass


class Eligibility(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    OUT_OF_AREA = "OUT_OF_AREA"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NEEDS_COUNTRY = "NEEDS_COUNTRY"
    NEEDS_POSTAL_CODE = "NEEDS_POSTAL_CODE"
    UNCLEAR_POSTAL_CODE = "UNCLEAR_POSTAL_CODE"


@dataclass(frozen=True)
class ServiceArea:
    # (country, postal code) pairs, normalized on construction
    allowed: frozenset[tuple[str, str]]
    # served, but the owner approves each request by hand
    review: frozenset[tuple[str, str]] = frozenset()

    def __post_init__(self) -> None:
        allowed = frozenset(normalize_listed_code(*entry) for entry in self.allowed)
        review = frozenset(normalize_listed_code(*entry) for entry in self.review)
        if not allowed | review:
            raise ServiceAreaError("no postal codes listed")
        if overlap := allowed & review:
            raise ServiceAreaError(f"listed as both allowed and review: {sorted(overlap)}")
        object.__setattr__(self, "allowed", allowed)
        object.__setattr__(self, "review", review)

    @property
    def countries(self) -> set[str]:
        return {country for country, _ in self.allowed | self.review}


@dataclass(frozen=True)
class AreaCheck:
    eligibility: Eligibility
    # normalized, so they can key the pricing zones
    country: str | None = None
    postal_code: str | None = None


def normalize_postal_code(country: str, raw: str) -> str | None:
    if country not in POSTAL_FORMATS:
        return None
    pattern, template = POSTAL_FORMATS[country]
    match = pattern.fullmatch(_clean(raw))
    return match.expand(template) if match else None


def normalize_listed_code(country: str, raw: str) -> tuple[str, str]:
    country = country.strip().upper()
    if country not in POSTAL_FORMATS:
        raise ServiceAreaError(f"unsupported country {country}")
    code = normalize_postal_code(country, raw)
    if code is None:
        raise ServiceAreaError(f"unreadable {country} postal code {raw!r}")
    if code != _clean(raw):
        # trimming an owner's ZIP+4 would widen the area to the whole ZIP
        raise ServiceAreaError(f"list whole postal codes, not {raw!r}")
    return country, code


def check_service_area(area: ServiceArea, country: str | None, postal_code: str | None) -> AreaCheck:
    # country is what the customer stated, never guessed from the phone number
    country = (country or "").strip().upper()
    if len(country) != 2 or not (country.isascii() and country.isalpha()):
        return AreaCheck(Eligibility.NEEDS_COUNTRY)
    if country not in area.countries:
        return AreaCheck(Eligibility.OUT_OF_AREA, country)
    if not _clean(postal_code or ""):
        return AreaCheck(Eligibility.NEEDS_POSTAL_CODE, country)
    code = normalize_postal_code(country, postal_code)
    if code is None:
        return AreaCheck(Eligibility.UNCLEAR_POSTAL_CODE, country)
    if (country, code) in area.review:
        return AreaCheck(Eligibility.NEEDS_REVIEW, country, code)
    if (country, code) in area.allowed:
        return AreaCheck(Eligibility.ELIGIBLE, country, code)
    return AreaCheck(Eligibility.OUT_OF_AREA, country, code)


def _clean(raw: str) -> str:
    # invisible characters from copy-paste are dropped; digits from other scripts become 0-9
    text = "".join(
        str(unicodedata.decimal(ch)) if ch.isdecimal() else ch
        for ch in raw
        if unicodedata.category(ch) != "Cf"
    )
    return " ".join(text.upper().split())
