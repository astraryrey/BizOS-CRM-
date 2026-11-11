"""HTTP API. Tenant comes from the X-Tenant-Id header (JWT in production)."""
from datetime import datetime, timedelta

from fastapi import (
    APIRouter, Depends, File, Form, Header, HTTPException, Query, Response,
    UploadFile, WebSocket, WebSocketDisconnect,
)
from sqlalchemy.orm import Session

from .. import clock
from ..calendar_feed import check_token, event_ics, feed_ics, feed_token
from ..call_brief import build_brief
from ..db import SessionLocal, get_db
from ..integrations.billing import get_billing
from ..integrations.csv_import import import_clients
from ..integrations.grusher import get_maps
from ..integrations.sms import analyze, get_sms_gateway, sandbox
from ..intents import SESSIONS, drop_session, get_session, handle_utterance
from ..lifecycle import (
    IntegrityError, find_conflict, on_appointment_created, set_status,
    validate_appointment_parts,
)
from ..models import (
    Appointment, AppointmentStatus, Asset, AuditLog, Client, Invoice,
    InvoiceStatus, MessageStatus, Resource, ScheduledMessage, Service, Tenant,
)
from ..realtime import hub
from ..scheduler import dispatch_due_messages
from ..schemas import (
    AppointmentIn, AppointmentOut, AssetIn, AssetOut, ClientCreateResult, ClientIn,
    ClientOut, IncomingCallIn, InvoiceOut, MessageOut, ResourceIn, ResourceOut,
    ServiceOut, SlotCheckOut, StatusIn, TimeTravelIn,
)
from ..slots import find_free_slots
from ..verticals import VerticalNotFound, list_verticals, load_vertical

router = APIRouter()


# --------------------------------------------------------------- helpers ----
def get_tenant(db: Session = Depends(get_db),
               x_tenant_id: int = Header(default=1)) -> Tenant:
    tenant = db.get(Tenant, x_tenant_id)
    if not tenant:
        raise HTTPException(404, f"Business (tenant) #{x_tenant_id} not found")
    return tenant


def audit(db: Session, tenant: Tenant, action: str, entity: str, entity_id: int):
    db.add(AuditLog(tenant_id=tenant.id, action=action,
                    entity=entity, entity_id=entity_id))


def _card(db: Session, client: Client) -> dict:
    appts = (db.query(Appointment).filter(Appointment.client_id == client.id)
               .order_by(Appointment.starts_at.desc()).all())
    return {"client": ClientOut.model_validate(client),
            "assets": [AssetOut.model_validate(a) for a in client.assets],
            "appointments": [AppointmentOut.model_validate(a) for a in appts]}


# --------------------------------------------------------------- tenants ----
@router.get("/tenants")
def tenants_list(db: Session = Depends(get_db)):
    out = []
    for t in db.query(Tenant).order_by(Tenant.id).all():
        cfg = load_vertical(t.vertical)
        out.append({"id": t.id, "name": t.name, "vertical": t.vertical,
                    "vertical_title": cfg.get("title"),
                    "labels": cfg.get("labels", {}),
                    "asset_fields": cfg.get("asset_fields", []),
                    "billing_account": t.billing_account,
                    "lat": t.lat, "lon": t.lon})
    return out


# ------------------------------------------------------------- verticals ----
@router.get("/verticals")
def verticals():
    return list_verticals()


@router.get("/verticals/{name}")
def vertical_config(name: str):
    try:
        return load_vertical(name)
    except VerticalNotFound as e:
        raise HTTPException(404, {"error": str(e), "available": e.available})


# --------------------------------------------------------------- clients ----
@router.get("/clients", response_model=list[ClientOut])
def clients_list(q: str = "", db: Session = Depends(get_db),
                 tenant=Depends(get_tenant)):
    query = db.query(Client).filter(Client.tenant_id == tenant.id)
    if q:
        query = query.filter(Client.phone.contains(q) | Client.name.contains(q))
    return query.order_by(Client.id.desc()).limit(200).all()


