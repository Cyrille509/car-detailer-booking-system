from dataclasses import replace

import pytest

from app.domain.pricing import (
    AddOn,
    Adjustment,
    Catalog,
    JobRequest,
    Line,
    LineKind,
    Package,
    PricingError,
    Zone,
    quote,
)

CATALOG = Catalog(
    version=3,
    currency="usd",
    packages={"full_detail": Package(prices_minor={"sedan": 15000, "suv": 18000}, minutes=180)},
    add_ons={"pet_hair": AddOn(price_minor=4000, minutes=45)},
    conditions={
        ("stains", "none"): Adjustment(price_minor=0, minutes=0),
        ("stains", "heavy"): Adjustment(price_minor=3500, minutes=30),
    },
    zones={
        "07030": Zone(fee_minor=0, tax_bps=0),
        "07302": Zone(fee_minor=1500, tax_bps=663),
        "07087": Zone(fee_minor=0, tax_bps=None),
    },
    deposit_bps=2000,
    lengths=(120, 180, 240, 300),
    buffer_minutes=30,
)


def job(**fields) -> JobRequest:
    defaults = {
        "postal_code": "07030",
        "vehicle_class": "suv",
        "package_id": "full_detail",
        "conditions": {"stains": "none"},
    }
    return JobRequest(**(defaults | fields))


def test_suv_full_detail_with_pet_hair():
    result = quote(CATALOG, job(add_on_ids=frozenset({"pet_hair"})))

    assert result.lines == (
        Line(LineKind.PACKAGE, "full_detail", 18000),
        Line(LineKind.ADD_ON, "pet_hair", 4000),
    )
    assert (result.subtotal_minor, result.tax_minor, result.total_minor) == (22000, 0, 22000)
    assert result.deposit_minor == 4400
    assert (result.catalog_version, result.currency) == (3, "usd")


def test_zone_fee_is_a_line_and_is_taxed():
    result = quote(CATALOG, job(postal_code="07302", vehicle_class="sedan"))

    assert result.lines[-1] == Line(LineKind.ZONE_FEE, "07302", 1500)
    # 16500 * 6.63% = 1093.95
    assert (result.subtotal_minor, result.tax_minor, result.total_minor) == (16500, 1094, 17594)
    # 17594 * 20% = 3518.8
    assert result.deposit_minor == 3519


def test_tax_rounds_half_up_not_to_even():
    catalog = replace(
        CATALOG,
        packages={"wash": Package(prices_minor={"sedan": 105}, minutes=60)},
        zones={"07030": Zone(fee_minor=0, tax_bps=1000)},
    )

    # 105 * 10% = 10.5, which round() would turn into 10
    assert quote(catalog, job(vehicle_class="sedan", package_id="wash")).tax_minor == 11


def test_condition_answers_need_a_published_rule():
    heavy = quote(CATALOG, job(conditions={"stains": "heavy"}))
    clean = quote(CATALOG, job(conditions={"stains": "none"}))

    assert heavy.lines[-1] == Line(LineKind.CONDITION, "stains=heavy", 3500)
    assert clean.lines == (Line(LineKind.PACKAGE, "full_detail", 18000),)
    with pytest.raises(PricingError, match="no rule for stains=moderate"):
        quote(CATALOG, job(conditions={"stains": "moderate"}))


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"package_id": "ceramic_coating"}, "unknown package"),
        ({"vehicle_class": "pickup"}, "no price for pickup"),
        ({"add_on_ids": frozenset({"engine_bay"})}, "unknown add-on"),
        ({"postal_code": "90210"}, "no zone"),
        ({"postal_code": "07087"}, "no tax rate"),
        ({"conditions": {}}, "unanswered condition question: stains"),
    ],
)
def test_unpriced_work_is_never_quoted(fields, reason):
    with pytest.raises(PricingError, match=reason):
        quote(CATALOG, job(**fields))


def test_repeated_add_on_is_charged_once():
    result = quote(CATALOG, job(add_on_ids=["pet_hair", "pet_hair"]))

    assert [line.ref for line in result.lines] == ["full_detail", "pet_hair"]
    assert result.total_minor == 22000


def test_duration_rounds_up_to_an_event_length():
    assert quote(CATALOG, job()).event_minutes == 180
    # 180 + 45 + 30 = 255
    busy = job(add_on_ids=frozenset({"pet_hair"}), conditions={"stains": "heavy"})
    assert quote(CATALOG, busy).event_minutes == 300

    with pytest.raises(PricingError, match="no event length fits 255 minutes"):
        quote(replace(CATALOG, lengths=(120, 180, 240)), busy)


def test_deposit_that_rounds_to_zero_is_rejected():
    catalog = replace(CATALOG, packages={"full_detail": Package(prices_minor={"suv": 2}, minutes=60)})

    with pytest.raises(PricingError, match="deposit 0"):
        quote(catalog, job())
