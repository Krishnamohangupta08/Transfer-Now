"""Anonymous sender-address generator (no fabricated human identities).

The "random" sender mode must never invent a plausible person at a real
provider domain: a made-up gmail/outlook address can collide with somebody's
real mailbox, and sending files under a fabricated human identity is wrong.
So ``random_email()`` only produces clearly synthetic local parts at the
IANA-reserved example domains (RFC 2606 / RFC 6761) — they can never reach a
real person.  The UI (updated separately) labels this mode "anonymous
sender — reserved domain, not a real mailbox"; the recommended mode is the
user's own address ("fixed" sender mode).
"""
from __future__ import annotations

import secrets

# RFC 2606 / IANA special-use domains — reserved, can never deliver to a
# real mailbox, safe to put into a sender field.
DOMAINS = ("example.com", "example.org", "example.net")

# Synthetic local-part prefixes — deliberately NOT first/last-name pairs.
LOCAL_PREFIXES = ("anon", "guest", "sender")


def random_email() -> str:
    """Generate an anonymous sender address, e.g. ``anon-4271@example.org``."""
    local = f"{secrets.choice(LOCAL_PREFIXES)}-{secrets.randbelow(9000) + 1000}"
    return f"{local}@{secrets.choice(DOMAINS)}"


if __name__ == "__main__":
    for _ in range(10):
        print(random_email())
