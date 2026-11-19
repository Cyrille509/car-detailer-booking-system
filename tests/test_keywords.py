import pytest

from app.domain.keywords import Keyword, find_keyword


@pytest.mark.parametrize(
    "body",
    ["STOP", "stop", "Stop.", "  STOP!!\n", "STOPALL", "Stop all", "unsubscribe", "END", "quit", "CANCEL",
     "revoke", "OPTOUT", "opt out", "Opt-Out", "ＳＴＯＰ", "STOP​", "\tstop\t"],
)
def test_opt_out_words(body):
    assert find_keyword(body) == Keyword.OPT_OUT


@pytest.mark.parametrize("body", ["START", "start", "Unstop", "un-stop!"])
def test_opt_in_words(body):
    assert find_keyword(body) == Keyword.OPT_IN


@pytest.mark.parametrize("body", ["HELP", "help?", "Info"])
def test_help_words(body):
    assert find_keyword(body) == Keyword.HELP


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("cancel my appointment", id="booking request, not an opt-out"),
        pytest.param("please stop by at 5", id="stop inside a sentence"),
        pytest.param("I want to start over", id="start inside a sentence"),
        pytest.param("yes", id="yes is an answer"),
        pytest.param("STOP1", id="keyword with a digit"),
        pytest.param("stopp", id="misspelled"),
        pytest.param("", id="empty"),
        pytest.param("   ", id="blank"),
        pytest.param("🛑", id="emoji only"),
    ],
)
def test_ordinary_texts_are_not_keywords(body):
    assert find_keyword(body) is None
