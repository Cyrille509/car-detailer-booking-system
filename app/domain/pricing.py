from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from app.domain.service_area import ServiceArea, normalize_listed_code

# Stripe's smallest USD charge
MINIMUM_DEPOSIT_MINOR = 50

# machine ids such as full_detail
Id = Annotated[str, Field(pattern=r"^[a-z0-9_]{1,40}$")]
# integer minor units up to Stripe's largest charge; strict, so 40.5 or "40" is refused, not rounded
Amount = Annotated[int, Field(strict=True, ge=0, le=99_999_999)]
Minutes = Annotated[int, Field(strict=True, ge=0, le=24 * 60)]


class PricingError(Exception):
    pass


class LineKind(StrEnum):
    PACKAGE = "PACKAGE"
    ADD_ON = "ADD_ON"
    CONDITION = "CONDITION"
    ZONE_FEE = "ZONE_FEE"


class Published(BaseModel):
    # unknown keys are typos in the owner's settings; parts copied in code are checked again
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")


class Package(Published):
    # vehicle class -> price; a class with no price is not offered
    prices_minor: dict[Id, Amount] = Field(min_length=1)
    minutes: Annotated[Minutes, Field(gt=0)]


class AddOn(Published):
    price_minor: Amount
    minutes: Minutes


class Adjustment(Published):
    price_minor: Amount
    minutes: Minutes


class Zone(Published):
    fee_minor: Amount
    # millionths, so a rate like 6.625% is exact; 0 where detailing isn't taxed
    tax_ppm: Annotated[int, Field(strict=True, ge=0, le=1_000_000)]
    # served, but the owner prices each request by hand
    review: Annotated[bool, Field(strict=True)] = False


class Catalog(Published):
    currency: Literal["usd"]
    packages: dict[Id, Package] = Field(min_length=1)
    add_ons: dict[Id, AddOn] = {}
    # question -> answer -> rule; every answer needs a rule, even a zero one
    conditions: dict[Id, Annotated[dict[Id, Adjustment], Field(min_length=1)]] = {}
    # country -> postal code -> zone; together they are the service area
    zones: dict[str, Annotated[dict[str, Zone], Field(min_length=1)]] = Field(min_length=1)
    deposit_bps: Annotated[int, Field(strict=True, gt=0, le=10_000)]
    # allowed Cal event lengths
    lengths: tuple[Annotated[Minutes, Field(gt=0)], ...] = Field(min_length=1)
    buffer_minutes: Minutes

    @field_validator("zones")
    @classmethod
    def _normalize_zones(cls, zones: dict[str, dict[str, Zone]]) -> dict[str, dict[str, Zone]]:
        normalized: dict[str, dict[str, Zone]] = {}
        for raw_country, codes in zones.items():
            for raw_code, zone in codes.items():
                country, code = normalize_listed_code(raw_country, raw_code)
                if code in normalized.setdefault(country, {}):
                    raise ValueError(f"{country} {code} is listed twice")
                normalized[country][code] = zone
        return normalized

    @field_validator("lengths")
    @classmethod
    def _sort_lengths(cls, lengths: tuple[int, ...]) -> tuple[int, ...]:
        return tuple(sorted(set(lengths)))

    @model_validator(mode="after")
    def _every_job_is_bookable(self) -> Self:
        # every job answers each question, so its shortest and cheapest answers always apply
        rules = [answers.values() for answers in self.conditions.values()]
        shortest = sum(min(rule.minutes for rule in answers) for answers in rules)
        for package_id, package in self.packages.items():
            if package.minutes + shortest > self.lengths[-1]:
                raise ValueError(f"{package_id} is longer than every event length")

        cheapest = sum(min(rule.price_minor for rule in answers) for answers in rules)
        cheapest += min(price for package in self.packages.values() for price in package.prices_minor.values())
        deposits = [
            _totals(cheapest + zone.fee_minor, zone, self.deposit_bps)[2]
            for codes in self.zones.values()
            for zone in codes.values()
            if not zone.review
        ]
        if deposits and min(deposits) < MINIMUM_DEPOSIT_MINOR:
            raise ValueError(
                f"a deposit can be {min(deposits)}, under Stripe's minimum of {MINIMUM_DEPOSIT_MINOR}"
            )
        return self

    @property
    def service_area(self) -> ServiceArea:
        listed = [(country, code, zone) for country, codes in self.zones.items() for code, zone in codes.items()]
        return ServiceArea(
            allowed={(country, code) for country, code, zone in listed if not zone.review},
            review={(country, code) for country, code, zone in listed if zone.review},
        )


