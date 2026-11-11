import pytest

from app.domain.service_area import (
    AreaCheck,
    Eligibility,
    ServiceArea,
    ServiceAreaError,
    check_service_area,
)

AREA = ServiceArea(
    allowed=[("US", "07030"), ("US", "07302"), ("us", "07087")],
    review=[("US", "07310")],
)


@pytest.mark.parametrize(
    ("country", "postal_code", "expected"),
    [
        pytest.param("US", "07030", AreaCheck(Eligibility.ELIGIBLE, "US", "07030"), id="listed zip"),
        pytest.param("us", " 07030-1234 ", AreaCheck(Eligibility.ELIGIBLE, "US", "07030"), id="zip plus four"),
        pytest.param("US", "07030​", AreaCheck(Eligibility.ELIGIBLE, "US", "07030"), id="zero-width space pasted"),
        pytest.param("US", "﻿07030", AreaCheck(Eligibility.ELIGIBLE, "US", "07030"), id="byte order mark pasted"),
        pytest.param("US", "07087", AreaCheck(Eligibility.ELIGIBLE, "US", "07087"), id="owner typed lowercase country"),
        pytest.param("US", "０７０３０", AreaCheck(Eligibility.ELIGIBLE, "US", "07030"), id="fullwidth digits"),
        pytest.param("US", "٠٧٠٣٠", AreaCheck(Eligibility.ELIGIBLE, "US", "07030"), id="arabic-indic digits"),
        pytest.param("US", "07310", AreaCheck(Eligibility.NEEDS_REVIEW, "US", "07310"), id="owner reviews by hand"),
        pytest.param("US", "10001", AreaCheck(Eligibility.OUT_OF_AREA, "US", "10001"), id="zip not listed"),
        pytest.param("CA", "M5V 3L9", AreaCheck(Eligibility.OUT_OF_AREA, "CA"), id="country not served"),
        pytest.param("US", "7030", AreaCheck(Eligibility.UNCLEAR_POSTAL_CODE, "US"), id="dropped leading zero"),
        pytest.param("US", None, AreaCheck(Eligibility.NEEDS_POSTAL_CODE, "US"), id="postal code missing"),
        pytest.param("US", "  ", AreaCheck(Eligibility.NEEDS_POSTAL_CODE, "US"), id="postal code blank"),
        pytest.param("US", "​", AreaCheck(Eligibility.NEEDS_POSTAL_CODE, "US"), id="postal code only invisible"),
        pytest.param("CA", None, AreaCheck(Eligibility.OUT_OF_AREA, "CA"), id="country not served, no postal code"),
        pytest.param(None, "07030", AreaCheck(Eligibility.NEEDS_COUNTRY), id="country not stated"),
        pytest.param("USA", "07030", AreaCheck(Eligibility.NEEDS_COUNTRY), id="country not a code"),
        pytest.param("ÜS", "07030", AreaCheck(Eligibility.NEEDS_COUNTRY), id="country not ascii"),
    ],
)
def test_check_service_area(country, postal_code, expected):
    assert check_service_area(AREA, country, postal_code) == expected


def test_area_built_directly_is_still_normalized():
    area = ServiceArea(allowed={("us", "​07030")}, review={(" US ", "07310")})

    assert (area.allowed, area.review) == ({("US", "07030")}, {("US", "07310")})


@pytest.mark.parametrize(
    ("allowed", "review", "reason"),
    [
        pytest.param([("US", "7030")], [], "unreadable US postal code", id="unreadable code"),
        pytest.param([("US", "07087-1234")], [], "list whole postal codes", id="zip plus four"),
        pytest.param([("CA", "M5V 3L9")], [], "unsupported country CA", id="unsupported country"),
        pytest.param([("US", "07030")], [("US", "07030")], "both allowed and review", id="conflict"),
        pytest.param([("US", "07030")], [("us", " 07030")], "both allowed and review", id="conflict once normalized"),
        pytest.param([], [], "no postal codes", id="empty"),
    ],
)
def test_bad_owner_settings_are_rejected(allowed, review, reason):
    with pytest.raises(ServiceAreaError, match=reason):
        ServiceArea(allowed, review)