@router.post("/clients", response_model=ClientCreateResult)
def client_create(data: ClientIn, db: Session = Depends(get_db),
                  tenant=Depends(get_tenant)):
    """Idempotent on phone number: calling it twice does not create a duplicate."""
    existing = (db.query(Client)
                  .filter(Client.tenant_id == tenant.id, Client.phone == data.phone)
                  .first())
    if existing:
        return ClientCreateResult(
            created=False, client=ClientOut.model_validate(existing),
            message=f"A client with {data.phone} already exists (id={existing.id})")

    client = Client(tenant_id=tenant.id, **data.model_dump())

    # INTEGRATION POINT (maps): address -> coordinates
    if client.address:
        maps = get_maps()
        if maps.name != "null":
            try:
                pt = maps.geocode(client.address)
                if pt:
                    client.lat, client.lon = pt["lat"], pt["lon"]
            except Exception:
                pass

    db.add(client)
    db.commit()
    audit(db, tenant, "create", "client", client.id)
    db.commit()
    return ClientCreateResult(created=True,
                              client=ClientOut.model_validate(client),
                              message="Client created")


@router.get("/clients/{client_id}/card")
def client_card(client_id: int, db: Session = Depends(get_db),
                tenant=Depends(get_tenant)):
    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(404, f"Client #{client_id} not found")
    if client.tenant_id != tenant.id:
        raise HTTPException(404, f"Client #{client_id} belongs to business "
                                 f"#{client.tenant_id}. Change X-Tenant-Id.")
    return _card(db, client)


@router.get("/clients/{client_id}/appointments", response_model=list[AppointmentOut])
def client_appointments(client_id: int, db: Session = Depends(get_db),
                        tenant=Depends(get_tenant)):
    """EVERY booking for this client, with no date filter."""
    client = db.get(Client, client_id)
    if not client or client.tenant_id != tenant.id:
        raise HTTPException(404, f"Client #{client_id} not found in this business")
    return (db.query(Appointment).filter(Appointment.client_id == client_id)
              .order_by(Appointment.starts_at.desc()).all())


# ---------------------------------------------------------------- assets ----
@router.post("/assets", response_model=AssetOut)
def asset_create(data: AssetIn, db: Session = Depends(get_db),
                 tenant=Depends(get_tenant)):
    client = db.get(Client, data.client_id)
    if not client or client.tenant_id != tenant.id:
        raise HTTPException(400, f"Client #{data.client_id} not found in this business")

    cfg = load_vertical(tenant.vertical)
    if not cfg.get("asset_type"):
        raise HTTPException(400, f"The {cfg.get('title')} vertical does not use assets")

    asset = Asset(tenant_id=tenant.id, type=cfg["asset_type"], **data.model_dump())
    db.add(asset)
    db.commit()
    return asset


@router.get("/assets/{asset_id}", response_model=AssetOut)
def asset_get(asset_id: int, db: Session = Depends(get_db),
              tenant=Depends(get_tenant)):
    asset = db.get(Asset, asset_id)
    if not asset or asset.tenant_id != tenant.id:
        raise HTTPException(404, f"Asset #{asset_id} not found in this business")
    return asset


# ------------------------------------------------------------- resources ----
@router.get("/resources", response_model=list[ResourceOut])
def resources_list(db: Session = Depends(get_db), tenant=Depends(get_tenant)):
    return db.query(Resource).filter(Resource.tenant_id == tenant.id,
                                     Resource.active).all()


@router.post("/resources", response_model=ResourceOut)
def resource_create(data: ResourceIn, db: Session = Depends(get_db),
                    tenant=Depends(get_tenant)):
    res = Resource(tenant_id=tenant.id, **data.model_dump())
    db.add(res)
    db.commit()
    return res


@router.get("/resources/{resource_id}/schedule", response_model=list[AppointmentOut])
def resource_schedule(resource_id: int, date_from: datetime | None = None,
                      date_to: datetime | None = None,
                      db: Session = Depends(get_db), tenant=Depends(get_tenant)):
    res = db.get(Resource, resource_id)
    if not res or res.tenant_id != tenant.id:
        raise HTTPException(404, f"Resource #{resource_id} not found")
    q = db.query(Appointment).filter(Appointment.tenant_id == tenant.id,
                                     Appointment.resource_id == resource_id)
    if date_from:
        q = q.filter(Appointment.starts_at >= date_from)
    if date_to:
        q = q.filter(Appointment.starts_at <= date_to)
    return q.order_by(Appointment.starts_at).all()


# -------------------------------------------------------------- services ----
@router.get("/services", response_model=list[ServiceOut])
def services_list(db: Session = Depends(get_db), tenant=Depends(get_tenant)):
    return db.query(Service).filter(Service.tenant_id == tenant.id,
                                    Service.active).all()


