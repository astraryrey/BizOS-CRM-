"""
The scheduler: every 30 seconds it picks up messages whose send time has
arrived (virtual time — see clock.py) and dispatches them.
"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler

from . import clock
from .db import SessionLocal
from .integrations.sms import get_sms_gateway
from .models import Client, MessageStatus, ScheduledMessage

log = logging.getLogger("bizos.scheduler")


class Notifier:
    """
    Channel router: looks at msg.channel and hands the message to the right
    sender. SMS goes to a real gateway (or the sandbox). Telegram and Viber
    currently write to the log — see INTEGRATIONS.md.
    """

    def send(self, msg: ScheduledMessage,
             phone: str | None = None) -> tuple[bool, str | None]:
        text = msg.rendered_text or ""
        if msg.channel == "sms":
            if not phone:
                return False, "Client has no phone number"
            res = get_sms_gateway().send(phone, text)
            if not res["ok"]:
                log.warning("SMS to %s failed: %s", phone, res["error"])
            return res["ok"], res["error"]
        # TODO: replace with a Telegram notifier built on aiogram
        log.info("[%s -> client %s] %s", msg.channel, msg.client_id, text)
        return True, None


notifier = Notifier()


def dispatch_due_messages() -> int:
    db = SessionLocal()
    sent = 0
    try:
        due = (db.query(ScheduledMessage)
                 .filter(ScheduledMessage.status == MessageStatus.pending,
                         ScheduledMessage.send_at <= clock.now())
                 .limit(200).all())
        for msg in due:
            client = db.get(Client, msg.client_id)
            ok, err = notifier.send(msg, phone=client.phone if client else None)
            msg.status = MessageStatus.sent if ok else MessageStatus.failed
            msg.sent_at = clock.now()
            if err:
                msg.payload = {**(msg.payload or {}), "last_error": err}
            sent += 1
        db.commit()
    finally:
        db.close()
    return sent


def start_scheduler() -> BackgroundScheduler:
    sched = BackgroundScheduler()
    sched.add_job(dispatch_due_messages, "interval", seconds=30, id="dispatch")
    sched.start()
    return sched
