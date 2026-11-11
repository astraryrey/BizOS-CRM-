"""
MOCK U-BILLING - a standalone service on port 8081.

This is NOT part of BizOS. It is a toy copy of the billing system that
behaves like the real one: it accepts HTTP calls, raises invoices,
remembers them and can mark them paid. It exists for one reason - to show
the whole "visit -> invoice -> payment" cycle in a demo while there is no
access to the production U-billing.

When the real one arrives this service is switched off and one environment
variable changes in BizOS. No BizOS code is touched.

Run:  python3 -m uvicorn mocks.ubilling_mock:app --port 8081
"""
import itertools
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI(title="U-billing (MOCK)", version="0.1.0",
              description="Toy copy of the billing system, for the BizOS demo")

_seq = itertools.count(100001)
INVOICES: dict[str, dict] = {}

# Subscribers of the ISP - what would really live inside U-billing.
SUBSCRIBERS = [
    {"subscriber_id": "UB-1001", "account": "GAR-0417", "name": "Motor Garage",
     "phone": "+380442000101", "tariff": "Business-100", "balance": 1240.0},
    {"subscriber_id": "UB-1002", "account": "SAL-2209", "name": "Malva Salon",
     "phone": "+380442000102", "tariff": "Business-50", "balance": -310.0},
    {"subscriber_id": "UB-1003", "account": "VET-3318", "name": "Aibolit Vet Clinic",
     "phone": "+380442000103", "tariff": "Business-100", "balance": 0.0},
]


class InvoiceIn(BaseModel):
    account: str | None = None
    subscriber_id: str | None = None
    phone: str | None = None
    amount: float
    description: str = ""


@app.get("/health")
def health():
    return {"service": "ubilling-mock", "invoices": len(INVOICES),
            "subscribers": len(SUBSCRIBERS)}


@app.get("/api/subscribers")
def find_subscriber(phone: str | None = None, account: str | None = None):
    """Subscriber lookup - how BizOS links its own record to billing."""
    for s in SUBSCRIBERS:
        if (phone and s["phone"] == phone) or (account and s["account"] == account):
            return s
    raise HTTPException(404, "Subscriber not found")


@app.get("/api/subscribers/all")
def all_subscribers():
    return SUBSCRIBERS


@app.post("/api/invoices")
def create_invoice(data: InvoiceIn):
    """Raise an invoice. Exactly what BizOS does once a visit is closed."""
    inv_id = f"INV-{next(_seq)}"
    rec = {
        "invoice_id": inv_id,
        "account": data.account,
        "subscriber_id": data.subscriber_id,
        "phone": data.phone,
        "amount": round(data.amount, 2),
        "description": data.description,
        "status": "sent",
        "issued_at": datetime.utcnow().isoformat(timespec="seconds"),
        "paid_at": None,
        "pay_link": f"http://127.0.0.1:8081/pay/{inv_id}",
    }
    INVOICES[inv_id] = rec
    return rec


@app.get("/api/invoices/{invoice_id}")
def get_invoice(invoice_id: str):
    inv = INVOICES.get(invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    return inv


@app.get("/api/invoices")
def list_invoices(limit: int = Query(50, le=500)):
    return list(reversed(list(INVOICES.values())))[:limit]


@app.post("/api/invoices/{invoice_id}/pay")
def pay_invoice(invoice_id: str):
    """Mark as paid - stands in for the customer paying during a demo."""
    inv = INVOICES.get(invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    inv["status"] = "paid"
    inv["paid_at"] = datetime.utcnow().isoformat(timespec="seconds")
    return inv


@app.get("/pay/{invoice_id}", response_class=HTMLResponse, include_in_schema=False)
def pay_page(invoice_id: str):
    """The payment page - the link that goes out to the customer."""
    inv = INVOICES.get(invoice_id)
    if not inv:
        return HTMLResponse("<h1>Invoice not found</h1>", status_code=404)
    paid = inv["status"] == "paid"
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Invoice {invoice_id}</title>
<style>
body{{font-family:system-ui,sans-serif;background:#E6E8E1;margin:0;
padding:40px 18px;color:#171A17}}
.card{{max-width:420px;margin:0 auto;background:#fff;border-radius:6px;
padding:26px;box-shadow:0 2px 14px rgba(0,0,0,.09)}}
.lbl{{font-size:11px;letter-spacing:.13em;text-transform:uppercase;color:#8B9186}}
.sum{{font-size:38px;font-weight:700;margin:6px 0 18px}}
.row{{display:flex;justify-content:space-between;padding:7px 0;
border-bottom:1px dotted #DEE0D8;font-size:14px}}
button{{width:100%;margin-top:22px;padding:14px;border:none;border-radius:4px;
background:#2E5B4C;color:#fff;font-size:16px;font-weight:600;cursor:pointer}}
.paid{{margin-top:22px;padding:14px;border-radius:4px;background:#EDF4EE;
color:#234A3D;text-align:center;font-weight:600}}
.note{{margin-top:16px;font-size:11px;color:#8B9186;text-align:center}}
</style></head><body><div class="card">
<div class="lbl">Invoice {invoice_id}</div>
<div class="sum">{inv['amount']:.0f} UAH</div>
<div class="row"><span>Service</span><span>{inv['description'] or '-'}</span></div>
<div class="row"><span>Issued</span><span>{inv['issued_at'][:10]}</span></div>
<div class="row"><span>Status</span><span>{'paid' if paid else 'awaiting payment'}</span></div>
{'<div class="paid">Paid</div>' if paid else
 f'<form method="post" action="/api/invoices/{invoice_id}/pay">'
 '<button type="submit">Pay now</button></form>'}
<div class="note">This is a mock of the U-billing payment page.<br>
No real money is involved.</div>
</div></body></html>"""
    return HTMLResponse(body)
