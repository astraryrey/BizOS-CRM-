"""
The MVP lifecycle:
call -> client card -> booking -> reminder -> visit -> invoice -> repeat offer.

Also home to two guards: resource availability (no double-booking) and
referential integrity (every part of a booking belongs to the same tenant).
"""
from datetime import timedelta

from sqlalchemy.orm import Session

from . import clock
from .models import (
    Appointment, AppointmentStatus, Asset, Client, Invoice, InvoiceStatus,
    MessageStatus, Resource, ScheduledMessage, Service, Tenant,
)
from .verticals import load_vertical, render_template

DEFAULT_REMINDER_HOURS = 48

# Statuses that occupy a slot. Cancellations and no-shows free the time.
BLOCKING_STATUSES = (
    AppointmentStatus.scheduled,
    AppointmentStatus.confirmed,
    AppointmentStatus.done,
)


class ConflictError(Exception):
    """Slot is taken -> 409."""


class IntegrityError(Exception):
    """Record missing or belongs to another tenant -> 400."""


def reminder_offset(cfg: dict) -> timedelta:
    """How far ahead to remind. Per-vertical, set in YAML."""
    return timedelta(hours=int(cfg.get("reminder_hours_before", DEFAULT_REMINDER_HOURS)))


def pick_channel(client: Client) -> str:
    """Telegram if the client has subscribed to the bot, otherwise SMS."""
    return "telegram" if client.telegram_chat_id else "sms"


# ----------------------------------------------------------- integrity ----
def fetch_owned(db: Session, model, obj_id: int, tenant: Tenant, label: str):
    """Fetch a record and verify it belongs to this tenant."""
    obj = db.get(model, obj_id)
    if obj is None:
        raise IntegrityError(f"{label} #{obj_id} not found")
    if obj.tenant_id != tenant.id:
        raise IntegrityError(
            f"{label} #{obj_id} belongs to business #{obj.tenant_id}, "
            f"but you are working as business #{tenant.id}"
        )
    return obj


def validate_appointment_parts(
    db: Session, tenant: Tenant, client_id: int, service_id: int,
    asset_id: int | None, resource_id: int | None,
) -> tuple[Client, Service, Asset | None, Resource | None]:
    client = fetch_owned(db, Client, client_id, tenant, "Client")
    service = fetch_owned(db, Service, service_id, tenant, "Service")

    asset = None
    if asset_id is not None:
        asset = fetch_owned(db, Asset, asset_id, tenant, "Asset")
        if asset.client_id != client.id:
            raise IntegrityError(
                f"Asset #{asset_id} belongs to client #{asset.client_id}, "
                f"but the booking is for client #{client.id}"
            )

    resource = None
    if resource_id is not None:
        resource = fetch_owned(db, Resource, resource_id, tenant, "Resource")

    cfg = load_vertical(tenant.vertical)
    if cfg.get("resource_required") and resource is None:
        label = (cfg.get("labels") or {}).get("resource", "Resource")
        raise IntegrityError(f"This vertical requires a {label} (resource_id)")

    return client, service, asset, resource


# --------------------------------------------------------- availability ----
def find_conflict(
    db: Session, tenant: Tenant, resource_id: int | None,
    starts_at, ends_at, exclude_id: int | None = None,
) -> Appointment | None:
    """
    Two intervals overlap when A_start < B_end AND A_end > B_start.
    Only this resource, only statuses that actually hold the slot.
    """
    if resource_id is None:
        return None

    q = db.query(Appointment).filter(
        Appointment.tenant_id == tenant.id,
        Appointment.resource_id == resource_id,
        Appointment.status.in_(BLOCKING_STATUSES),
        Appointment.starts_at < ends_at,
        Appointment.ends_at > starts_at,
    )
    if exclude_id is not None:
        q = q.filter(Appointment.id != exclude_id)
    return q.first()


# ------------------------------------------------------------ templates ----
def _ctx_for(appt: Appointment, db: Session | None = None,
             tenant: Tenant | None = None) -> dict:
    """Flat context for message templates."""
    slots = "call us to confirm"
    if db is not None and tenant is not None:
        try:
            from .slots import find_free_slots, slots_text
            slots = slots_text(find_free_slots(db, tenant, appt.service, limit=3))
        except Exception:
            pass
    return {
        "client.name": appt.client.name or "there",
        "asset.title": appt.asset.title if appt.asset else "",
        "resource.name": appt.resource.name if appt.resource else "",
        "service.name": appt.service.name,
        "when": appt.starts_at.strftime("%d %b, %H:%M"),
        "amount": f"{appt.service.price:.0f}",
        "slots": slots,
        "days": (appt.service.repeat_days or 0),
        "pay_link": "",
    }


