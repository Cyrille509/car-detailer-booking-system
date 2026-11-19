import unicodedata
from enum import StrEnum


class Keyword(StrEnum):
    OPT_OUT = "OPT_OUT"
    OPT_IN = "OPT_IN"
    HELP = "HELP"


# Twilio's defaults for long-code numbers. Twilio also opts people back in on YES, but here YES
# stays an ordinary answer ("is that a 3-row SUV?"); Twilio reports its own opt-ins on the webhook.
KEYWORDS = {
    **dict.fromkeys(["STOP", "STOPALL", "UNSUBSCRIBE", "END", "QUIT", "CANCEL", "REVOKE", "OPTOUT"], Keyword.OPT_OUT),
    **dict.fromkeys(["START", "UNSTOP"], Keyword.OPT_IN),
    **dict.fromkeys(["HELP", "INFO"], Keyword.HELP),
}


def find_keyword(body: str) -> Keyword | None:
    # the whole text must be the keyword, so "cancel my appointment" stays a booking request;
    # case, spacing, punctuation and lookalike characters don't matter ("Stop.", "opt out", "ＳＴＯＰ")
    text = unicodedata.normalize("NFKC", body)
    letters = "".join(ch for ch in text if not unicodedata.category(ch).startswith(("P", "Z", "C")))
    return KEYWORDS.get(letters.upper())