class QuoteRequest(BaseModel):
    # validated copies: a repeated add-on is one add-on, and later edits by the caller stay out
    model_config = ConfigDict(frozen=True, extra="forbid")

    # normalized, as the service area check returns them
    country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    postal_code: Annotated[str, Field(min_length=1, max_length=16)]
    vehicle_class: Id
    package_id: Id
    add_on_ids: frozenset[Id] = frozenset()
    # question -> answer
    conditions: dict[Id, Id] = {}

    @field_serializer("add_on_ids")
    def _sorted_add_ons(self, add_on_ids: frozenset[str]) -> list[str]:
        # sets have no order; a sorted list keeps saved snapshots identical run to run
        return sorted(add_on_ids)


@dataclass(frozen=True)
class Line:
    kind: LineKind
    ref: str
    amount_minor: int


@dataclass(frozen=True)
class Quote:
    currency: str
    lines: tuple[Line, ...]
    subtotal_minor: int
    tax_minor: int
    total_minor: int
    deposit_minor: int
    event_minutes: int
    buffer_minutes: int


def quote(catalog: Catalog, job: QuoteRequest) -> Quote:
    package = catalog.packages.get(job.package_id)
    if package is None:
        raise PricingError(f"unknown package {job.package_id}")
    if job.vehicle_class not in package.prices_minor:
        raise PricingError(f"{job.package_id} has no price for {job.vehicle_class}")
    lines = [Line(LineKind.PACKAGE, job.package_id, package.prices_minor[job.vehicle_class])]
    minutes = package.minutes

    for add_on_id in sorted(job.add_on_ids):
        add_on = catalog.add_ons.get(add_on_id)
        if add_on is None:
            raise PricingError(f"unknown add-on {add_on_id}")
        lines.append(Line(LineKind.ADD_ON, add_on_id, add_on.price_minor))
        minutes += add_on.minutes

    if unanswered := sorted(catalog.conditions.keys() - job.conditions.keys()):
        raise PricingError(f"unanswered condition question: {', '.join(unanswered)}")
    for question, answer in sorted(job.conditions.items()):
        adjustment = catalog.conditions.get(question, {}).get(answer)
        if adjustment is None:
            raise PricingError(f"no rule for {question}={answer}")
        if adjustment.price_minor:
            lines.append(Line(LineKind.CONDITION, f"{question}={answer}", adjustment.price_minor))
        minutes += adjustment.minutes

    zone = catalog.zones.get(job.country, {}).get(job.postal_code)
    if zone is None:
        raise PricingError(f"no zone for {job.country} {job.postal_code}")
    if zone.review:
        raise PricingError(f"{job.country} {job.postal_code} is priced by the owner")
    if zone.fee_minor:
        lines.append(Line(LineKind.ZONE_FEE, job.postal_code, zone.fee_minor))

    subtotal = sum(line.amount_minor for line in lines)
    tax, total, deposit = _totals(subtotal, zone, catalog.deposit_bps)
    # publishing rules this out; a catalog copied without validation could still get here
    if not MINIMUM_DEPOSIT_MINOR <= deposit <= total:
        raise PricingError(f"deposit {deposit} is invalid for total {total}")

    length = next((n for n in catalog.lengths if n >= minutes), None)
    if length is None:
        raise PricingError(f"no event length fits {minutes} minutes")

    return Quote(
        currency=catalog.currency,
        lines=tuple(lines),
        subtotal_minor=subtotal,
        tax_minor=tax,
        total_minor=total,
        deposit_minor=deposit,
        event_minutes=length,
        buffer_minutes=catalog.buffer_minutes,
    )


def _totals(subtotal_minor: int, zone: Zone, deposit_bps: int) -> tuple[int, int, int]:
    # each rate is applied once, rounding half up; amounts are never negative
    tax = (subtotal_minor * zone.tax_ppm + 500_000) // 1_000_000
    total = subtotal_minor + tax
    deposit = (total * deposit_bps + 5_000) // 10_000
    return tax, total, deposit