def schedule_message(
    db: Session, tenant: Tenant, client: Client, kind: str,
    template_key: str, send_at, ctx: dict, channel: str | None = None,
) -> ScheduledMessage:
    cfg = load_vertical(tenant.vertical)
    channel = channel or pick_channel(client)
    ctx = {**ctx, "tenant": tenant.name}

    # SMS gets the short template. Non-Latin text fits only 70 characters
    # per segment, so a long message costs two or three times as much.
    key = template_key
    if channel == "sms" and f"{template_key}_sms" in (cfg.get("templates") or {}):
        key = f"{template_key}_sms"

    msg = ScheduledMessage(
        tenant_id=tenant.id, client_id=client.id, channel=channel,
        kind=kind, template_key=key, payload=ctx,
        rendered_text=render_template(cfg, key, ctx), send_at=send_at,
    )
    db.add(msg)
    return msg


# ------------------------------------------------------------ lifecycle ----
def on_appointment_created(db: Session, appt: Appointment, tenant: Tenant) -> None:
    """Booking made -> schedule the reminder (lead time comes from YAML)."""
    cfg = load_vertical(tenant.vertical)
    remind_at = appt.starts_at - reminder_offset(cfg)
    if remind_at > clock.now():
        schedule_message(db, tenant, appt.client, kind="reminder",
                         template_key="reminder", send_at=remind_at,
                         ctx=_ctx_for(appt, db, tenant))


def on_appointment_done(db: Session, appt: Appointment, tenant: Tenant) -> None:
    """
    Visit completed -> raise an invoice and schedule the repeat offer.

    INTEGRATION POINT (billing): the invoice is created here and pushed to
    the billing system through the adapter. With no billing connected the
    null adapter takes over and everything else still works.
    """
    from .integrations.billing import get_billing

    invoice = Invoice(
        tenant_id=tenant.id, appointment_id=appt.id,
        amount=appt.service.price, status=InvoiceStatus.sent,
        issued_at=clock.now(),
    )

    billing = get_billing()
    try:
        res = billing.create_invoice(
            external_client_id=appt.client.external_id,
            account=tenant.billing_account,
            amount=appt.service.price,
            description=f"{appt.service.name} - {appt.starts_at:%d %b %Y}",
            phone=appt.client.phone,
        )
        invoice.external_source = billing.name
        invoice.external_id = res.get("external_id")
        invoice.pay_link = res.get("pay_link")
    except Exception as e:
        # Billing unreachable: still raise the invoice locally, log and move on.
        invoice.external_source = None
        invoice.pay_link = None
        import logging
        logging.getLogger("bizos.billing").warning("Billing unreachable: %s", e)

    db.add(invoice)

    ctx = _ctx_for(appt, db, tenant)
    ctx["pay_link"] = invoice.pay_link or ""
    schedule_message(db, tenant, appt.client, kind="invoice",
                     template_key="invoice", send_at=clock.now(), ctx=ctx)

    if appt.service.repeat_days:
        schedule_message(
            db, tenant, appt.client, kind="repeat_offer",
            template_key="repeat_offer",
            send_at=clock.now() + timedelta(days=appt.service.repeat_days),
            ctx=_ctx_for(appt, db, tenant),
        )


def on_appointment_cancelled(db: Session, appt: Appointment) -> None:
    """Cancelled -> drop reminders that have not gone out yet."""
    (db.query(ScheduledMessage)
       .filter(ScheduledMessage.client_id == appt.client_id,
               ScheduledMessage.kind == "reminder",
               ScheduledMessage.status == MessageStatus.pending)
       .update({ScheduledMessage.status: MessageStatus.cancelled},
               synchronize_session=False))


def set_status(db: Session, appt: Appointment, status: AppointmentStatus, tenant: Tenant):
    appt.status = status
    if status == AppointmentStatus.done:
        on_appointment_done(db, appt, tenant)
    elif status in (AppointmentStatus.cancelled, AppointmentStatus.no_show):
        on_appointment_cancelled(db, appt)
