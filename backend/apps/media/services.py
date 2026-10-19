import re

from django.conf import settings
from django.db import transaction
from django.http import Http404

from apps.accounts import services as acl
from apps.audit import services as audit
from apps.core.exceptions import BusinessRuleError

from .images import process_image
from .models import Attachment, AttachmentData

# entity_type -> (model, permission to view, permission to attach)
ENTITIES = {
    "expense": ("expenses.Expense", "expenses.view", "expenses.record"),
    "payment": ("billing.Payment", "billing.view", "payments.record"),
    "complaint": ("operations.Complaint", "complaints.view", "complaints.work"),
    "equipment": ("inventory.Equipment", "inventory.view", "inventory.edit"),
    "connection": ("customers.Connection", "connections.view", "connections.complete_installation"),
}


def resolve_entity(user, entity_type, entity_id, *, write):
    """Returns (object, office_id) if the user may access that record, else raises Http404 (no existence leak)."""
    from django.apps import apps

    if entity_type not in ENTITIES:
        raise BusinessRuleError("Unknown entity type.")
    model_path, view_perm, write_perm = ENTITIES[entity_type]
    obj = apps.get_model(model_path).objects.filter(pk=entity_id).first()
    if obj is None:
        raise Http404
    office_id = obj.office_id
    if entity_type == "complaint":
        # office-wide rights (managers/staff) or, for technicians, only complaints assigned to them
        wide = "complaints.assign" if write else "complaints.view"
        own = "complaints.work" if write else "complaints.view_assigned"
        if office_id in acl.office_scope(user, wide) or (obj.assigned_to_id == user.pk and office_id in acl.office_scope(user, own)):
            return obj, office_id
        raise Http404
    perm = write_perm if write else view_perm
    if office_id in acl.office_scope(user, perm):
        return obj, office_id
    raise Http404


@transaction.atomic
def upload(*, actor, entity_type, entity_id, kind, filename, data, request=None):
    obj, office_id = resolve_entity(actor, entity_type, entity_id, write=True)
    if Attachment.objects.filter(entity_type=entity_type, entity_id=entity_id).count() >= settings.IMAGE_MAX_PER_ENTITY:
        raise BusinessRuleError(f"At most {settings.IMAGE_MAX_PER_ENTITY} images can be attached to one record.")
    img = process_image(data)  # raises BusinessRuleError on anything unsafe
    safe_name = re.sub(r"[^\w.\- ]", "_", (filename or "image").strip())[:120] or "image"
    att = Attachment.objects.create(
        entity_type=entity_type, entity_id=entity_id, office_id=office_id, kind=kind, filename=safe_name, mime_type=img.mime_type,
        width=img.width, height=img.height, size_bytes=img.size_bytes, thumb_size_bytes=img.thumb_size_bytes,
        checksum_sha256=img.checksum, original_format=img.original_format, created_by=actor)
    AttachmentData.objects.create(attachment=att, data_b64=img.data_b64, thumb_b64=img.thumb_b64)
    audit.log(actor=actor, action="attachment.uploaded", entity=att, office=att.office, request=request,
              after={"entity": f"{entity_type}:{entity_id}", "kind": kind, "bytes": img.size_bytes, "sha256": img.checksum})  # never the image
    return att
