import random

FACES = ("heads", "tails")
EMOJI = {"heads": "🟡", "tails": "⚪"}


def flip() -> str:
    return random.choice(FACES)
