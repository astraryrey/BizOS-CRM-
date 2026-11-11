"""
The call assistant: builds the brief an operator sees in the first second
of an incoming call.

No speech recognition involved — the caller's number is known before the
handset is even picked up, and everything here is derived from it.
"""
from datetime import datetime

from sqlalchemy.orm import Session

from . import clock
from .integrations.sms import normalize_msisdn
from .models import (
    Appointment, AppointmentStatus, Client, Invoice, InvoiceStatus, Service, Tenant,
)
from .slots import find_free_slots
from .verticals import load_vertical

# A service counts as "due" once 80% of its interval has elapsed.
DUE_FACTOR = 0.8


def _fmt(dt: datetime) -> str:
    return f"{dt.day:02d}.{dt.month:02d}.{dt.year} {dt:%H:%M}"


def _plural_days(n: int) -> str:
    return f"{n} day" if n == 1 else f"{n} days"


def _find_client(db: Session, tenant: Tenant, phone: str) -> Client | None:
    """
    Telephony hands over numbers in whatever shape it likes: Asterisk
    without the plus, a mobile with it, somebody else with a leading zero.
    Try every spelling, or a known caller looks like a stranger.
    """
    variants = {phone}
    norm = normalize_msisdn(phone)
    if norm:
        variants.add(norm)
        variants.add(norm.lstrip("+"))
    digits = "".join(ch for ch in phone if ch.isdigit())
    if digits:
        variants.add(digits)
        variants.add("+" + digits)

    q = db.query(Client).filter(Client.tenant_id == tenant.id)
    client = q.filter(Client.phone.in_(list(variants))).first()
    if client:
        return client

    # Last resort: match on the final nine digits, ignoring country code.
    tail = digits[-9:]
    if len(tail) == 9:
        for c in q.all():
            if "".join(ch for ch in c.phone if ch.isdigit()).endswith(tail):
                return c
    return None


