"""
Prompt engine driven by what was said on the call.

PRINCIPLE: nothing is generated. An intent is matched on keywords, a real
fact is pulled from the database, and it is poured into a fixed template.
What reaches the operator's ear is known in advance.

    recognised speech (text)
      -> detect INTENT (price / availability / later / agreement)
      -> fetch the REAL fact (price, free slot)
      -> fill the TEMPLATE
      -> return a short line for the earpiece + detail for the screen

The dialogue keeps state: once 8:00 has been offered, "anything later?"
produces the next slot rather than repeating the same one.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from . import clock
from .models import Service, Tenant
from .slots import find_free_slots
from .verticals import load_vertical

# Keyword stems, so inflected forms still match.
PATTERNS: dict[str, list[str]] = {
    "wait":     ["one moment", "just a moment", "hold on", "bear with me",
                 "let me check", "one second", "give me a second",
                 "let me have a look", "bear with"],
    "price":    ["how much", "price", "cost", "what do you charge",
                 "ballpark", "quote", "roughly"],
    "when":     ["when can", "when could", "when would", "any availability",
                 "what times", "get me in", "free slot", "appointment",
                 "schedule me", "fit me in", "when are you free"],
    "later":    ["later", "can't make", "cannot make", "doesn't work",
                 "does not work", "another time", "too early", "no good",
                 "something else", "anything else"],
    "earlier":  ["earlier", "sooner", "in the morning", "first thing"],
    "tomorrow": ["tomorrow"],
    # Be careful with polite filler: "perfect" can be a reaction to the price
    # rather than agreement, so these only fire when a slot is on the table
    # and no counter-request for another time is present.
    "confirm":  ["book it", "book me in", "that works", "sounds good",
                 "go ahead", "yes please", "let's do", "lets do",
                 "i'll take it", "ill take it", "sign me up", "that's fine",
                 "thats fine", "put me down"],
    "bye":      ["see you", "goodbye", "bye", "cheers", "thanks a lot"],
    "greeting": ["hello", "hi there", "good morning", "good afternoon",
                 "good evening", "hey there"],
    "cancel":   ["cancel", "reschedule", "move my", "can't come",
                 "cannot come", "push it back"],
}

# A negation in front flips the meaning: "doesn't work" is not agreement.
NEGATIONS = ("not", "n't", "no", "never", "cannot", "cant")

NAME_RE = re.compile(
    r"(?:my name is|this is|i am|i'm|im)\s+([A-Z][a-z]{2,20})\b"
)


def _hit(low: str, key: str) -> bool:
    """Find the key, discarding matches that sit behind a negation."""
    start = 0
    while (i := low.find(key, start)) != -1:
        before = low[max(0, i - 16):i]
        words = before.replace("'", "'").split()
        tail = " ".join(words[-3:]) if words else ""
        if not any(tail.endswith(n) or f" {n} " in f" {tail} " for n in NEGATIONS):
            return True
        start = i + 1
    return False


def detect_intents(text: str) -> list[str]:
    """Every intent found in one utterance. There can be several."""
    low = " " + text.lower() + " "
    found = []
    for intent, keys in PATTERNS.items():
        if any(_hit(low, k) for k in keys):
            found.append(intent)

    # Asking for another time always beats agreement: in "perfect, when can
    # I come in?" the word "perfect" is politeness, not a confirmation.
    if {"later", "earlier", "when", "tomorrow"} & set(found):
        found = [i for i in found if i != "confirm"]
    return found


def detect_service(text: str, cfg: dict, services: list[Service]) -> Service | None:
    """
    Match a service by the SYNONYMS in YAML. Nobody says "Seasonal tyre
    change" out loud — they say "I need my tyres swapped".
    """
    low = text.lower()
    syn: dict = cfg.get("service_synonyms") or {}
    best, best_len = None, 0
    for svc in services:
        for k in syn.get(svc.name, []):
            if k.lower() in low and len(k) > best_len:
                best, best_len = svc, len(k)
        if svc.name.lower() in low and len(svc.name) > best_len:
            best, best_len = svc, len(svc.name)
    return best


def detect_name(text: str) -> str | None:
    m = NAME_RE.search(text)
    return m.group(1) if m else None


@dataclass
class CallSession:
    """
    Memory of one conversation. Without it, "anything later?" would mean
    the same slot again instead of the next one.
    """
    call_id: str
    tenant_id: int
    phone: str
    client_id: int | None = None
    service_id: int | None = None
    slots: list[dict] = field(default_factory=list)
    slot_index: int = -1
    guessed_name: str | None = None
    transcript: list[dict] = field(default_factory=list)
    booked_appointment_id: int | None = None
    pending: dict | None = None      # what has been offered, awaiting a yes

    def current_slot(self) -> dict | None:
        if 0 <= self.slot_index < len(self.slots):
            return self.slots[self.slot_index]
        return None


SESSIONS: dict[str, CallSession] = {}


def get_session(call_id: str, tenant_id: int, phone: str) -> CallSession:
    s = SESSIONS.get(call_id)
    if s is None:
        s = CallSession(call_id=call_id, tenant_id=tenant_id, phone=phone)
        SESSIONS[call_id] = s
    return s


def drop_session(call_id: str) -> None:
    SESSIONS.pop(call_id, None)


MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def _speak_time(dt: datetime) -> str:
    """"the 5th of December at 8:00" - how it should sound in the earpiece."""
    today = clock.now().date()
    if dt.date() == today:
        day = "today"
    elif (dt.date() - today).days == 1:
        day = "tomorrow"
    else:
        day = f"{MONTHS[dt.month - 1]} {dt.day}"
    return f"{day} at {dt.hour}:{dt.minute:02d}"


def _prompt(speak: str, screen: str = "", kind: str = "info", **extra) -> dict:
    """One prompt: short for the ear, fuller for the screen."""
    return {"speak": speak, "screen": screen or speak, "kind": kind, **extra}


def handle_utterance(db: Session, tenant: Tenant, session: CallSession,
                     text: str, speaker: str = "caller") -> list[dict]:
    """
    Takes one recognised utterance, returns prompts. An empty list means
    there is nothing to say — and that is fine. Silence beats talking over
    the customer.
    """
    cfg = load_vertical(tenant.vertical)
    services = db.query(Service).filter(
        Service.tenant_id == tenant.id, Service.active).all()

    session.transcript.append({"at": clock.now().isoformat(timespec="seconds"),
                               "speaker": speaker, "text": text})

    intents = detect_intents(text)
    svc = detect_service(text, cfg, services)
    if svc:
        session.service_id = svc.id
    if (nm := detect_name(text)) and not session.guessed_name:
        session.guessed_name = nm

    # The OPERATOR saying "one moment" is the signal that the customer has
    # paused and is waiting. It is the only safe window to speak into the
    # earpiece — two voices at once is unusable.
    if speaker == "operator":
        if "wait" in intents:
            return _answer_pending(db, tenant, session)
        return []

    out: list[dict] = []
    service = db.get(Service, session.service_id) if session.service_id else None

    # Service named -> prepare price and slots, but stay silent for now.
    if svc:
        session.pending = {"type": "price_and_slot", "service_id": svc.id}

    if "price" in intents:
        if service:
            out.append(_prompt(f"{service.price:.0f} hryvnia",
                               f"{service.name} - {service.price:.0f} UAH, "
                               f"{service.duration_min} min",
                               kind="price", service_id=service.id))
        else:
            names = ", ".join(s.name for s in services[:4])
            out.append(_prompt("Ask which service",
                               f"Service not recognised. Catalogue: {names}",
                               kind="clarify"))

    want_slot = ("when" in intents or "later" in intents
                 or "earlier" in intents or "tomorrow" in intents)
    if want_slot:
        target = service or (services[0] if services else None)
        if target is None:
            return out

        if not session.slots or (svc and session.slot_index < 0):
            session.slots = find_free_slots(db, tenant, target, limit=12)
            session.slot_index = -1

        if "later" in intents and session.slot_index >= 0:
            session.slot_index += 1                  # next slot along
        elif "earlier" in intents and session.slot_index > 0:
            session.slot_index -= 1
        elif "tomorrow" in intents:
            tmr = clock.now().date().toordinal() + 1
            idx = next((i for i, s in enumerate(session.slots)
                        if datetime.fromisoformat(s["starts_at"]).date().toordinal() == tmr),
                       session.slot_index + 1)
            session.slot_index = idx
        else:
            session.slot_index = max(0, session.slot_index)

        slot = session.current_slot()
        if slot:
            dt = datetime.fromisoformat(slot["starts_at"])
            session.pending = {"type": "book", "slot": slot, "service_id": target.id}
            left = len(session.slots) - session.slot_index - 1
            out.append(_prompt(_speak_time(dt),
                               f"{slot['human']} · {target.name} · {left} more options",
                               kind="slot", slot=slot, service_id=target.id))
        else:
            out.append(_prompt("Nothing free",
                               "No open slots in range - offer a waiting list",
                               kind="empty"))

    if "confirm" in intents and session.pending \
            and session.pending.get("type") == "book":
        slot = session.pending["slot"]
        out.append(_prompt("Booking it", f"Confirm the booking: {slot['human']}",
                           kind="ready_to_book", slot=slot,
                           service_id=session.pending["service_id"]))

    if "bye" in intents:
        out.append(_prompt("", "Call wrapping up - don't forget to save the booking",
                           kind="closing"))

    if "greeting" in intents and session.guessed_name and not session.client_id:
        out.append(_prompt("", f"Gave their name as {session.guessed_name} - new customer?",
                           kind="hint"))

    return out


def _answer_pending(db: Session, tenant: Tenant, session: CallSession) -> list[dict]:
    """
    The operator said "one moment" — the customer is quiet and waiting.
    This is the moment to speak.
    """
    p = session.pending
    if not p:
        return []

    if p["type"] == "price_and_slot":
        svc = db.get(Service, p["service_id"])
        if not svc:
            return []
        slots = find_free_slots(db, tenant, svc, limit=12)
        session.slots, session.slot_index = slots, -1
        line = f"{svc.price:.0f} hryvnia"
        screen = f"{svc.name}: {svc.price:.0f} UAH, {svc.duration_min} min"
        if slots:
            dt = datetime.fromisoformat(slots[0]["starts_at"])
            line += f". Next opening {_speak_time(dt)}"
            screen += f" · next: {slots[0]['human']}"
        return [_prompt(line, screen, kind="price")]

    if p["type"] == "book":
        slot = p["slot"]
        dt = datetime.fromisoformat(slot["starts_at"])
        return [_prompt(_speak_time(dt), slot["human"], kind="slot", slot=slot)]

    return []
