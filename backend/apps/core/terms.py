"""Single authoritative billing-term calculation (shared by API and scheduled jobs)."""
from datetime import timedelta

VALIDITY_DAYS = 30  # fixed for every package; no grace period


def compute_term(payment_at, current_expiry=None):
    """Return (term_start, term_end) for a payment confirmed at `payment_at` (UTC).

    New/lapsed connection: starts at the payment timestamp.
    Early renewal (current expiry still in the future): the new term starts when the
    existing one ends, so the already-paid period is never shortened.
    """
    start = payment_at
    if current_expiry is not None and current_expiry > payment_at:
        start = current_expiry
    return start, start + timedelta(days=VALIDITY_DAYS)
