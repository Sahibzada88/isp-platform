from django.db import transaction

from .models import DocumentSequence


def next_value(scope: str, key: str = "") -> int:
    """Allocate the next number for (scope, key).

    Must be called inside the caller's transaction: the row lock is held until the
    caller commits, so two requests can never receive the same number, and a number
    is only consumed if the document that uses it is actually saved.
    """
    with transaction.atomic():
        DocumentSequence.objects.get_or_create(scope=scope, key=key)
        seq = DocumentSequence.objects.select_for_update().get(scope=scope, key=key)
        seq.last_value += 1
        seq.save(update_fields=["last_value"])
        return seq.last_value


def format_customer_id(village_code: str, number: int) -> str:
    return f"{village_code}{number:03d}"


def format_connection_id(number: int) -> str:
    return f"CON-{number:05d}"
