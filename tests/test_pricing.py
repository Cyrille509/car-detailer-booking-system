import copy
from functools import reduce

import pytest
from pydantic import ValidationError

from app.domain.pricing import (
    MINIMUM_DEPOSIT_MINOR,
    Catalog,
    JobRequest,
    Line,
    LineKind,
    Package,
    PricingError,
    quote,
)
from app.domain.service_area import AreaCheck, Eligibility, check_service_area

DROP = object()

# the design doc's example, plus a taxed zip with a travel fee, a zip the owner prices, and a question
SETTINGS = {
    "currency": "usd",
    "packages": {"full_detail": {"prices_minor": {"sedan": 15000, "suv": 18000}, "minutes": 180}},
    "add_ons": {"pet_hair": {"price_minor": 4000, "minutes": 45}},
    "conditions": {
        "stains": {
            "none": {"price_minor": 0, "minutes": 0},
            "heavy": {"price_minor": 3500, "minutes": 30},
        }
    },
    "zones": {
        "US": {
            "07030": {"fee_minor": 0, "tax_ppm": 0},
            "07302": {"fee_minor": 1500, "tax_ppm": 66250},
            "07310": {"fee_minor": 0, "tax_ppm": 66250, "review": True},
        }
    },
    "deposit_bps": 2000,
    "lengths": [120, 180, 240, 300],
    "buffer_minutes": 30,
}
CATALOG = Catalog.model_validate(SETTINGS)
# 247 * 20% = 49.4, a cent under Stripe's minimum on its own
CHEAP_WASH = {"wash": {"prices_minor": {"suv": 247}, "minutes": 60}}


def catalog(**changes) -> Catalog:
    return Catalog.model_validate(SETTINGS | changes)


def edited(path: str, value) -> dict:
    settings = copy.deepcopy(SETTINGS)
    *parents, key = path.split(".")
    target = reduce(dict.__getitem__, parents, settings)
    if value is DROP:
        del target[key]
    else:
        target[key] = value
    return settings


def job(**fields) -> JobRequest:
    defaults = {
        "country": "US",
        "postal_code": "07030",
        "vehicle_class": "suv",
        "package_id": "full_detail",
        "conditions": {"stains": "none"},
    }
    return JobRequest(**(defaults | fields))


def test_suv_full_detail_with_pet_hair():
    result = quote(CATALOG, job(add_on_ids={"pet_hair"}))

    assert result.lines == (
        Line(LineKind.PACKAGE, "full_detail", 18000),
        Line(LineKind.ADD_ON, "pet_hair", 4000),
    )
    assert (result.subtotal_minor, result.tax_minor, result.total_minor) == (22000, 0, 22000)
    assert (result.deposit_minor, result.currency) == (4400, "usd")


def test_zone_fee_is_a_line_and_is_taxed():
    result = quote(CATALOG, job(postal_code="07302", vehicle_class="sedan"))

    assert result.lines[-1] == Line(LineKind.ZONE_FEE, "07302", 1500)
    # 16500 * 6.625% = 1093.125; the rate rounded to whole basis points (6.63%) would give 1094
    assert (result.subtotal_minor, result.tax_minor, result.total_minor) == (16500, 1093, 17593)
    # 17593 * 20% = 3518.6
    assert result.deposit_minor == 3519


def test_tax_rounds_half_up_not_to_even():
    taxed = catalog(
        packages={"full_detail": {"prices_minor": {"suv": 10005}, "minutes": 180}},
        zones={"US": {"07030": {"fee_minor": 0, "tax_ppm": 100_000}}},
    )

    # 10005 * 10% = 1000.5, which round() would turn into 1000
    assert quote(taxed, job()).tax_minor == 1001


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
        pytest.param({"package_id": "ceramic_coating"}, "unknown package", id="package not offered"),
        pytest.param({"vehicle_class": "pickup"}, "no price for pickup", id="class not priced"),
        pytest.param({"add_on_ids": {"engine_bay"}}, "unknown add-on", id="add-on not offered"),
        pytest.param({"postal_code": "90210"}, "no zone for US 90210", id="zip not listed"),
        pytest.param({"country": "CA"}, "no zone for CA 07030", id="same digits in another country"),
        pytest.param({"postal_code": "07310"}, "priced by the owner", id="zip the owner prices"),
        pytest.param({"conditions": {}}, "unanswered condition question: stains", id="question unanswered"),
        pytest.param(
            {"conditions": {"stains": "none", "odor": "smoke"}}, "no rule for odor=smoke", id="unknown question"
        ),
    ],
)
def test_unpriced_work_is_never_quoted(fields, reason):
    with pytest.raises(PricingError, match=reason):
        quote(CATALOG, job(**fields))


