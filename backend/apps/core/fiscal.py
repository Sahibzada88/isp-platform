"""Financial-year helpers. The owner chooses the starting month (default July, as in Pakistan's fiscal year)."""
from datetime import date, timedelta

from .settings_store import get_setting


def fy_start_month() -> int:
    try:
        m = int(get_setting("financial_year_start_month", 7))
    except (TypeError, ValueError):
        return 7
    return m if 1 <= m <= 12 else 7


def fy_label_for(day: date) -> int:
    """A financial year is labelled by the calendar year in which it STARTS (FY2026 = Jul 2026 - Jun 2027)."""
    m = fy_start_month()
    return day.year if day.month >= m else day.year - 1


def fy_range(label: int):
    m = fy_start_month()
    start = date(label, m, 1)
    end = date(label + 1, m, 1) - timedelta(days=1)
    return start, end
