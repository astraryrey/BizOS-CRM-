"""
Calendar output as .ics, per the iCalendar standard (RFC 5545).

Why .ics rather than "a Google Calendar integration":
  * every calendar understands it - Apple, Google, Outlook, Android;
  * no OAuth, no API keys, no cloud project to register;
  * the customer installs nothing, the file just opens.

Two modes:
  1. ONE EVENT - a file for a single booking. The operator downloads it or
     sends it on; opening it drops the visit into the customer's phone.
  2. SUBSCRIPTION - a stylist adds one link to their calendar and their
     schedule keeps itself up to date. No console, no login, no training.
"""
import hashlib
import hmac
import os
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from . import clock
from .models import (
    Appointment, AppointmentStatus, Client, Resource, Service, Tenant,
)

PRODID = "-//BizOS//Digital Administrator//EN"
SECRET = os.getenv("CALENDAR_SECRET", "dev-secret-change-me")


def feed_token(tenant_id: int, resource_id: int) -> str:
    """
    Token for the subscription link. It ends up inside somebody else's
    calendar app, so it must not be guessable by walking ids.
    """
    msg = f"{tenant_id}:{resource_id}".encode()
    return hmac.new(SECRET.encode(), msg, hashlib.sha256).hexdigest()[:20]


def check_token(tenant_id: int, resource_id: int, token: str) -> bool:
    return hmac.compare_digest(feed_token(tenant_id, resource_id), token or "")


def _esc(text: str | None) -> str:
    """Escape per the RFC: commas, semicolons, newlines."""
    if not text:
        return ""
    return (str(text).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line: str) -> str:
    """
    Lines longer than 75 octets must be folded, or strict calendars
    (Apple first among them) reject the file.
    """
    raw = line.encode("utf-8")
    if len(raw) <= 73:
        return line
    parts, buf = [], raw
    while len(buf) > 73:
        cut = 73
        while cut > 0 and (buf[cut] & 0xC0) == 0x80:
            cut -= 1
        parts.append(buf[:cut].decode("utf-8"))
        buf = buf[cut:]
    parts.append(buf.decode("utf-8"))
    return "\r\n ".join(parts)


def _local(dt: datetime) -> str:
    """Floating local time: the calendar shows the hour the console shows."""
    return dt.strftime("%Y%m%dT%H%M%S")


def _utc_stamp() -> str:
    return datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def _event_lines(appt: Appointment, tenant: Tenant, service: Service | None,
                 client: Client | None, resource: Resource | None,
                 labels: dict) -> list[str]:
    who = (client.name or client.phone) if client else f"client #{appt.client_id}"
    what = service.name if service else "service"
    asset = appt.asset.title if appt.asset else None

    summary = f"{what} — {who}"
    if asset:
        summary += f" ({asset})"

    desc = [f"Customer: {who}"]
    if client and client.phone:
        desc.append(f"Phone: {client.phone}")
    if asset:
        desc.append(f"{labels.get('asset', 'Asset')}: {asset}")
    if resource:
        desc.append(f"{labels.get('resource', 'Assigned to')}: {resource.name}")
    if service:
        desc.append(f"Duration: {service.duration_min} min · {service.price:.0f} UAH")
    if appt.comment:
        desc.append(f"Note: {appt.comment}")
    desc.append(f"Booking #{appt.id} · {tenant.name}")

    status = ("CANCELLED" if appt.status in (AppointmentStatus.cancelled,
                                             AppointmentStatus.no_show)
              else "CONFIRMED" if appt.status == AppointmentStatus.confirmed
              else "TENTATIVE")

    return [
        "BEGIN:VEVENT",
        f"UID:appt-{appt.id}-t{tenant.id}@bizos",
        f"DTSTAMP:{_utc_stamp()}",
        f"DTSTART:{_local(appt.starts_at)}",
        f"DTEND:{_local(appt.ends_at)}",
        f"SUMMARY:{_esc(summary)}",
        f"DESCRIPTION:{_esc(chr(10).join(desc))}",
        f"LOCATION:{_esc(tenant.name)}",
        f"STATUS:{status}",
        f"SEQUENCE:{int(appt.starts_at.timestamp()) % 10000}",
        "BEGIN:VALARM",
        "TRIGGER:-PT1H",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{_esc('Coming up: ' + summary)}",
        "END:VALARM",
        "END:VEVENT",
    ]


def _wrap(body: list[str], calname: str | None = None, refresh_min: int = 15) -> str:
    head = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}",
            "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
    if calname:
        head += [f"X-WR-CALNAME:{_esc(calname)}",
                 "X-WR-TIMEZONE:Europe/Kiev",
                 f"REFRESH-INTERVAL;VALUE=DURATION:PT{refresh_min}M",
                 f"X-PUBLISHED-TTL:PT{refresh_min}M"]
    all_lines = head + body + ["END:VCALENDAR"]
    return "\r\n".join(_fold(l) for l in all_lines) + "\r\n"


def _lookup(db: Session, appt: Appointment):
    return (db.get(Service, appt.service_id),
            db.get(Client, appt.client_id),
            db.get(Resource, appt.resource_id) if appt.resource_id else None)


def event_ics(db: Session, tenant: Tenant, appt: Appointment, labels: dict) -> str:
    """File for a single booking."""
    service, client, resource = _lookup(db, appt)
    return _wrap(_event_lines(appt, tenant, service, client, resource, labels))


def feed_ics(db: Session, tenant: Tenant, labels: dict,
             resource: Resource | None = None,
             days_back: int = 30, days_ahead: int = 120) -> str:
    now = clock.now()
    q = db.query(Appointment).filter(
        Appointment.tenant_id == tenant.id,
        Appointment.starts_at >= now - timedelta(days=days_back),
        Appointment.starts_at <= now + timedelta(days=days_ahead))
    if resource is not None:
        q = q.filter(Appointment.resource_id == resource.id)

    body: list[str] = []
    for appt in q.order_by(Appointment.starts_at).all():
        service, client, res = _lookup(db, appt)
        body += _event_lines(appt, tenant, service, client, res, labels)

    calname = (f"{tenant.name} - {resource.name}" if resource
               else f"{tenant.name} - all bookings")
    return _wrap(body, calname=calname)
