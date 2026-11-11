"""
INTEGRATION POINT #3 - SMS GATEWAY.

The hard part of SMS is not sending but two other things:

  1. NON-LATIN TEXT IS EXPENSIVE. Latin fits 160 characters in one
     message; Cyrillic and most other scripts fit only 70. A long
     reminder becomes three messages and costs three times as much.
     That is why the YAML carries separate short *_sms templates.

  2. PROVING IT WORKS BEFORE PAYING FOR A GATEWAY. Hence the sandbox
     below, which behaves like a real gateway including its failures.

Modes (environment variable SMS_GATEWAY):
  console   writes to the log (default)
  sandbox   simulates a gateway: delays, rejections, bad numbers
  turbosms  skeleton for a real gateway
"""
import logging
import os
import random
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

log = logging.getLogger("bizos.sms")

# Characters that fit the basic GSM-7 alphabet (Latin plus punctuation)
GSM7 = set(
    "@£$¥èéùìòÇØøÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà\n\r"
)


@dataclass
class SmsInfo:
    """What the text becomes on the wire and what it will cost."""
    text: str
    length: int
    encoding: str          # gsm7 / ucs2
    segments: int
    cost_hint: str

    @property
    def expensive(self) -> bool:
        return self.segments > 1


def analyze(text: str) -> SmsInfo:
    """How many messages this text splits into. Non-Latin means 70 chars."""
    is_gsm = all(ch in GSM7 for ch in text)
    n = len(text)
    if is_gsm:
        single, multi, enc = 160, 153, "gsm7"
    else:
        single, multi, enc = 70, 67, "ucs2"
    segments = 1 if n <= single else -(-n // multi)   # round up
    return SmsInfo(text=text, length=n, encoding=enc, segments=segments,
                   cost_hint=f"{segments} SMS" +
                             (" - worth shortening" if segments > 1 else ""))


def normalize_msisdn(phone: str) -> str | None:
    """Normalise to international format. Malformed numbers are rejected."""
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("0") and len(digits) == 10:
        digits = "38" + digits
    if len(digits) < 11 or len(digits) > 15:
        return None
    return "+" + digits


class SmsGateway(Protocol):
    name: str

    def send(self, phone: str, text: str) -> dict:
        """Returns {'ok': bool, 'provider_id': str|None, 'error': str|None}"""
        ...


# ------------------------------------------------------ level 1: console ----
class ConsoleSms:
    """Sends nothing, writes to the log. The default mode."""
    name = "console"

    def send(self, phone: str, text: str) -> dict:
        info = analyze(text)
        log.info("SMS -> %s [%s, %s] %s", phone, info.encoding, info.cost_hint, text)
        return {"ok": True, "provider_id": f"console-{datetime.utcnow():%H%M%S}",
                "error": None}


# ------------------------------------------------------ level 2: sandbox ----
@dataclass
class SandboxSms:
    """
    A simulated gateway, so the system can be tested without sending real
    messages or spending money.

    It fails the way real gateways fail: bad numbers are rejected, some
    sends drop at random, and specific numbers can be made unreachable.
    Everything "sent" lands in `outbox`, readable through
    GET /api/test/sms/outbox.
    """
    name: str = "sandbox"
    fail_rate: float = 0.0                 # share of random failures, 0..1
    outbox: list[dict] = field(default_factory=list)
    blocked: set[str] = field(default_factory=set)

    def send(self, phone: str, text: str) -> dict:
        msisdn = normalize_msisdn(phone)
        info = analyze(text)
        rec = {"at": datetime.utcnow().isoformat(timespec="seconds"),
               "to": msisdn or phone, "text": text, "encoding": info.encoding,
               "segments": info.segments, "chars": info.length}

        if msisdn is None:
            rec |= {"ok": False, "error": "Malformed phone number"}
        elif msisdn in self.blocked:
            rec |= {"ok": False, "error": "Subscriber unreachable"}
        elif self.fail_rate and random.random() < self.fail_rate:
            rec |= {"ok": False, "error": "Temporary gateway failure"}
        else:
            rec |= {"ok": True, "error": None,
                    "provider_id": f"sandbox-{len(self.outbox)+1:05d}"}

        self.outbox.append(rec)
        return {"ok": rec["ok"], "provider_id": rec.get("provider_id"),
                "error": rec.get("error")}

    def stats(self) -> dict:
        sent = [m for m in self.outbox if m["ok"]]
        failed = [m for m in self.outbox if not m["ok"]]
        return {"total": len(self.outbox), "sent": len(sent), "failed": len(failed),
                "segments_total": sum(m["segments"] for m in sent),
                "multipart": len([m for m in sent if m["segments"] > 1]),
                "errors": {e: len([m for m in failed if m["error"] == e])
                           for e in {m["error"] for m in failed}}}


# -------------------------------------------------- level 3: real gateway ----
class TurboSmsAdapter:
    """
    SKELETON for a real gateway (TurboSMS / SMS-Fly / a carrier - the
    principle is the same).

    To switch it on you need:
      1. a contract with the gateway and credit on the account;
      2. a REGISTERED sender ID (alphanumeric), e.g. "SALON MALVA".
         Carriers reject unregistered sender IDs. Registration takes days
         and requires company documents;
      3. an API token -> SMS_TOKEN;
      4. consent from customers to be messaged - without it this is spam,
         both legally and in practice.

    Switch on with:
      SMS_GATEWAY=turbosms SMS_TOKEN=... SMS_SENDER="SALON MALVA"
    """
    name = "turbosms"

    def __init__(self) -> None:
        self.token = os.getenv("SMS_TOKEN", "")
        self.sender = os.getenv("SMS_SENDER", "")

    def send(self, phone: str, text: str) -> dict:
        if not self.token or not self.sender:
            return {"ok": False, "provider_id": None,
                    "error": "Gateway not configured: need SMS_TOKEN and SMS_SENDER"}
        # TODO: httpx.post(gateway_url, json={...}, headers={...})
        raise NotImplementedError("Real sending not implemented - credentials needed")


_sandbox = SandboxSms()


def get_sms_gateway() -> SmsGateway:
    which = os.getenv("SMS_GATEWAY", "console").lower()
    if which == "sandbox":
        return _sandbox
    if which == "turbosms":
        return TurboSmsAdapter()
    return ConsoleSms()


def sandbox() -> SandboxSms:
    """Access to the sandbox for the test endpoints."""
    return _sandbox
