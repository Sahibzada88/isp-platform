from django.db import transaction

from apps.audit import services as audit
from apps.core.exceptions import BusinessRuleError
from apps.customers import services as cs

from .models import Lead, LeadNote

OPEN = [Lead.Status.NEW, Lead.Status.CONTACTED, Lead.Status.INTERESTED]


@transaction.atomic
def create_lead(*, actor, office, name, phone, source="office", address="", interested_package=None, notes="", assigned_to=None, request=None):
    lead = Lead.objects.create(office=office, name=name, phone=phone, source=source, address=address, interested_package=interested_package,
                               notes=notes, assigned_to=assigned_to, created_by=actor)
    audit.log(actor=actor, action="lead.created", entity=lead, office=office, request=request, after={"name": name, "source": source})
    return lead


@transaction.atomic
def update_lead(*, actor, lead, changes, request=None):
    lead = Lead.objects.select_for_update().get(pk=lead.pk)
    if lead.status in (Lead.Status.CONVERTED, Lead.Status.LOST):
        raise BusinessRuleError(f"A {lead.status} lead can no longer be edited.")
    if changes.get("status") in (Lead.Status.CONVERTED, Lead.Status.LOST):
        raise BusinessRuleError("Use the convert or mark-lost actions for this change.")
    before = {k: str(getattr(lead, k)) for k in changes}
    for k, v in changes.items():
        setattr(lead, k, v)
    lead.save()
    audit.log(actor=actor, action="lead.updated", entity=lead, office=lead.office, request=request, before=before,
              after={k: str(v) for k, v in changes.items()})
    return lead


@transaction.atomic
def add_note(*, actor, lead, note):
    if not note:
        raise BusinessRuleError("The note cannot be empty.")
    return LeadNote.objects.create(lead=lead, author=actor, note=note)


@transaction.atomic
def mark_lost(*, actor, lead, reason, request=None):
    lead = Lead.objects.select_for_update().get(pk=lead.pk)
    if lead.status not in OPEN:
        raise BusinessRuleError("Only open leads can be marked as lost.")
    if not reason:
        raise BusinessRuleError("A reason is required.")
    lead.status, lead.lost_reason = Lead.Status.LOST, reason
    lead.save()
    audit.log(actor=actor, action="lead.lost", entity=lead, office=lead.office, reason=reason, request=request)
    return lead


@transaction.atomic
def convert(*, actor, lead, village, package, installation_address, permanent_address, full_name=None, cnic="", confirm_duplicates=False,
            override_reason="", existing_customer=None, request=None):
    """Turn a lead into a customer (new customer ID) + connection, or add a connection for an existing customer."""
    lead = Lead.objects.select_for_update().get(pk=lead.pk)
    if lead.status not in OPEN:
        raise BusinessRuleError("Only open leads can be converted.")
    if existing_customer is not None:
        customer = existing_customer
    else:
        customer = cs.register_customer(actor=actor, confirm_duplicates=confirm_duplicates, reason=override_reason, request=request, data={
            "full_name": full_name or lead.name, "phone": lead.phone, "cnic": cnic, "permanent_address": permanent_address,
            "village": village, "office": lead.office})
    connection = cs.create_connection(actor=actor, customer=customer, package=package, installation_address=installation_address,
                                      village=village, office=lead.office, request=request)
    lead.status, lead.converted_customer, lead.converted_connection = Lead.Status.CONVERTED, customer, connection
    lead.save()
    audit.log(actor=actor, action="lead.converted", entity=lead, office=lead.office, request=request,
              after={"customer": customer.customer_id, "connection": connection.connection_id})
    return lead