def test_repeated_add_on_is_charged_once():
    result = quote(CATALOG, job(add_on_ids=["pet_hair", "pet_hair"]))

    assert [line.ref for line in result.lines] == ["full_detail", "pet_hair"]
    assert result.total_minor == 22000


def test_add_ons_as_a_bare_string_are_rejected():
    with pytest.raises(TypeError, match="not a string"):
        job(add_on_ids="pet_hair")


def test_conditions_must_be_a_mapping():
    # a list of pairs could answer one question twice
    with pytest.raises(TypeError, match="map each question"):
        job(conditions=[("stains", "none"), ("stains", "heavy")])


def test_job_keeps_its_own_copy_of_answers():
    answers = {"stains": "none"}
    request = job(conditions=answers)
    answers["stains"] = "heavy"

    assert quote(CATALOG, request).total_minor == 18000


def test_duration_rounds_up_to_an_event_length():
    assert quote(CATALOG, job()).event_minutes == 180
    # 180 + 45 + 30 = 255
    busy = job(add_on_ids={"pet_hair"}, conditions={"stains": "heavy"})
    assert quote(CATALOG, busy).event_minutes == 300

    with pytest.raises(PricingError, match="no event length fits 255 minutes"):
        quote(catalog(lengths=[120, 180, 240]), busy)


def test_quote_rechecks_the_deposit_of_an_unvalidated_catalog():
    # model_copy skips validation, so a catalog changed in code could still get here
    cheap = CATALOG.model_copy(
        update={"packages": {"full_detail": Package(prices_minor={"suv": 200}, minutes=180)}}
    )

    with pytest.raises(PricingError, match="deposit 40 is invalid"):
        quote(cheap, job())


def test_parts_copied_in_code_are_checked_again():
    # model_copy skips validation; the catalog that receives the copy must not
    refund = CATALOG.zones["US"]["07302"].model_copy(update={"fee_minor": -1500})

    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        Catalog.model_validate(CATALOG.model_dump() | {"zones": {"US": {"07302": refund}}})


def test_zones_are_the_service_area():
    check = check_service_area(CATALOG.service_area, "us", "07302-1234")

    assert check == AreaCheck(Eligibility.ELIGIBLE, "US", "07302")
    assert check_service_area(CATALOG.service_area, "US", "07310").eligibility == Eligibility.NEEDS_REVIEW
    # the checked country and code key the zone directly
    priced = quote(CATALOG, job(country=check.country, postal_code=check.postal_code, vehicle_class="sedan"))
    assert priced.lines[-1] == Line(LineKind.ZONE_FEE, "07302", 1500)


def test_settings_are_stored_in_one_form():
    messy = SETTINGS | {
        "lengths": [300, 120, 180, 120],
        "zones": {"us": {" 07030​": {"fee_minor": 0, "tax_ppm": 0}}},
    }

    stored = Catalog.model_validate(messy).model_dump(mode="json")

    assert stored["lengths"] == [120, 180, 300]
    assert stored["zones"] == {"US": {"07030": {"fee_minor": 0, "tax_ppm": 0, "review": False}}}
    assert Catalog.model_validate(stored) == Catalog.model_validate(messy)


