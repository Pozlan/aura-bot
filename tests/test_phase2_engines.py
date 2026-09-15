from app.games import coin, dice, highlow


def test_coin_flip_is_always_a_valid_face():
    for _ in range(50):
        assert coin.flip() in ("heads", "tails")


def test_dice_roll_range():
    for _ in range(50):
        r = dice.roll()
        assert 1 <= r <= 6


def test_dice_resolve():
    assert dice.resolve(6, 3) == dice.Outcome.WIN
    assert dice.resolve(2, 5) == dice.Outcome.LOSS
    assert dice.resolve(4, 4) == dice.Outcome.DRAW


def test_highlow_first_card_range():
    for _ in range(50):
        c = highlow.draw_first_card()
        assert 2 <= c <= 12  # never 1 or 13 -- both directions always legal


def test_highlow_second_card_never_ties_first():
    for _ in range(50):
        first = highlow.draw_first_card()
        second = highlow.draw_second_card(exclude=first)
        assert second != first
        assert 1 <= second <= 13


def test_highlow_check_guess_no_ties_possible():
    assert highlow.check_guess(7, 9, "higher") is True
    assert highlow.check_guess(7, 3, "higher") is False
    assert highlow.check_guess(7, 3, "lower") is True
    assert highlow.check_guess(7, 9, "lower") is False


def test_highlow_edges():
    assert highlow.can_guess_higher(13) is False
    assert highlow.can_guess_higher(12) is True
    assert highlow.can_guess_lower(1) is False
    assert highlow.can_guess_lower(2) is True
