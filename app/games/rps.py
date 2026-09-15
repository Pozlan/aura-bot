"""
Rock Paper Scissors — House and PvP. This is the template every future
game engine (coin/dice/highlow/bj/slots) should follow: pure logic here,
no Telegram objects, no DB session — handlers own I/O, engines own rules.
"""
import random
from enum import Enum

CHOICES = ("rock", "paper", "scissors")
EMOJI = {"rock": "✊", "paper": "📄", "scissors": "✌️"}
BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}


class Outcome(Enum):
    WIN = "win"
    LOSS = "loss"
    DRAW = "draw"


def resolve(choice_a: str, choice_b: str) -> Outcome:
    """From A's perspective."""
    if choice_a == choice_b:
        return Outcome.DRAW
    return Outcome.WIN if BEATS[choice_a] == choice_b else Outcome.LOSS


def house_choice() -> str:
    return random.choice(CHOICES)