@pytest.mark.parametrize(
    ("path", "value", "reason"),
    [
        pytest.param("packages.full_detail.prices_minor.suv", -1, "greater than or equal to 0", id="negative price"),
        pytest.param("add_ons.pet_hair.price_minor", 40.5, "valid integer", id="fraction of a cent"),
        pytest.param("add_ons.pet_hair.price_minor", 4000.0, "valid integer", id="whole number as a float"),
        pytest.param("add_ons.pet_hair.price_minor", "4000", "valid integer", id="number as text"),
        pytest.param("add_ons.pet_hair.minutes", True, "valid integer", id="true as a number"),
        pytest.param(
            "add_ons.pet_hair.price_minor", 100_000_000, "less than or equal to 99999999", id="over stripe's largest"
        ),
        pytest.param("currency", "eur", "Input should be 'usd'", id="unsupported currency"),
        pytest.param("currency", "USD", "Input should be 'usd'", id="currency in capitals"),
        pytest.param("deposit_bps", 0, "greater than 0", id="no deposit"),
        pytest.param("deposit_bps", 10_001, "less than or equal to 10000", id="deposit above the total"),
        pytest.param("zones.US.07302.tax_ppm", DROP, "Field required", id="tax rate missing"),
        pytest.param("zones.US.07302.tax_ppm", -1, "greater than or equal to 0", id="negative tax"),
        pytest.param("zones.US.07302.tax_ppm", 1_000_001, "less than or equal to 1000000", id="tax above 100%"),
        pytest.param("zones.US.07302.review", "yes", "valid boolean", id="review flag as text"),
        pytest.param("packages", {}, "at least 1 item", id="no packages"),
        pytest.param("packages.full_detail.prices_minor", {}, "at least 1 item", id="package with no prices"),
        pytest.param("packages.full_detail.minutes", 0, "greater than 0", id="package takes no time"),
        pytest.param(
            "packages.Full Detail",
            {"prices_minor": {"suv": 18000}, "minutes": 180},
            "should match pattern",
            id="id with capitals and spaces",
        ),
        pytest.param("conditions.stains", {}, "at least 1 item", id="question with no answers"),
        pytest.param("zones", {}, "at least 1 item", id="no zones"),
        pytest.param("zones.US", {}, "at least 1 item", id="country with no zips"),
        pytest.param(
            "zones.US.07030-1234", {"fee_minor": 0, "tax_ppm": 0}, "list whole postal codes", id="zip plus four"
        ),
        pytest.param(
            "zones.CA", {"M5V 3L9": {"fee_minor": 0, "tax_ppm": 0}}, "unsupported country CA", id="unsupported country"
        ),
        pytest.param(
            "zones.us", {"07030": {"fee_minor": 0, "tax_ppm": 0}}, "US 07030 is listed twice", id="same zip twice"
        ),
        pytest.param("lengths", [], "at least 1 item", id="no event lengths"),
        pytest.param("lengths", [0, 180], "greater than 0", id="zero-minute event"),
        pytest.param("buffer_minutes", -5, "greater than or equal to 0", id="negative buffer"),
        pytest.param("add_on", {}, "Extra inputs are not permitted", id="misspelled key"),
    ],
)
def test_bad_settings_are_rejected(path, value, reason):
    with pytest.raises(ValidationError, match=reason):
        Catalog.model_validate(edited(path, value))


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        pytest.param({"lengths": [120]}, "full_detail is longer than every event length", id="package too long"),
        pytest.param(
            {"lengths": [180], "conditions": {"stains": {"none": {"price_minor": 0, "minutes": 30}}}},
            "full_detail is longer than every event length",
            id="every job answers the slow question",
        ),
        pytest.param(
            {"packages": CHEAP_WASH},
            "a deposit can be 49, under Stripe's minimum of 50",
            id="deposit a cent under stripe's minimum",
        ),
    ],
)
def test_settings_that_cannot_be_booked_are_rejected(changes, reason):
    with pytest.raises(ValidationError, match=reason):
        catalog(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"lengths": [180]}, id="package exactly fills the longest event"),
        pytest.param(
            {"packages": CHEAP_WASH, "conditions": {"size": {"small": {"price_minor": 1, "minutes": 0}}}},
            id="every job answers a priced question",
        ),
        pytest.param(
            {"packages": CHEAP_WASH, "zones": {"US": {"07030": {"fee_minor": 1, "tax_ppm": 0}}}},
            id="every zip charges a fee",
        ),
        pytest.param(
            # 247 * 0.2025% = 0.500175, which rounds up to 1
            {"packages": CHEAP_WASH, "zones": {"US": {"07030": {"fee_minor": 0, "tax_ppm": 2025}}}},
            id="tax lifts every deposit",
        ),
        pytest.param(
            {
                "packages": CHEAP_WASH,
                "zones": {
                    "US": {
                        "07030": {"fee_minor": 0, "tax_ppm": 0, "review": True},
                        "07302": {"fee_minor": 1, "tax_ppm": 0},
                    }
                },
            },
            id="zips the owner prices are left out",
        ),
        pytest.param(
            {"packages": CHEAP_WASH, "zones": {"US": {"07030": {"fee_minor": 0, "tax_ppm": 0, "review": True}}}},
            id="every zip priced by the owner",
        ),
    ],
)
def test_settings_at_the_limits_are_accepted(changes):
    catalog(**changes)


def test_cheapest_job_at_the_limit_pays_exactly_the_minimum():
    settings = catalog(packages={"wash": {"prices_minor": {"suv": 248}, "minutes": 60}})

    # 248 * 20% = 49.6
    assert quote(settings, job(package_id="wash")).deposit_minor == MINIMUM_DEPOSIT_MINOR