# ---------------------------------------------------------- appointments ----
@router.get("/appointments", response_model=list[AppointmentOut])
def appointments_list(
    include_past: bool = Query(False, description="Include past bookings"),
    days_ahead: int = Query(7), date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None), resource_id: int | None = Query(None),
    db: Session = Depends(get_db), tenant=Depends(get_tenant),
):
    q = db.query(Appointment).filter(Appointment.tenant_id == tenant.id)
    if date_from:
        q = q.filter(Appointment.starts_at >= date_from)
    elif not include_past:
        q = q.filter(Appointment.starts_at >= clock.now() - timedelta(hours=12))
    if date_to:
        q = q.filter(Appointment.starts_at <= date_to)
    elif not include_past:
        q = q.filter(Appointment.starts_at <= clock.now() + timedelta(days=days_ahead))
    if resource_id is not None:
        q = q.filter(Appointment.resource_id == resource_id)
    return q.order_by(Appointment.starts_at).limit(500).all()


@router.get("/appointments/check-slot", response_model=SlotCheckOut)
def check_slot(service_id: int, starts_at: datetime,
               resource_id: int | None = None,
               db: Session = Depends(get_db), tenant=Depends(get_tenant)):
    service = db.get(Service, service_id)
    if not service or service.tenant_id != tenant.id:
        raise HTTPException(404, f"Service #{service_id} not found")
    ends_at = starts_at + timedelta(minutes=service.duration_min)
    conflict = find_conflict(db, tenant, resource_id, starts_at, ends_at)
    if conflict:
        return SlotCheckOut(free=False,
                            reason=(f"Taken: booking #{conflict.id} from "
                                    f"{conflict.starts_at:%d.%m %H:%M} "
                                    f"to {conflict.ends_at:%H:%M}"),
                            conflict_appointment_id=conflict.id)
    return SlotCheckOut(free=True)


@router.get("/appointments/{appt_id}", response_model=AppointmentOut)
def appointment_get(appt_id: int, db: Session = Depends(get_db),
                    tenant=Depends(get_tenant)):
    appt = db.get(Appointment, appt_id)
    if not appt:
        raise HTTPException(404, f"Booking #{appt_id} not found")
    if appt.tenant_id != tenant.id:
        raise HTTPException(404, f"Booking #{appt_id} belongs to business "
                                 f"#{appt.tenant_id}")
    return appt


@router.post("/appointments", response_model=AppointmentOut)
def appointment_create(data: AppointmentIn, db: Session = Depends(get_db),
                       tenant=Depends(get_tenant)):
    try:
        client, service, asset, resource = validate_appointment_parts(
            db, tenant, data.client_id, data.service_id,
            data.asset_id, data.resource_id)
    except IntegrityError as e:
        raise HTTPException(400, str(e))

    ends_at = data.starts_at + timedelta(minutes=service.duration_min)
    conflict = find_conflict(db, tenant, data.resource_id, data.starts_at, ends_at)
    if conflict:
        cfg = load_vertical(tenant.vertical)
        label = (cfg.get("labels") or {}).get("resource", "Resource")
        raise HTTPException(409,
            f"{label} '{resource.name}' is busy: booking #{conflict.id} "
            f"from {conflict.starts_at:%d.%m %H:%M} to {conflict.ends_at:%H:%M}. "
            f"Pick another time or another {label.lower()}.")

    appt = Appointment(tenant_id=tenant.id, ends_at=ends_at, **data.model_dump())
    db.add(appt)
    db.flush()
    on_appointment_created(db, appt, tenant)
    audit(db, tenant, "create", "appointment", appt.id)
    db.commit()
    return appt


@router.post("/appointments/{appt_id}/status", response_model=AppointmentOut)
def appointment_status(appt_id: int, data: StatusIn,
                       db: Session = Depends(get_db), tenant=Depends(get_tenant)):
    appt = db.get(Appointment, appt_id)
    if not appt or appt.tenant_id != tenant.id:
        raise HTTPException(404, f"Booking #{appt_id} not found in this business")
    try:
        status = AppointmentStatus(data.status)
    except ValueError:
        raise HTTPException(400, f"Unknown status '{data.status}'. Allowed: "
                                 f"{[s.value for s in AppointmentStatus]}")
    set_status(db, appt, status, tenant)
    db.commit()
    return appt


# ------------------------------------------------------ invoices/messages ----
@router.get("/invoices", response_model=list[InvoiceOut])
def invoices_list(db: Session = Depends(get_db), tenant=Depends(get_tenant)):
    return (db.query(Invoice).filter(Invoice.tenant_id == tenant.id)
              .order_by(Invoice.id.desc()).limit(100).all())


