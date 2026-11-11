# BizOS - digital front desk for small businesses

One core, with the vertical defined by a text file. The full chain:
**call → client card → booking → reminder → visit → invoice → repeat offer.**

---

## Run the demo with one command

```bash
chmod +x run_demo.sh
./run_demo.sh
```

Three services come up:

| What | Address |
|---|---|
| **Operator console** | http://localhost:8000/panel |
| Swagger (debugging) | http://localhost:8000/docs |
| U-billing mock | http://localhost:8081/docs |
| Mapping (Grusher) mock | http://localhost:8082/docs |

The order to show things in is in **`DEMO.md`**.

### Starting it by hand

```bash
pip3 install -r requirements.txt
python3 -m app.seeds                                  # demo data
python3 -m uvicorn mocks.ubilling_mock:app --port 8081 &
python3 -m uvicorn mocks.grusher_mock:app  --port 8082 &
BILLING_ADAPTER=mock MAPS_ADAPTER=mock SMS_GATEWAY=sandbox \
  python3 -m uvicorn app.main:app --reload
```

Businesses: **1 = garage, 2 = beauty salon, 3 = vet clinic**
(the `X-Tenant-Id` header; in the console it is the switcher at the top).

---

## What it does

**One core across every vertical.** Switching business changes the labels,
the service catalogue, the card fields and the repeat intervals. A vertical
is `configs/<name>.yaml` and not a line of code. Adding dentistry means
adding a file.

**Call assist.** The `/api/telephony/incoming` webhook turns a phone number
into a brief: last visit and how long ago, the asset with its attributes,
what is due, unpaid invoices, free slots and ready-made phrases. The card
pushes itself onto the console over a WebSocket.

**Resource availability.** Booking a busy stylist is refused, naming the
conflicting appointment. The same time with someone else is fine.

**Reminders and repeat business.** Lead time lives in YAML
(`reminder_hours_before: 48`). The channel picks itself: Telegram if the
client subscribed to the bot, otherwise SMS. SMS uses the short template -
see `TESTING.md` on why that matters for the bill.

**Calendar.** An `.ics` file per booking, plus a subscription link per
staff member: added once, the schedule then keeps itself current. Works in
Apple, Google, Outlook and Android with no OAuth and no API keys.

**Maps.** Service zones, provider network nodes, clients, and the on-site
crew's route. The route follows appointment times; a shorter reordering is
offered only as a hint.

**Customer migration.** `POST /api/import/clients` takes a CSV exported
from Excel. It detects the columns, normalises phone numbers, skips
duplicates and can geocode the addresses on the way in.

**Virtual clock.** The +1d / +7d / +30d buttons turn a two-day wait into
five seconds.

---

## What is not connected yet

| What | State | What is needed |
|---|---|---|
| U-billing | mock works | credentials for the production API |
| Mapping (Grusher) | mock works | credentials for the production API |
| Asterisk | configs ready | run it, see `ASTERISK.md` |
| SMS gateway | sandbox | contract, sender ID, token |
| Telegram bot | logs only | bot token, a listener process |
| Speech recognition | engine ready | AudioSocket + Vosk |

Details in **`INTEGRATIONS.md`**; live status on the Integrations tab of
the console, or `GET /api/integrations/status`.

---

## Layout

```
app/
  models.py        Client, Asset(JSON), Resource, Service, Appointment,
                   Invoice, ScheduledMessage, AuditLog
  verticals.py     loads the vertical YAML files
  lifecycle.py     the chain, availability checks, integrity checks
  slots.py         genuinely free slots
  call_brief.py    the brief built from a caller ID
  intents.py       call assist from recognised speech (no LLM)
  calendar_feed.py .ics files and subscription feeds
  scheduler.py     dispatches messages that have come due
  clock.py         virtual time
  realtime.py      WebSocket to the console
  integrations/    billing.py · grusher.py · sms.py · csv_import.py
  routers/api.py   HTTP API
  static/panel.html  the console (one file, no build step)
configs/           auto_service.yaml · beauty.yaml · vet.yaml
mocks/             ubilling_mock.py · grusher_mock.py
telephony/         Asterisk sandbox in Docker
```

## Rules that must not be broken

1. No `if vertical == ...` in business logic - read the config instead.
2. Time comes only from `clock.now()`, or time travel breaks.
3. Message wording comes only from `templates` in the config.
4. Every row carries `tenant_id`, and it is checked when records are linked.
5. The core knows adapters, never the foreign systems behind them.

## Documents

| File | About |
|---|---|
| `DEMO.md` | a ten-minute demo script |
| `INTEGRATIONS.md` | integration points: what plugs in where |
| `ASTERISK.md` | telephony: three ways to connect it |
| `VOICE_ASSIST.md` | speech recognition and the earpiece prompt |
| `TESTING.md` | how to test messaging without spending money |
| `LINKS.md` | what to download, and in what order |
