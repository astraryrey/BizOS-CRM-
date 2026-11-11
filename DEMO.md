# Demo script - ten minutes

A running order that builds from the simple to the striking.
Start everything with `./run_demo.sh`, then open
**http://localhost:8000/panel**.

---

## 0. What is running

| What | Address | Why |
|---|---|---|
| BizOS | localhost:8000/panel | the product itself |
| U-billing mock | localhost:8081/docs | stands in for billing |
| Grusher mock | localhost:8082/docs | stands in for mapping |

The mocks are temporary. When the real systems arrive they are switched
off and one environment variable changes in BizOS.

---

## 1. One core, three businesses (30 seconds)

The switcher at the top: **Garage → Salon → Vet clinic**.

Point out what changes:
* the schedule columns - Bays / Stylists / Vets;
* the client card - Vehicles / nothing / Pets;
* the service catalogue and repeat intervals.

**Say:** these are not three programs. It is one, and the vertical is a
text file (`configs/beauty.yaml`). Adding dentistry takes fifteen
minutes and no code.

---

## 2. The call (the heart of it, 2 minutes)

Press **Take a call** and pick a number from the database.

The card appears by itself, carrying:
* ready-made phrases - what to say right now;
* when they were last in and how long ago;
* the vehicle with its mileage, or the pet;
* what is due, with how overdue it is;
* unpaid invoices with a payment link;
* free slots - clicking one opens the booking form.

**Say:** the operator searched for nothing. The number came from the
phone system and everything else was assembled before they picked up.

---

## 3. Double-booking (30 seconds)

Click an empty cell in the schedule to open the booking form. Choose a
time when that stylist is already busy:

> Stylist 'Olha' is busy: booking #66 from 01.12 11:00 to 12:30.
> Pick another time or another stylist.

Switch to a different stylist - free.

---

## 4. Visit closed → invoice in billing (1 minute)

Click a booking and press **Mark as done**.

What happens by itself:
1. an invoice is raised **in U-billing**, not inside BizOS;
2. the customer is sent a message with the payment link;
3. a repeat offer is scheduled at the service's interval.

Open `GET /api/invoices` and the `external_id` is there -
`INV-100001` - along with the `pay_link`. Follow the link: it is the
billing system's payment page. Press Pay, come back and call
`POST /api/invoices/{id}/refresh` - the status flips to `paid`.

**Say:** the invoice lives in billing and BizOS only holds its number.
The production U-billing will work exactly the same way.

---

## 5. Time travel (the striking part, 30 seconds)

Press **+30d** in the header.

The red "now" line jumps, and the feed on the left fills with repeat
offers that have just gone out:

> Tatiana, time to refresh your Haircut? Malva Salon

**Say:** this is the customer's main source of revenue - bringing back
people who would otherwise not have returned. In the demo we simply
skip the month of waiting.

---

## 6. The map (1 minute)

The **Map** tab:

* service zones with their callout surcharges;
* provider network nodes and the data centre;
* clients (click one to open their card);
* **Tomorrow's route** - the order of the trip, distance and time.

The route follows **appointment times** - customers have been told an
hour already. The system separately reports how much a reordered trip
would save.

---

## 7. Staff calendars (30 seconds)

The **Staff calendars** block on the left, then **Subscribe**.

**Say:** the stylist adds the link once and their schedule then keeps
itself up to date. No console, no login, no training - they look at the
calendar they already use. That is the whole positioning: work inside
the tools people already have and keep the complicated system out of
sight.

---

## 8. Call assist (2 minutes)

Through Swagger, `POST /api/assist/utterance`, feeding utterances with
one shared `call_id`:

| Utterance | speaker | In the earpiece |
|---|---|---|
| Hello, this is Vlad. I need the winter tyres put on my Audi A3. How much is that? | caller | "1200 hryvnia" |
| One moment | operator | "1200 hryvnia. Next opening today at 12:00" |
| Perfect, when can I come in? | caller | "today at 12:00" |
| I can't make that. Anything later? | caller | "today at 12:30" |
| Sounds good, book it | caller | "Booking it" |

Then `POST /api/assist/book` creates the appointment.

**Say:** nothing is invented. Keywords → a fact from the database → a
fixed template. The customer says "winter tyres" and the system maps it
to "Seasonal tyre change" through synonyms held in the same YAML. The
dialogue remembers what has already been offered, so "anything later?"
moves to the next slot rather than repeating the last one.

---

## 9. Integrations (closing, 1 minute)

The **Integrations** tab gives the honest picture:

* U-billing and Grusher - **mock** (amber);
* Asterisk, SMS, Telegram, speech recognition - **not connected** (red);
* each one names the file responsible and what to request.

**Say:** these are not decorative stubs. The core talks to the mocks for
real, over HTTP. Connecting the production systems means changing an
environment variable and filling in one class - not rewriting the product.

---

## What not to show

* Swagger as an interface - it is for debugging and will frighten a
  customer.
* Audio through the Dockerised Asterisk on macOS - RTP over NAT is
  unreliable. The card-by-number path works; audio is not needed for it.
* Promises about the earpiece voice - the engine is ready, the audio
  pipeline is not.
