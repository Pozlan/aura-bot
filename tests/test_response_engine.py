from app.services.response_engine import react, win_category, loss_category, wager_framing


def test_win_category_buckets():
    assert win_category(100) == "small_win"
    assert win_category(30_000) == "normal_win"
    assert win_category(600_000) == "big_win"
    assert win_category(6_000_000) == "massive_win"


def test_loss_category_buckets():
    assert loss_category(100) == "small_loss"
    assert loss_category(6_000_000) == "massive_loss"


def test_react_returns_nonempty_string():
    line = react("normal_win", amount=50_000)
    assert isinstance(line, str) and len(line) > 0


def test_react_unknown_category_is_safe():
    assert react("not_a_real_category") == ""


def test_wager_framing_tiny():
    assert wager_framing(10, house_cap=250_000) is not None


def test_wager_framing_normal_is_none():
    assert wager_framing(50_000, house_cap=250_000) is None


def test_wager_framing_huge():
    assert wager_framing(10_000_000, house_cap=250_000) is not None
