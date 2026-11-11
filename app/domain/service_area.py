import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

# country -> (format, template for the normalized code)
POSTAL_FORMATS = {
    # customers may send ZIP+4; only the five-digit ZIP is kept
    "US": (re.compile(r"([0-9]{5})(?:[- ]?[0-9]{4})?"), r"\1"),
}


class ServiceAreaError(Exception):
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
    # (country, normalized postal code) pairs
    allowed: frozenset[tuple[str, str]]
    # served, but the owner approves each request by hand
    review: frozenset[tuple[str, str]]

    @property
    def countries(self) -> set[str]:
        return {country for country, _ in self.allowed | self.review}


@dataclass(frozen=True)
class AreaCheck:
    eligibility: Eligibility
    postal_code: str | None = None


def normalize_postal_code(country: str, raw: str) -> str | None:
    if country not in POSTAL_FORMATS:
        return None
    pattern, template = POSTAL_FORMATS[country]
    match = pattern.fullmatch(_clean(raw))
    return match.expand(template) if match else None


def build_service_area(
    allowed: Iterable[tuple[str, str]], review: Iterable[tuple[str, str]] = ()
) -> ServiceArea:
    area = ServiceArea(
        allowed=frozenset(_entry(country, code) for country, code in allowed),
        review=frozenset(_entry(country, code) for country, code in review),
    )
    if not area.countries:
        raise ServiceAreaError("no postal codes listed")
    if overlap := area.allowed & area.review:
        raise ServiceAreaError(f"listed as both allowed and review: {sorted(overlap)}")
    return area


def check_service_area(area: ServiceArea, country: str | None, postal_code: str | None) -> AreaCheck:
    # country is what the customer stated, never guessed from the phone number
    country = (country or "").strip().upper()
    if len(country) != 2 or not (country.isascii() and country.isalpha()):
        return AreaCheck(Eligibility.NEEDS_COUNTRY)
    if country not in area.countries:
        return AreaCheck(Eligibility.OUT_OF_AREA)
    if not _clean(postal_code or ""):
        return AreaCheck(Eligibility.NEEDS_POSTAL_CODE)
    code = normalize_postal_code(country, postal_code)
    if code is None:
        return AreaCheck(Eligibility.UNCLEAR_POSTAL_CODE)
    if (country, code) in area.review:
        return AreaCheck(Eligibility.NEEDS_REVIEW, code)
    if (country, code) in area.allowed:
        return AreaCheck(Eligibility.ELIGIBLE, code)
    return AreaCheck(Eligibility.OUT_OF_AREA, code)


def _clean(raw: str) -> str:
    # invisible characters from copy-paste are dropped; digits from other scripts become 0-9
    text = "".join(
        str(unicodedata.decimal(ch)) if ch.isdecimal() else ch
        for ch in raw
        if unicodedata.category(ch) != "Cf"
    )
    return " ".join(text.upper().split())


def _entry(country: str, raw: str) -> tuple[str, str]:
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
