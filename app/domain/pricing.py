from dataclasses import dataclass, field
from enum import StrEnum


class PricingError(Exception):
    pass


class LineKind(StrEnum):
    PACKAGE = "PACKAGE"
    ADD_ON = "ADD_ON"
    CONDITION = "CONDITION"
    ZONE_FEE = "ZONE_FEE"


@dataclass(frozen=True)
class Package:
    # vehicle class -> price; a class with no price is not offered
    prices_minor: dict[str, int]
    minutes: int


@dataclass(frozen=True)
class AddOn:
    price_minor: int
    minutes: int


@dataclass(frozen=True)
class Adjustment:
    price_minor: int
    minutes: int


@dataclass(frozen=True)
class Zone:
    fee_minor: int
    # None means not configured, which blocks checkout; 0 means not taxed
    tax_bps: int | None


@dataclass(frozen=True)
class Catalog:
    version: int
    currency: str
    packages: dict[str, Package]
    add_ons: dict[str, AddOn]
    # keyed by (question, answer); every answer needs a rule, even a zero one
    conditions: dict[tuple[str, str], Adjustment]
    # keyed by postal code
    zones: dict[str, Zone]
    deposit_bps: int
    # allowed Cal event lengths, in minutes
    lengths: tuple[int, ...]
    buffer_minutes: int


@dataclass(frozen=True)
class JobRequest:
    postal_code: str
    vehicle_class: str
    package_id: str
    add_on_ids: frozenset[str] = frozenset()
    # question -> answer
    conditions: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Line:
    kind: LineKind
    ref: str
    amount_minor: int


@dataclass(frozen=True)
class Quote:
    catalog_version: int
    currency: str
    lines: tuple[Line, ...]
    subtotal_minor: int
    tax_minor: int
    total_minor: int
    deposit_minor: int
    event_minutes: int
    buffer_minutes: int


def apply_bps(amount_minor: int, bps: int) -> int:
    # rounded once, half up; amounts are never negative
    return (amount_minor * bps + 5000) // 10000


def quote(catalog: Catalog, job: JobRequest) -> Quote:
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

    for question, answer in sorted(job.conditions.items()):
        adjustment = catalog.conditions.get((question, answer))
        if adjustment is None:
            raise PricingError(f"no rule for {question}={answer}")
        if adjustment.price_minor:
            lines.append(Line(LineKind.CONDITION, f"{question}={answer}", adjustment.price_minor))
        minutes += adjustment.minutes

    zone = catalog.zones.get(job.postal_code)
    if zone is None:
        raise PricingError(f"no zone for postal code {job.postal_code}")
    if zone.tax_bps is None:
        raise PricingError(f"no tax rate for postal code {job.postal_code}")
    if zone.fee_minor:
        lines.append(Line(LineKind.ZONE_FEE, job.postal_code, zone.fee_minor))

    subtotal = sum(line.amount_minor for line in lines)
    tax = apply_bps(subtotal, zone.tax_bps)
    total = subtotal + tax
    deposit = apply_bps(total, catalog.deposit_bps)
    if not 0 < deposit <= total:
        raise PricingError(f"deposit {deposit} is invalid for total {total}")

    length = next((n for n in sorted(catalog.lengths) if n >= minutes), None)
    if length is None:
        raise PricingError(f"no event length fits {minutes} minutes")

    return Quote(
        catalog_version=catalog.version,
        currency=catalog.currency,
        lines=tuple(lines),
        subtotal_minor=subtotal,
        tax_minor=tax,
        total_minor=total,
        deposit_minor=deposit,
        event_minutes=length,
        buffer_minutes=catalog.buffer_minutes,
    )
