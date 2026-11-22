"""Random realistic email generator."""
from __future__ import annotations

import random

FIRST_NAMES = [
    "alex", "sarah", "maria", "david", "james", "laura", "emily", "michael",
    "chris", "jenny", "kevin", "nina", "oliver", "paula", "robert", "sophie",
    "thomas", "victoria", "walter", "anna", "ben", "carla", "daniel", "elena",
    "felix", "grace", "hannah", "ivan", "julia", "leo", "mona", "nathan",
]
LAST_NAMES = [
    "smith", "johnson", "brown", "miller", "davis", "wilson", "moore", "taylor",
    "anderson", "thomas", "jackson", "white", "harris", "martin", "thompson",
    "garcia", "clark", "lewis", "walker", "hall", "young", "king", "wright",
    "lopez", "hill", "green", "adams", "baker", "ross", "rivera", "campbell",
]
DOMAINS = [
    ("gmail.com", 0.55), ("outlook.com", 0.2), ("yahoo.com", 0.12),
    ("hotmail.com", 0.08), ("protonmail.com", 0.05),
]


def random_email() -> str:
    """Generate a realistic-looking random email address."""
    first = random.choice(FIRST_NAMES)
    last = random.choice(LAST_NAMES)
    style = random.random()
    num = str(random.randint(2, 9899))
    if style < 0.45:
        local = f"{first}.{last}{num}"
    elif style < 0.75:
        local = f"{first}{last}{num}"
    elif style < 0.9:
        local = f"{first}{num}"
    else:
        local = f"{first}.{last}"
    r, acc = random.random(), 0.0
    domain = "gmail.com"
    for d, w in DOMAINS:
        acc += w
        if r <= acc:
            domain = d
            break
    return f"{local}@{domain}"


if __name__ == "__main__":
    for _ in range(10):
        print(random_email())
