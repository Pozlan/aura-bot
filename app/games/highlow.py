"""
One-shot higher/lower. A single first card is drawn, the player guesses
higher or lower once, a second card is drawn, and the round resolves
immediately -- no streak, no compounding multiplier, no cash-out.

The first card is always 2-12 (never 1 or 13) so a guess in either
direction is always meaningful -- a first card of 1 would make "lower"
impossible, 13 would make "higher" impossible. The second card is drawn
from the full 1-13 range but is guaranteed never to equal the first card,
so every round has a definite winner -- no ties.

Payout is a flat 2x on a win (you get your wager back plus an equal
amount) regardless of which card or direction was guessed -- no
probability scaling.
"""
import random

MIN_CARD = 1
MAX_CARD = 13
FIRST_CARD_MIN = 2
FIRST_CARD_MAX = 12


def draw_first_card() -> int:
    return random.randint(FIRST_CARD_MIN, FIRST_CARD_MAX)


def draw_second_card(exclude: int) -> int:
    """Draws from the full 1-13 range, guaranteed not to equal `exclude`."""
    pool = [v for v in range(MIN_CARD, MAX_CARD + 1) if v != exclude]
    return random.choice(pool)


def can_guess_higher(card: int) -> bool:
    return card < MAX_CARD


def can_guess_lower(card: int) -> bool:
    return card > MIN_CARD


def check_guess(card: int, next_card: int, guess: str) -> bool:
    """No ties are possible by construction (see draw_second_card), so
    this is always a clean win or loss."""
    if guess == "higher":
        return next_card > card
    return next_card < card
