# Connecting telephony (Asterisk)

## The short version

The application does **not** need to place calls, handle audio or speak
SIP. All it needs from the phone system is the **caller's number** at the
moment the call arrives.

```
Asterisk: "a call just came in from +380671234567"
   ↓ HTTP request
BizOS:    builds the brief, the due dates and the free slots
   ↓ WebSocket
Console:  the client card appears on the operator's screen
```

The heavy part is written. Only the first arrow is left.

---

## Checking it without Asterisk at all

Open the console, then in another browser tab:

```
http://localhost:8000/api/telephony/incoming?phone=%2B380671112233&tenant_id=1
```

Or from a **separate** terminal tab (the one running uvicorn is busy):

```bash
curl -X POST "http://localhost:8000/api/telephony/incoming?phone=%2B380671112233&tenant_id=1"
```

The card appears instantly. **This is exactly what a real call does** -
Asterisk runs the same command, just by itself.

Use a number from your own data (`GET /api/clients`), or you will see
"new number". A number without the plus also matches; the lookup tries
every spelling.

---

## Step 1. Find out what the call centre runs

Ask whoever maintains the phones:

1. Is it **Asterisk or FreePBX**, or something else?
2. Is there access to the dialplan (`extensions.conf`) or to FreePBX?
3. Is AMI or ARI enabled?
4. Is the BizOS server reachable from the network the PBX sits on?

**FreePBX is not an add-on to Asterisk, it is a web front end for it.**
FreePBX runs Asterisk underneath. You need one or the other, not both.

---

## Step 2. Pick a method

### Method A - a line in the dialplan (simplest)

```
exten => _X.,1,NoOp(Incoming: ${CALLERID(num)})
 same => n,System(curl -s -X POST "http://SERVER_IP:8000/api/telephony/incoming?phone=${CALLERID(num)}&tenant_id=1" &)
 same => n,Dial(SIP/operator)
```

`${CALLERID(num)}` is the Asterisk variable holding the caller's number.
The trailing ampersand means "do not wait", so the call is never delayed
by our request.

### Method B - ARI (the right one for production)

A separate process subscribes to `StasisStart` events and calls the
webhook. It carries more data (call id, who answered, when it ended) and
survives config rewrites.

### Method C - FreePBX through the web interface

*Custom Destinations*, with no hand-editing of files.

---

## Step 3. A sandbox on your own machine

The `telephony/` folder holds a ready Asterisk in Docker.

```bash
cd telephony
docker compose up
```

Two SIP accounts: **6001** (the caller, password `test6001`) and
**6002** (the operator, password `test6002`). You will need a softphone -
Linphone works.

Your machine's IP: `ipconfig getifaddr en0`

| Dial | What it tests |
|---|---|
| `100` | an ordinary incoming call (business #1) |
| `101` / `102` / `103` | routing by line, into the three businesses |
| `6002` | a direct call - proves the link works at all |
| `200` | echo test |

To make the call appear to come from a particular customer, change
`callerid` for 6001 in `asterisk/pjsip.conf` to a number from your data
and run `docker compose restart`.

**About audio:** inside Docker on macOS, RTP across NAT is unreliable.
It does not matter here - the card is raised from the number, before
anyone has said a word.

---

## Telling the businesses apart (tenant_id)

One call centre serves many customers. Which one is being called is
decided by the number that was **dialled** (`${CALLERID(dnid)}` or
`${EXTEN}`):

| Dialled number | tenant_id | Business |
|---|---|---|
| 0800-111-11 | 1 | Motor Garage |
| 0800-222-22 | 2 | Malva Salon |

In the dialplan these are separate contexts, each with its own
`tenant_id`.