def build_brief(db: Session, tenant: Tenant, phone: str) -> dict:
    cfg = load_vertical(tenant.vertical)
    labels = cfg.get("labels") or {}
    now = clock.now()

    client = _find_client(db, tenant, phone)

    # --- unknown number ---------------------------------------------------
    if not client:
        services = db.query(Service).filter(
            Service.tenant_id == tenant.id, Service.active).all()
        slots = find_free_slots(db, tenant, services[0], limit=4) if services else []
        return {
            "known": False, "phone": phone,
            "headline": "New number - not in the database",
            "labels": labels, "client": None, "assets": [], "facts": [],
            "due": [], "upcoming": None, "debts": [], "free_slots": slots,
            "phrases": [
                "Hello! Could I take your name and what you need booked?",
                "First time with us? I'll set up a record so it's quicker next time.",
            ],
        }

    # --- history ----------------------------------------------------------
    appts = (db.query(Appointment)
               .filter(Appointment.client_id == client.id)
               .order_by(Appointment.starts_at.desc()).all())
    services = {s.id: s for s in db.query(Service).filter(
        Service.tenant_id == tenant.id).all()}

    done = [a for a in appts if a.status == AppointmentStatus.done]
    upcoming = sorted(
        [a for a in appts if a.starts_at > now
         and a.status in (AppointmentStatus.scheduled, AppointmentStatus.confirmed)],
        key=lambda a: a.starts_at)
    no_shows = [a for a in appts if a.status == AppointmentStatus.no_show]

    facts: list[dict] = []
    if done:
        last = done[0]
        gap = (now - last.starts_at).days
        sv = services.get(last.service_id)
        facts.append({"label": "Last visit",
                      "value": f"{_fmt(last.starts_at)} - {sv.name if sv else '-'}",
                      "hint": f"{_plural_days(gap)} ago"})
    else:
        facts.append({"label": "History", "value": "no visits yet",
                      "hint": "record exists but they never came in"})

    facts.append({"label": "Total visits", "value": str(len(done)),
                  "hint": f"customer since {_fmt(client.created_at)[:10]}"})

    if no_shows:
        facts.append({"label": "No-shows", "value": str(len(no_shows)),
                      "hint": "worth confirming the day before"})

    # --- what is due ------------------------------------------------------
    due = []
    last_by_service: dict[int, Appointment] = {}
    for a in done:
        last_by_service.setdefault(a.service_id, a)

    for sid, last in last_by_service.items():
        sv = services.get(sid)
        if not sv or not sv.repeat_days:
            continue
        gap = (now - last.starts_at).days
        if gap >= sv.repeat_days * DUE_FACTOR:
            overdue = gap - sv.repeat_days
            due.append({"service_id": sv.id, "service_name": sv.name,
                        "price": sv.price, "days_since": gap,
                        "interval": sv.repeat_days,
                        "overdue_days": max(0, overdue),
                        "status": "overdue" if overdue > 0 else "due now"})
    due.sort(key=lambda d: -d["days_since"])

    # --- unpaid invoices --------------------------------------------------
    appt_ids = [a.id for a in appts]
    debts = []
    if appt_ids:
        unpaid = (db.query(Invoice)
                    .filter(Invoice.tenant_id == tenant.id,
                            Invoice.appointment_id.in_(appt_ids),
                            Invoice.status.in_([InvoiceStatus.sent,
                                                InvoiceStatus.overdue]))
                    .all())
        debts = [{"invoice_id": i.id, "amount": i.amount,
                  "issued_at": _fmt(i.issued_at), "pay_link": i.pay_link,
                  "external_id": i.external_id} for i in unpaid]

    # --- free slots -------------------------------------------------------
    target = services.get(due[0]["service_id"]) if due else None
    if target is None and done:
        target = services.get(done[0].service_id)
    if target is None:
        active = [s for s in services.values() if s.active]
        target = active[0] if active else None
    slots = find_free_slots(db, tenant, target, limit=6) if target else []

    # --- ready-made phrases ----------------------------------------------
    name = client.name or "there"
    asset_title = client.assets[0].title if client.assets else None
    phrases = [f"Hello {name}!"]

    if upcoming:
        nxt = upcoming[0]
        sv = services.get(nxt.service_id)
        phrases.append(f"You're booked for {_fmt(nxt.starts_at)} - "
                       f"{sv.name if sv else 'a service'}. Confirm or move it?")
    elif due:
        d = due[0]
        subject = f"{asset_title} is " if asset_title else "You're "
        phrases.append(f"{subject}due for {d['service_name']} "
                       f"({_plural_days(d['days_since'])} since the last one). "
                       f"Shall I find a time?")
        if slots:
            phrases.append("Next available: " +
                           ", ".join(s["human"] for s in slots[:3]))
    elif done:
        last = done[0]
        sv = services.get(last.service_id)
        phrases.append(f"Last time was {sv.name if sv else '-'}, "
                       f"{(now - last.starts_at).days} days ago. "
                       f"What shall I book you in for?")

    if debts:
        total = sum(d["amount"] for d in debts)
        phrases.append(f"Heads up: there's an unpaid invoice of {total:.0f} UAH.")

    headline = name
    if asset_title:
        headline += f" · {asset_title}"

    return {
        "known": True, "phone": client.phone, "headline": headline, "labels": labels,
        "client": {"id": client.id, "name": client.name, "phone": client.phone,
                   "notes": client.notes, "address": client.address,
                   "lat": client.lat, "lon": client.lon},
        "assets": [{"id": a.id, "title": a.title, "attributes": a.attributes or {}}
                   for a in client.assets],
        "facts": facts, "due": due,
        "upcoming": ({"id": upcoming[0].id,
                      "starts_at": upcoming[0].starts_at.isoformat(timespec="minutes"),
                      "service_name": (services[upcoming[0].service_id].name
                                       if upcoming[0].service_id in services else None),
                      "human": _fmt(upcoming[0].starts_at)} if upcoming else None),
        "debts": debts, "free_slots": slots, "phrases": phrases,
    }
