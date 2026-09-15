import random
from enum import Enum


class Outcome(Enum):
    WIN = "win"
    LOSS = "loss"
    DRAW = "draw"


def roll() -> int:
    return random.randint(1, 6)


def resolve(roll_a: int, roll_b: int) -> Outcome:
    """From A's perspective."""
    if roll_a == roll_b:
        return Outcome.DRAW
    return Outcome.WIN if roll_a > roll_b else Outcome.LOSS