@router.post("/invoices/{invoice_id}/refresh", response_model=InvoiceOut)
def invoice_refresh(invoice_id: int, db: Session = Depends(get_db),
                    tenant=Depends(get_tenant)):
    """
    Ask the billing system whether the invoice has been paid.
    INTEGRATION POINT (billing): payment check.
    """
    inv = db.get(Invoice, invoice_id)
    if not inv or inv.tenant_id != tenant.id:
        raise HTTPException(404, f"Invoice #{invoice_id} not found")
    if not inv.external_id:
        raise HTTPException(400, "Invoice was never pushed to billing - nothing to check")

    billing = get_billing()
    try:
        res = billing.check_payment(inv.external_id)
    except Exception as e:
        raise HTTPException(502, f"Billing unreachable: {e}")

    if res.get("paid"):
        inv.status = InvoiceStatus.paid
        inv.paid_at = clock.now()
        db.commit()
    return inv


@router.get("/messages", response_model=list[MessageOut])
def messages_list(kind: str | None = Query(None), status: str | None = Query(None),
                  db: Session = Depends(get_db), tenant=Depends(get_tenant)):
    q = db.query(ScheduledMessage).filter(ScheduledMessage.tenant_id == tenant.id)
    if kind:
        q = q.filter(ScheduledMessage.kind == kind)
    if status:
        q = q.filter(ScheduledMessage.status == status)
    return q.order_by(ScheduledMessage.send_at.desc()).limit(200).all()


# ------------------------------------------------------------------ slots ----
@router.get("/slots")
def slots(service_id: int, resource_id: int | None = None, limit: int = 8,
          db: Session = Depends(get_db), tenant=Depends(get_tenant)):
    service = db.get(Service, service_id)
    if not service or service.tenant_id != tenant.id:
        raise HTTPException(404, f"Service #{service_id} not found")
    return find_free_slots(db, tenant, service, resource_id=resource_id, limit=limit)


# ============================================================== TELEPHONY ====
@router.api_route("/telephony/incoming", methods=["POST", "GET"])
async def telephony_incoming(
    phone: str = Query(..., description="Caller ID of the incoming call"),
    tenant_id: int = Query(1), call_id: str | None = Query(None),
):
    """
    INCOMING CALL WEBHOOK - this is what Asterisk calls.

    Accepts POST (what Asterisk sends) and GET, so the link can simply be
    opened in a browser to check it.

    The line to add to the Asterisk dialplan:
      System(curl -s -X POST "http://SERVER:8000/api/telephony/incoming
             ?phone=${CALLERID(num)}&tenant_id=1" &)
    """
    db = SessionLocal()
    try:
        tenant = db.get(Tenant, tenant_id)
        if not tenant:
            raise HTTPException(404, f"Business #{tenant_id} not found")
        brief = build_brief(db, tenant, phone)
    finally:
        db.close()

    brief["call_id"] = call_id
    delivered = await hub.push(tenant_id, {"type": "incoming_call", "brief": brief})
    return {"ok": True, "delivered_to_panels": delivered, "brief": brief}


@router.get("/telephony/brief")
def telephony_brief(phone: str, db: Session = Depends(get_db),
                    tenant=Depends(get_tenant)):
    return build_brief(db, tenant, phone)


