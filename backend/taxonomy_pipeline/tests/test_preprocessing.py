import pytest

from taxonomy_pipeline.metrics.preprocessing import get_normalizer, typography_v1


@pytest.mark.parametrize("source, expected", [
    (None, ""), ("", ""), (" \t\u00a0\n", ""),
    ("  Ｔｅｌｅｃｏｍ  ", "telecom"),
    ("Post‑Market Surveillance", "post-market surveillance"),
    ("Accountant / Accounts Executive", "accountant/accounts executive"),
    ("Ships’ Deck Officers", "ships' deck officers"),
    ("Real Estate ( Commercial/Residential )", "real estate (commercial/residential)"),
    ("C++ C# M&A", "c++ c# m&a"),
    ("[ Air Transport ]", "[air transport]"),
    ("A ,B ; C :D", "a, b; c: d"),
    ("(A ; ) [B : ] C,", "(a;) [b:] c,"),
    ('“Clinical” — Legal', '\"clinical\"-legal'),
    ("M & A / R & D", "m&a/r&d"),
    ("Straße\u00a0Services", "strasse services"),
    ("n.e.c. + # =", "n.e.c. + # ="),
])
def test_typography_rules_and_idempotence(source, expected):
    assert typography_v1(source) == expected
    assert typography_v1(expected) == expected


def test_does_not_expand_split_or_remove_meaning():
    assert typography_v1("COO") == "coo"
    assert typography_v1("Education & EdTech") == "education&edtech"
    assert typography_v1("Air Transport") != typography_v1("Water Transport")
    assert typography_v1("Medical (Sales)") == "medical (sales)"


def test_legacy_preserves_existing_normalization():
    assert get_normalizer("legacy")("  Ships’  / Deck‑Crew ") == "ships’ / deck‑crew"


@pytest.mark.parametrize("source, expected", [
    ("Post\u2011Market", "post\u2011market"),
    ("  Ｔｅｌｅｃｏｍ  ", "ｔｅｌｅｃｏｍ"),
    ("Services.,;:!? ", "services.,;:!?"),
    ("Straße", "straße"),
    ("  Mixed\t\u00a0Whitespace\n", "mixed whitespace"),
    (None, ""),
])
def test_legacy_retains_original_text_rules(source, expected):
    assert get_normalizer("legacy")(source) == expected


@pytest.mark.parametrize("profile", [None, "unknown", 1, [], {}])
def test_invalid_profile(profile):
    with pytest.raises(ValueError, match="preprocessing"):
        get_normalizer(profile)
