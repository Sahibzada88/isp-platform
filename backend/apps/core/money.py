from decimal import Decimal

CENT = Decimal("0.01")


def money_str(value) -> str:
    """Always two decimals, whatever the database returned (SQLite's SUM() drops trailing zeros; None means zero)."""
    return str(Decimal(str(value if value is not None else 0)).quantize(CENT))
