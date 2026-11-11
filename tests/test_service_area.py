import pytest

from app.domain.service_area import (
    Eligibility,
    ServiceAreaError,
    build_service_area,
    check_service_area,
)

AREA = build_service_area(
    allowed=[("US", "07030"), ("US", "07302"), ("us", "07087")],
    review=[("US", "07310")],
)


@pytest.mark.parametrize(
    ("country", "postal_code", "eligibility", "normalized"),
    [
        pytest.param("US", "07030", Eligibility.ELIGIBLE, "07030", id="listed zip"),
        pytest.param("us", " 07030-1234 ", Eligibility.ELIGIBLE, "07030", id="zip plus four"),
        pytest.param("US", "07087", Eligibility.ELIGIBLE, "07087", id="owner typed lowercase country"),
        pytest.param("US", "０７０３０", Eligibility.ELIGIBLE, "07030", id="fullwidth digits"),
        pytest.param("US", "٠٧٠٣٠", Eligibility.ELIGIBLE, "07030", id="arabic-indic digits"),
        pytest.param("US", "07310", Eligibility.NEEDS_REVIEW, "07310", id="owner reviews by hand"),
        pytest.param("US", "10001", Eligibility.OUT_OF_AREA, "10001", id="zip not listed"),
        pytest.param("CA", "M5V 3L9", Eligibility.OUT_OF_AREA, None, id="country not served"),
        pytest.param("US", "7030", Eligibility.UNCLEAR_POSTAL_CODE, None, id="dropped leading zero"),
        pytest.param(None, "07030", Eligibility.NEEDS_COUNTRY, None, id="country not stated"),
        pytest.param("USA", "07030", Eligibility.NEEDS_COUNTRY, None, id="country not a code"),
        pytest.param("ÜS", "07030", Eligibility.NEEDS_COUNTRY, None, id="country not ascii"),
    ],
)
def test_check_service_area(country, postal_code, eligibility, normalized):
    result = check_service_area(AREA, country, postal_code)

    assert (result.eligibility, result.postal_code) == (eligibility, normalized)


@pytest.mark.parametrize(
    ("allowed", "review", "reason"),
    [
        pytest.param([("US", "7030")], [], "unreadable US postal code", id="unreadable code"),
        pytest.param([("US", "07087-1234")], [], "list whole postal codes", id="zip plus four"),
        pytest.param([("CA", "M5V 3L9")], [], "unsupported country CA", id="unsupported country"),
        pytest.param([("US", "07030")], [("US", "07030")], "both allowed and review", id="conflict"),
        pytest.param([], [], "no postal codes", id="empty"),
    ],
)
def test_bad_owner_settings_are_rejected(allowed, review, reason):
    with pytest.raises(ServiceAreaError, match=reason):
        build_service_area(allowed, review)
