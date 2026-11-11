# Testing reminders without spending money

The problem: a reminder set for two days out means waiting two days, and
real SMS costs money and reaches real people. Two things solve it - the
virtual clock and a sandbox gateway.

## Level 1. The queue - what the system intends to send

```
GET /api/test/pending
```

Who, on which channel, **in how many hours**, the exact text, and how
many SMS segments it becomes. Nothing to wait for: the queue is visible
the moment a booking is created.

To check the 48-hour rule, book something five days out and confirm
`in_hours ≈ 72`.

## Level 2. Time travel

```
POST /api/demo/timetravel  {"days": 3}
```

The clock jumps and the scheduler dispatches whatever has come due. In
the console these are the `+1d / +7d / +30d` buttons.

## Level 3. The SMS sandbox

```bash
SMS_GATEWAY=sandbox python3 -m uvicorn app.main:app --reload
```

| Endpoint | What it does |
|---|---|
| `GET /api/test/sms/outbox` | what "went out": text, encoding, segments, errors |
| `POST /api/test/sms/clear` | empty it |
| `POST /api/test/sms/block?phone=...` | make a number unreachable |
| `POST /api/test/sms/fail-rate?rate=0.3` | 30% random failures |
| `GET /api/test/sms/preview?text=...` | how many segments a text becomes |
| `POST /api/test/dispatch` | run the scheduler immediately |

### What to check

1. Normal delivery - booking → time travel → message in the outbox.
2. Unreachable subscriber - `block` the number; it goes to `failed`
   while the rest still go out.
3. Gateway failure - `fail-rate=1.0`; the system does not fall over.
4. Malformed number - rejected at validation.
5. Message length - see below; this one is about money.

## Message length and cost

Latin text fits 160 characters in one SMS. **Cyrillic and most non-Latin
scripts fit only 70.** A long reminder becomes two or three messages and
costs two or three times as much.

That is why the YAML carries short SMS templates alongside the full ones:

```yaml
templates:
  reminder_sms: "{client.name}, see you {when} - {service.name}. {tenant}"
  reminder: >-
    The longer version, for Telegram, where length is not billed...
```

The system picks `*_sms` automatically when the channel is SMS.

```
 52 chars -> 1 SMS   Oleg, see you 27 Aug 14:00 - Manicure. Malva Salon
120 chars -> 2 SMS   Tatiana, reminder about your appointment tomorrow...
```

If the business messages in Cyrillic, this is the first thing to check -
it can double the monthly bill on its own.

## Level 4. A real gateway

Only after everything above passes.

```bash
SMS_GATEWAY=turbosms SMS_TOKEN=... SMS_SENDER="SALON MALVA" uvicorn ...
```

What is needed besides code:

1. A contract with the gateway and credit on the account.
2. A **registered alphanumeric sender ID**. Carriers reject unregistered
   ones; registration takes days and needs company documents.
3. Customer consent to be messaged - without it this is spam.

**Send the first live message to yourself.** Add yourself as a client,
make a booking, travel forward, and confirm the SMS arrives and reads
the way you intended.

## Reminder lead time

In `configs/<vertical>.yaml`:

```yaml
reminder_hours_before: 48   # two days
```

Each vertical can differ: a salon is fine with a day, a dental practice
is better off with three.
