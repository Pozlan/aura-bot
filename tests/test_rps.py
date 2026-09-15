from app.games.rps import resolve, Outcome


def test_rock_beats_scissors():
    assert resolve("rock", "scissors") == Outcome.WIN


def test_scissors_beats_paper():
    assert resolve("scissors", "paper") == Outcome.WIN


def test_paper_beats_rock():
    assert resolve("paper", "rock") == Outcome.WIN


def test_draw():
    assert resolve("rock", "rock") == Outcome.DRAW


def test_loss():
    assert resolve("scissors", "rock") == Outcome.LOSS
