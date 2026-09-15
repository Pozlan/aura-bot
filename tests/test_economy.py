import pytest
from app.services.economy import parse_amount, format_amount, InvalidAmount, classify_amount


def test_parse_plain():
    assert parse_amount("500") == 500


def test_parse_shorthand():
    assert parse_amount("25k") == 25_000
    assert parse_amount("250k") == 250_000
    assert parse_amount("1m") == 1_000_000
    assert parse_amount("1.5m") == 1_500_000


def test_parse_trillion():
    assert parse_amount("1t") == 1_000_000_000_000
    assert parse_amount("2.5T") == 2_500_000_000_000


def test_parse_commas():
    assert parse_amount("1,500,000") == 1_500_000


@pytest.mark.parametrize("bad", ["-5", "0", "abc", "", "5x", "1.5.5"])
def test_parse_rejects_invalid(bad):
    with pytest.raises(InvalidAmount):
        parse_amount(bad)


def test_format_amount():
    from app.services.premium_emoji import pe
    symbol = pe("aura")
    assert format_amount(500) == f"500 {symbol}"
    assert format_amount(25_000) == f"25,000 {symbol}"
    assert format_amount(1_500_000) == f"1,500,000 {symbol}"
    assert format_amount(1_000_000_000_000) == f"1,000,000,000,000 {symbol}"
    assert format_amount(-2_000) == f"-2,000 {symbol}"


def test_classify_amount():
    assert classify_amount(100) == "small"
    assert classify_amount(30_000) == "normal"
    assert classify_amount(600_000) == "big"
    assert classify_amount(6_000_000) == "massive"