@router.websocket("/ws/panel/{tenant_id}")
async def panel_socket(websocket: WebSocket, tenant_id: int):
    await hub.join(tenant_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.leave(tenant_id, websocket)
    except Exception:
        hub.leave(tenant_id, websocket)


# ==================================================== CALL ASSIST ===========
@router.post("/assist/utterance")
async def assist_utterance(
    call_id: str = Query(...), text: str = Query(...),
    speaker: str = Query("caller"), phone: str = Query(""),
    tenant_id: int = Query(1),
):
    """
    Takes one RECOGNISED UTTERANCE and returns prompts.
    `speak` is the short line for the earpiece, `screen` the detail for
    the console. The module does not know where the text came from, which
    is why the whole engine is testable without a byte of audio.
    """
    db = SessionLocal()
    try:
        tenant = db.get(Tenant, tenant_id)
        if not tenant:
            raise HTTPException(404, f"Business #{tenant_id} not found")
        session = get_session(call_id, tenant_id, phone)
        if phone and not session.client_id:
            cl = db.query(Client).filter(Client.tenant_id == tenant_id,
                                         Client.phone == phone).first()
            if cl:
                session.client_id = cl.id
        prompts = handle_utterance(db, tenant, session, text, speaker=speaker)
    finally:
        db.close()

    if prompts:
        await hub.push(tenant_id, {"type": "assist_prompt", "call_id": call_id,
                                   "prompts": prompts})
    return {"call_id": call_id, "speaker": speaker, "heard": text,
            "prompts": prompts,
            "state": {"service_id": session.service_id,
                      "slot_index": session.slot_index,
                      "slots_left": max(0, len(session.slots) - session.slot_index - 1),
                      "client_id": session.client_id,
                      "guessed_name": session.guessed_name}}


@router.post("/assist/book")
def assist_book(call_id: str = Query(...), db: Session = Depends(get_db),
                tenant=Depends(get_tenant)):
    """The customer agreed - create the booking from what was discussed."""
    session = SESSIONS.get(call_id)
    if not session or not session.pending or session.pending.get("type") != "book":
        raise HTTPException(400, "Nothing in this call is waiting for confirmation")
    if not session.client_id:
        raise HTTPException(400, "Client not identified - create a record first")

    slot = session.pending["slot"]
    service = db.get(Service, session.pending["service_id"])
    starts = datetime.fromisoformat(slot["starts_at"])

    conflict = find_conflict(db, tenant, slot["resource_id"], starts,
                             starts + timedelta(minutes=service.duration_min))
    if conflict:
        raise HTTPException(409, "That slot was just taken - offer another time")

    client = db.get(Client, session.client_id)
    asset = client.assets[0] if client.assets else None
    appt = Appointment(tenant_id=tenant.id, client_id=client.id,
                       asset_id=asset.id if asset else None,
                       service_id=service.id, resource_id=slot["resource_id"],
                       starts_at=starts,
                       ends_at=starts + timedelta(minutes=service.duration_min),
                       source="call")
    db.add(appt)
    db.flush()
    on_appointment_created(db, appt, tenant)
    db.commit()

    session.booked_appointment_id = appt.id
    session.pending = None
    return {"ok": True, "appointment_id": appt.id,
            "when": slot["human"], "service": service.name}


@router.get("/assist/session/{call_id}")
def assist_session(call_id: str):
    s = SESSIONS.get(call_id)
    if not s:
        raise HTTPException(404, "Call not found")
    return {"call_id": s.call_id, "phone": s.phone, "client_id": s.client_id,
            "service_id": s.service_id, "slot_index": s.slot_index,
            "slots": s.slots[:6], "guessed_name": s.guessed_name,
            "booked_appointment_id": s.booked_appointment_id,
            "transcript": s.transcript}


@router.delete("/assist/session/{call_id}")
def assist_session_end(call_id: str):
    drop_session(call_id)
    return {"ok": True}


# ================================================================= MAPS =====
# INTEGRATION POINT (maps) - everything below goes through the adapter.

@router.get("/maps/overview")
def maps_overview(db: Session = Depends(get_db), tenant=Depends(get_tenant)):
    """What to draw: the premises, geocoded clients, service zones, nodes."""
    maps = get_maps()
    clients = (db.query(Client)
                 .filter(Client.tenant_id == tenant.id, Client.lat.isnot(None))
                 .limit(300).all())

    zones, nodes = [], []
    if maps.name == "mock-grusher":
        import httpx
        try:
            base = maps.base_url
            zones = httpx.get(f"{base}/api/zones", timeout=3).json()
            nodes = httpx.get(f"{base}/api/nodes", timeout=3).json()
        except Exception:
            pass

    return {
        "adapter": maps.name,
        "tenant": {"id": tenant.id, "name": tenant.name,
                   "lat": tenant.lat, "lon": tenant.lon},
        "clients": [{"id": c.id, "name": c.name, "phone": c.phone,
                     "address": c.address, "lat": c.lat, "lon": c.lon}
                    for c in clients],
        "zones": zones, "nodes": nodes,
    }


@router.post("/maps/geocode")
def maps_geocode(address: str = Query(...)):
    """Address -> coordinates through the mapping system."""
    maps = get_maps()
    if maps.name == "null":
        raise HTTPException(400, "Maps are not connected (MAPS_ADAPTER=null)")
    try:
        pt = maps.geocode(address)
    except NotImplementedError as e:
        raise HTTPException(501, str(e))
    except Exception as e:
        raise HTTPException(502, f"Mapping system unreachable: {e}")
    if not pt:
        raise HTTPException(404, "Address not recognised")
    return pt


@router.post("/maps/geocode-clients")
def maps_geocode_clients(db: Session = Depends(get_db), tenant=Depends(get_tenant)):
    """Geocode every client that has an address but no coordinates yet."""
    maps = get_maps()
    if maps.name == "null":
        raise HTTPException(400, "Maps are not connected (MAPS_ADAPTER=null)")

    todo = (db.query(Client)
              .filter(Client.tenant_id == tenant.id,
                      Client.address.isnot(None), Client.lat.is_(None))
              .all())
    ok, failed = 0, 0
    for c in todo:
        try:
            pt = maps.geocode(c.address)
            if pt:
                c.lat, c.lon = pt["lat"], pt["lon"]
                ok += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    db.commit()
    return {"adapter": maps.name, "processed": len(todo),
            "geocoded": ok, "failed": failed}


@router.get("/maps/route")
def maps_route(date: str | None = Query(None, description="YYYY-MM-DD, defaults to today"),
               resource_id: int | None = Query(None),
               optimize: bool = Query(False, description="Suggest the shortest round trip "
                                      "instead of following appointment times"),
               db: Session = Depends(get_db), tenant=Depends(get_tenant)):
    """
    The on-site crew's route for a day: take bookings whose client has
    coordinates and ask the mapping system to order the trip.
    """
    maps = get_maps()
    if maps.name == "null":
        raise HTTPException(400, "Maps are not connected (MAPS_ADAPTER=null)")

    day = (datetime.fromisoformat(date) if date else clock.now()).replace(
        hour=0, minute=0, second=0, microsecond=0)

    q = (db.query(Appointment)
           .filter(Appointment.tenant_id == tenant.id,
                   Appointment.starts_at >= day,
                   Appointment.starts_at < day + timedelta(days=1),
                   Appointment.status.in_([AppointmentStatus.scheduled,
                                           AppointmentStatus.confirmed])))
    if resource_id is not None:
        q = q.filter(Appointment.resource_id == resource_id)

    points, meta = [], []
    for a in q.order_by(Appointment.starts_at).all():
        c = db.get(Client, a.client_id)
        if not c or c.lat is None:
            continue
        s = db.get(Service, a.service_id)
        if resource_id is None and s and not s.onsite:
            continue  # unfiltered, take on-site services only
        points.append({"lat": c.lat, "lon": c.lon,
                       "label": f"{c.name or c.phone} · {a.starts_at:%H:%M}"})
        meta.append({"appointment_id": a.id, "client_id": c.id,
                     "client_name": c.name, "address": c.address,
                     "service": s.name if s else None,
                     "at": a.starts_at.isoformat(timespec="minutes"),
                     "lat": c.lat, "lon": c.lon})

    if not points:
        return {"adapter": maps.name, "date": day.date().isoformat(),
                "points": [], "route": None,
                "note": "no on-site bookings with coordinates on that day"}

    try:
        route = maps.route(points, optimize=optimize)
    except Exception as e:
        raise HTTPException(502, f"Mapping system unreachable: {e}")

    result = {"adapter": maps.name, "date": day.date().isoformat(),
              "points": meta, "route": route, "optimized": optimize}

    # How much a reordered trip would save. It would mean moving times
    # already promised to customers, so this is a hint for a manager
    # rather than something to apply automatically.
    if not optimize and len(points) > 2:
        try:
            best = maps.route(points, optimize=True)
            if best.get("distance_km") and route.get("distance_km"):
                saved = round(route["distance_km"] - best["distance_km"], 2)
                if saved > 0.5:
                    result["optimization_hint"] = {
                        "could_save_km": saved,
                        "note": "a reordered trip is shorter but would mean "
                                "moving appointment times",
                    }
        except Exception:
            pass
    return result


# =================================================== INTEGRATION STATUS ====
@router.get("/integrations/status")
def integrations_status(db: Session = Depends(get_db)):
    """
    One screen showing what is connected, what is a stub, and what is
    missing. This is the map of integration points - the starting point
    for the conversation with each system's administrator.
    """
    billing = get_billing()
    maps = get_maps()
    sms = get_sms_gateway()

    return {
        "billing": {
            "name": "U-billing",
            "adapter": billing.name,
            "health": billing.health(),
            "connected": billing.name not in ("null",),
            "production": billing.name == "ubilling",
            "where_in_code": "app/integrations/billing.py",
            "needed_to_connect": [
                "BASE_URL of the billing API",
                "token or username and password",
                "method names: create invoice, find subscriber, check payment",
                "request and response format",
                "a test environment",
            ],
            "env": "BILLING_ADAPTER=ubilling UBILLING_URL=... UBILLING_TOKEN=...",
        },
        "maps": {
            "name": "Grusher (mapping)",
            "adapter": maps.name,
            "health": maps.health(),
            "connected": maps.name not in ("null",),
            "production": maps.name == "grusher",
            "where_in_code": "app/integrations/grusher.py",
            "needed_to_connect": [
                "BASE_URL of the Grusher API",
                "token or API key",
                "which objects it exposes: addresses, equipment, network nodes",
                "whether it can geocode addresses",
                "whether it computes routes",
                "coordinate format (WGS84?)",
            ],
            "env": "MAPS_ADAPTER=grusher GRUSHER_URL=... GRUSHER_TOKEN=...",
        },
        "telephony": {
            "name": "Asterisk / call centre",
            "adapter": "webhook",
            "connected": False,
            "where_in_code": "app/routers/api.py -> /api/telephony/incoming",
            "needed_to_connect": [
                "Asterisk version (16+ required)",
                "access to the dialplan (extensions.conf) or FreePBX",
                "the BizOS server must be reachable from the PBX network",
                "which public number maps to which business",
            ],
            "env": "one line in the dialplan, see ASTERISK.md",
        },
        "sms": {
            "name": "SMS gateway",
            "adapter": sms.name,
            "connected": sms.name != "console",
            "production": sms.name == "turbosms",
            "where_in_code": "app/integrations/sms.py",
            "needed_to_connect": [
                "a contract with the gateway and credit on the account",
                "a registered alphanumeric sender ID",
                "API token",
                "customer consent to be messaged",
            ],
            "env": "SMS_GATEWAY=turbosms SMS_TOKEN=... SMS_SENDER=...",
        },
        "messaging": {
            "name": "Telegram / Viber",
            "adapter": "log-stub",
            "connected": False,
            "where_in_code": "app/scheduler.py -> Notifier.send",
            "needed_to_connect": [
                "bot token from @BotFather",
                "the customer must press Start first (Telegram blocks bots from messaging first)",
                "a process that listens for bot messages",
            ],
            "env": "TELEGRAM_TOKEN=...",
        },
        "speech": {
            "name": "Speech recognition",
            "adapter": "none",
            "connected": False,
            "where_in_code": "app/intents.py (engine ready), entry point /api/assist/utterance",
            "needed_to_connect": [
                "Asterisk AudioSocket for live audio",
                "Vosk or a cloud speech-to-text service",
                "a vocabulary of services and makes to improve accuracy",
            ],
            "env": "see VOICE_ASSIST.md",
        },
    }


# ------------------------------------------------------- customer import ----
@router.post("/import/clients")
async def import_clients_csv(
    file: UploadFile = File(..., description="CSV file with the customer list"),
    dry_run: bool = Form(True), source: str = Form("csv"),
    geocode: bool = Form(False, description="Geocode addresses through the mapping system"),
    db: Session = Depends(get_db), tenant=Depends(get_tenant),
):
    """Load an existing customer list from CSV. Run dry_run=true first."""
    content = await file.read()
    if not content:
        raise HTTPException(400, "The file is empty")
    try:
        return import_clients(db, tenant, content, source=source,
                              dry_run=dry_run, geocode=geocode)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ================================================================ CALENDAR ===
ICS_MEDIA = "text/calendar; charset=utf-8"


@router.get("/appointments/{appt_id}/ics")
def appointment_ics(appt_id: int, db: Session = Depends(get_db),
                    tenant=Depends(get_tenant)):
    appt = db.get(Appointment, appt_id)
    if not appt or appt.tenant_id != tenant.id:
        raise HTTPException(404, f"Booking #{appt_id} not found")
    labels = (load_vertical(tenant.vertical).get("labels") or {})
    return Response(content=event_ics(db, tenant, appt, labels),
                    media_type=ICS_MEDIA,
                    headers={"Content-Disposition":
                             f'attachment; filename="appointment-{appt_id}.ics"'})


@router.get("/calendar/links")
def calendar_links(db: Session = Depends(get_db), tenant=Depends(get_tenant)):
    """Subscription links: added once, the schedule then keeps itself current."""
    out = []
    for r in db.query(Resource).filter(Resource.tenant_id == tenant.id,
                                       Resource.active).all():
        token = feed_token(tenant.id, r.id)
        out.append({"resource_id": r.id, "resource_name": r.name, "role": r.role,
                    "path": f"/api/calendar/{tenant.id}/{r.id}/feed.ics?token={token}",
                    "hint": "Calendar -> Add calendar -> Subscribe to calendar"})
    return out


@router.get("/calendar/{tenant_id}/{resource_id}/feed.ics")
def calendar_feed(tenant_id: int, resource_id: int, token: str = Query(...),
                  db: Session = Depends(get_db)):
    """Subscription feed. No X-Tenant-Id: a calendar app reads this, and it
    knows nothing about our headers. Hence the token in the URL."""
    if not check_token(tenant_id, resource_id, token):
        raise HTTPException(403, "Invalid subscription link")
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Business not found")
    resource = db.get(Resource, resource_id) if resource_id else None
    if resource and resource.tenant_id != tenant_id:
        raise HTTPException(404, "Resource not found")
    labels = (load_vertical(tenant.vertical).get("labels") or {})
    return Response(content=feed_ics(db, tenant, labels, resource=resource),
                    media_type=ICS_MEDIA)


# ================================================= MESSAGING TEST BENCH ====
# Prove the reminder machinery works without sending anything real or
# waiting two days. See TESTING.md.
@router.post("/test/dispatch")
def test_dispatch():
    """Run the scheduler right now instead of waiting for its 30s cycle."""
    return {"dispatched": dispatch_due_messages()}


@router.get("/test/sms/outbox")
def sms_outbox(limit: int = 50):
    sb = sandbox()
    return {"gateway": get_sms_gateway().name, "stats": sb.stats(),
            "messages": list(reversed(sb.outbox))[:limit]}


@router.post("/test/sms/clear")
def sms_clear():
    sb = sandbox()
    n = len(sb.outbox)
    sb.outbox.clear()
    sb.blocked.clear()
    return {"cleared": n}


@router.post("/test/sms/block")
def sms_block(phone: str = Query(...)):
    sandbox().blocked.add(phone)
    return {"blocked": sorted(sandbox().blocked)}


@router.post("/test/sms/fail-rate")
def sms_fail_rate(rate: float = Query(0.0, ge=0, le=1)):
    sandbox().fail_rate = rate
    return {"fail_rate": rate}


@router.get("/test/sms/preview")
def sms_preview(text: str):
    i = analyze(text)
    return {"text": i.text, "chars": i.length, "encoding": i.encoding,
            "segments": i.segments, "hint": i.cost_hint}


@router.get("/test/pending")
def pending_messages(db: Session = Depends(get_db), tenant=Depends(get_tenant)):
    """The queue: what the system intends to send, to whom and when."""
    now = clock.now()
    rows = (db.query(ScheduledMessage)
              .filter(ScheduledMessage.tenant_id == tenant.id,
                      ScheduledMessage.status == MessageStatus.pending)
              .order_by(ScheduledMessage.send_at).limit(100).all())
    out = []
    for m in rows:
        i = analyze(m.rendered_text or "")
        out.append({"id": m.id, "client_id": m.client_id, "channel": m.channel,
                    "kind": m.kind,
                    "send_at": m.send_at.isoformat(timespec="minutes"),
                    "in_hours": round((m.send_at - now).total_seconds() / 3600, 1),
                    "text": m.rendered_text, "segments": i.segments,
                    "encoding": i.encoding})
    return {"virtual_now": now.isoformat(timespec="minutes"), "pending": out}


# ------------------------------------------------------------------ demo ----
@router.post("/demo/incoming-call")
def incoming_call(data: IncomingCallIn, db: Session = Depends(get_db),
                  tenant=Depends(get_tenant)):
    return build_brief(db, tenant, data.phone)


@router.post("/demo/timetravel")
def timetravel(data: TimeTravelIn):
    now = clock.travel(days=data.days, hours=data.hours)
    sent = dispatch_due_messages()
    return {"virtual_now": now.isoformat(), "messages_dispatched": sent}


@router.post("/demo/reset-clock")
def reset_clock():
    return {"virtual_now": clock.reset().isoformat()}


@router.get("/demo/now")
def demo_now():
    return {"virtual_now": clock.now().isoformat()}
