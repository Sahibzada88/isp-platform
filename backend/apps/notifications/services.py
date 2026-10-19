from apps.accounts.services import employees_with_permission

from .models import Notification


def notify(recipients, *, kind, title, body="", link="", office=None, dedupe_key="", exclude=None):
    """Create one notification per recipient. Safe to call repeatedly with the same dedupe_key."""
    ids = {r.pk if hasattr(r, "pk") else r for r in recipients}
    if exclude is not None:
        ids.discard(exclude.pk if hasattr(exclude, "pk") else exclude)
    rows = [Notification(recipient_id=i, office=office, kind=kind, title=title, body=body, link=link, dedupe_key=dedupe_key)
            for i in ids]
    Notification.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


def notify_permission(code, office, **kwargs):
    """Notify everyone who holds `code` in `office` (respects office scope)."""
    return notify(employees_with_permission(code, office), office=office, **kwargs)
