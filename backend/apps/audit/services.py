"""Central audit service. Call `log(...)` inside the same transaction as the change."""
from .models import AuditLog

_SECRET_PARTS = ("password", "token", "secret", "session")


def _mask_cnic(value):
    value = "" if value is None else str(value)
    return "*****-*******-" + value[-1] if value else value


def redact(data):
    """Recursively remove secrets and mask CNICs from before/after snapshots."""
    if isinstance(data, dict):
        out = {}
        for key, value in data.items():
            lowered = str(key).lower()
            if any(part in lowered for part in _SECRET_PARTS):
                out[key] = "[REDACTED]"
            elif lowered == "cnic":
                out[key] = _mask_cnic(value)
            else:
                out[key] = redact(value)
        return out
    if isinstance(data, (list, tuple)):
        return [redact(v) for v in data]
    return data


def client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")
    return ip or None


def log(*, actor, action, entity, office=None, reason="", before=None, after=None, request=None):
    """`entity` is a model instance or an (entity_type, entity_id) tuple."""
    if isinstance(entity, tuple):
        entity_type, entity_id = entity
    else:
        entity_type, entity_id = entity._meta.label_lower, entity.pk
    if actor is not None and not getattr(actor, "is_authenticated", False):
        actor = None
    return AuditLog.objects.create(
        actor=actor,
        actor_label=getattr(actor, "username", "") if actor else "system",
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        office=office,
        reason=reason or "",
        before=redact(before) if before is not None else None,
        after=redact(after) if after is not None else None,
        ip_address=client_ip(request),
    )


def snapshot(instance, fields):
    """Plain-dict snapshot of selected fields (stringifying non-JSON values)."""
    out = {}
    for name in fields:
        value = getattr(instance, name)
        if hasattr(value, "pk"):
            value = value.pk
        out[name] = value if isinstance(value, (str, int, bool, type(None))) else str(value)
    return out
