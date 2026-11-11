# Integration points: what plugs in where

A live version of this page sits on the **Integrations** tab of the
console (or `GET /api/integrations/status`), showing what is connected
right now.

---

## How it is arranged

The BizOS core **knows nothing** about U-billing or the Grusher mapping
system. It knows two interfaces - `BillingAdapter` and `MapAdapter`.
Everything else sits behind them.

```
      BizOS core
          │
   ┌──────┴──────┐
BillingAdapter  MapAdapter      ← the core knows only these
   │                │
   ├─ NullBilling   ├─ NullMaps          off; the system still runs
   ├─ MockUbilling  ├─ MockGrusher       MOCK, for demonstrations
   └─ Ubilling      └─ Grusher           PRODUCTION (skeleton)
```

Switching between them is one environment variable. No code changes.

---

## 1. U-billing (billing)

**File:** `app/integrations/billing.py`
**What it does:** raises an invoice after a visit, returns a payment link,
checks whether it has been paid.

| Mode | Launch with | What happens |
|---|---|---|
| off | `BILLING_ADAPTER=null` | invoices exist only inside BizOS |
| mock | `BILLING_ADAPTER=mock` | talks to `mocks/ubilling_mock.py` |
| production | `BILLING_ADAPTER=ubilling` | talks to the real U-billing |

**What to request from the U-billing administrator:**

1. BASE_URL of the API, e.g. `https://billing.local/api/v1`
2. How to authenticate: token, username and password, or API key
3. Method names: create invoice, find subscriber, check payment
4. Request and response format (there is usually documentation)
5. A test environment where no real invoices are created

**Once you have them:** fill in the three methods of `UbillingAdapter`
(each is marked `TODO` with an example call), then:

```bash
BILLING_ADAPTER=ubilling UBILLING_URL=... UBILLING_TOKEN=... \
  python3 -m uvicorn app.main:app
```

**If there is no API at all:** its database may be read READ-ONLY, through
a dedicated user with SELECT rights. Never write to another system's
database directly - its own logic (ledger entries, triggers, history)
would be bypassed and the data would drift apart.

**Where this lives in the data model:**
`Tenant.billing_account` - the contract number,
`Invoice.external_id` / `pay_link` - the invoice in billing,
`Client.external_id` - the subscriber in billing.

---

## 2. Grusher (mapping)

**File:** `app/integrations/grusher.py`
**What it does:** address → coordinates, service zone, crew route.

| Mode | Launch with |
|---|---|
| off | `MAPS_ADAPTER=null` |
| mock | `MAPS_ADAPTER=mock` (with `mocks/grusher_mock.py`) |
| production | `MAPS_ADAPTER=grusher` |

**What to request from whoever owns Grusher:**

1. BASE_URL of the API
2. Token or key
3. Which objects it exposes: addresses only, or equipment and nodes too
4. Whether it geocodes (address → coordinates) or only stores points
5. Whether it computes routes, or we compute them on our side
6. Coordinate format (WGS84, or something custom)

**If Grusher runs on PostGIS** (likely, given the Postgres stack): its
database could be read directly, but it is better to ask for a couple of
SQL views or a small HTTP endpoint. That decouples the two systems and
survives their upgrades.

**Where this lives in the data model:**
`Client.lat` / `lon` / `address`, `Tenant.lat` / `lon`,
`Resource.mobile` - an on-site crew, `Service.onsite` - a callout service.

**One detail about routing.** By default the trip follows **appointment
times** - customers have already been told an hour. The `optimize=true`
parameter shows how much shorter a reordered trip would be, but applying
it would mean moving bookings. It is a hint for a manager, not an
automatic decision.

---

## 3. Asterisk (telephony)

**File:** `app/routers/api.py` → `/api/telephony/incoming`
**What it does:** turns a caller ID into a brief and pushes it to the console.

It needs **one line** in the Asterisk dialplan:

```
same => n,System(curl -s -m 2 -X POST "http://SERVER:8000/api/telephony/incoming?phone=${CALLERID(num)}&tenant_id=1" &)
```

**What to establish with whoever runs the phones:**

1. Asterisk version (16+ is needed, for AudioSocket later)
2. Whether there is access to the dialplan (`extensions.conf`) or FreePBX
3. Whether the BizOS server is reachable from the PBX network
4. Which public number maps to which business (for `tenant_id`)

A ready-made sandbox for testing is in `telephony/`; see `ASTERISK.md`.

---

## 4. SMS gateway

**File:** `app/integrations/sms.py`

| Mode | Launch with |
|---|---|
| log | `SMS_GATEWAY=console` |
| sandbox | `SMS_GATEWAY=sandbox` - simulates failures, costs nothing |
| production | `SMS_GATEWAY=turbosms` |

**What is needed besides code:**

1. A contract with the gateway and credit on the account
2. A **registered alphanumeric sender ID** (e.g. `SALON MALVA`) -
   registration takes several days and requires company documents
3. An API token
4. Consent from customers to be messaged

---

## 5. Telegram / Viber

**File:** `app/scheduler.py` → `Notifier.send`

It currently writes to the log. To make it real:

1. Create a bot with `@BotFather`, take the token
2. Install `aiogram`
3. Replace that branch in `Notifier.send`

**The hard part is not the code.** Telegram forbids bots from messaging
first. The customer must press Start, and only then does a `chat_id`
exist. The `Client.telegram_chat_id` field is already there for it.

In Ukraine, Viber often matters more than Telegram for the older audience
of clinics and veterinary practices. `ScheduledMessage.channel` already
supports that.

---

## 6. Speech recognition

**File:** `app/intents.py` - the engine is done, the entry point is
`/api/assist/utterance`. It takes **text**, which is why it can be tested
without a byte of audio.

Missing: Asterisk AudioSocket for live audio, Vosk or a cloud speech
service, and Piper or Silero for the earpiece voice. Details in
`VOICE_ASSIST.md`, download links in `LINKS.md`.
