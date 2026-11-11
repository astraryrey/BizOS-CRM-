"""
Finds genuinely free slots: working hours from YAML minus booked time.

Two consumers: the call assistant ("offer the customer this time") and
the reminder / repeat-offer templates.
"""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from . import clock
from .lifecycle import find_conflict
from .models import Resource, Service, Tenant
from .verticals import load_vertical

STEP_MIN = 30  # granularity of the search grid


def work_hours(cfg: dict) -> tuple[int, int]:
    wh = cfg.get("work_hours") or {}
    return int(wh.get("start", 9)), int(wh.get("end", 19))


def work_days(cfg: dict) -> list[int]:
    """1 = Monday ... 7 = Sunday."""
    return cfg.get("work_days") or [1, 2, 3, 4, 5, 6]


def find_free_slots(
    db: Session, tenant: Tenant, service: Service,
    resource_id: int | None = None, days: int = 10, limit: int = 6,
    start_from: datetime | None = None,
) -> list[dict]:
    """
    Returns the next free slots as
    [{'starts_at', 'resource_id', 'resource_name', 'human'}].
    With no resource_id it searches across every resource.
    """
    cfg = load_vertical(tenant.vertical)
    h_start, h_end = work_hours(cfg)
    wdays = work_days(cfg)

    q = db.query(Resource).filter(Resource.tenant_id == tenant.id, Resource.active)
    if resource_id is not None:
        q = q.filter(Resource.id == resource_id)
    resources = q.all()
    if not resources:
        return []

    now = start_from or clock.now()
    duration = timedelta(minutes=service.duration_min)
    found: list[dict] = []

    for day_offset in range(days):
        day = (now + timedelta(days=day_offset)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        if day.isoweekday() not in wdays:
            continue

        cursor = day.replace(hour=h_start)
        day_end = day.replace(hour=h_end)
        while cursor + duration <= day_end:
            if cursor <= now:                      # never offer the past
                cursor += timedelta(minutes=STEP_MIN)
                continue
            for res in resources:
                if find_conflict(db, tenant, res.id, cursor, cursor + duration) is None:
                    found.append({
                        "starts_at": cursor.isoformat(timespec="minutes"),
                        "resource_id": res.id,
                        "resource_name": res.name,
                        "human": _human(cursor, res.name, len(resources) > 1),
                    })
                    break                           # one slot per time
            if len(found) >= limit:
                return found
            cursor += timedelta(minutes=STEP_MIN)

    return found


WD_SHORT = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}


def _human(dt: datetime, resource_name: str, with_name: bool) -> str:
    base = f"{WD_SHORT[dt.isoweekday()]} {dt.day:02d}.{dt.month:02d} {dt:%H:%M}"
    return f"{base} ({resource_name})" if with_name else base


def slots_text(slots: list[dict], limit: int = 3) -> str:
    """One line for a message template: 'Mon 18.08 10:00, Tue 19.08 14:00'."""
    if not slots:
        return "call us to confirm"
    return ", ".join(s["human"] for s in slots[:limit])
