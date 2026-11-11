"""
=============================================================================
INTEGRATION POINT #1 - BILLING (U-billing)
=============================================================================

The core (lifecycle.py) knows nothing about how U-billing works. It only
knows the BillingAdapter interface below. Swap the billing system and this
one file is rewritten; nothing else is touched.

THREE MODES (environment variable BILLING_ADAPTER):

  null      stub. Does nothing. The system runs fine without billing.
  mock      LOCAL MOCK of U-billing (mocks/ubilling_mock.py).
            Real HTTP calls to a real service - the service is just a toy.
            This is what the demo runs on.
  ubilling  PRODUCTION U-billing. Needs credentials (see below).

WHAT TO REQUEST FROM THE U-BILLING ADMINISTRATOR:

  1. BASE_URL of the API, e.g. https://billing.local/api/v1
  2. How to authenticate: token / username+password / API key
  3. Method names: create invoice, find subscriber, check payment
  4. Request and response format (there is usually documentation)
  5. A test environment where nothing real gets charged

Once that exists, fill in UbillingAdapter below and run:

  BILLING_ADAPTER=ubilling UBILLING_URL=... UBILLING_TOKEN=... uvicorn ...

IF U-BILLING HAS NO API AT ALL:
  its database may be read READ-ONLY, through a dedicated user with SELECT
  rights. Never write to another system's database directly - its own
  logic (ledger entries, triggers, history) would be bypassed and the data
  would drift apart.
"""
import logging
import os
from typing import Protocol

import httpx

log = logging.getLogger("bizos.billing")


class BillingAdapter(Protocol):
    """What the core wants from any billing system, whichever one it is."""
    name: str

    def create_invoice(self, *, external_client_id, account, amount,
                       description, phone) -> dict: ...

    def check_payment(self, external_invoice_id: str) -> dict: ...

    def find_client(self, phone: str) -> dict | None: ...

    def health(self) -> dict: ...


# --------------------------------------------------------- mode: stub ----
class NullBilling:
    """Sends nothing anywhere. The product works end to end without billing."""
    name = "null"

    def create_invoice(self, **kw) -> dict:
        return {"external_id": None, "pay_link": None}

    def check_payment(self, external_invoice_id: str) -> dict:
        return {"paid": False, "paid_at": None}

    def find_client(self, phone: str) -> dict | None:
        return None

    def health(self) -> dict:
        return {"ok": True, "mode": "null", "note": "billing not connected"}


# ---------------------------------------------------- mode: local mock ----
class MockUbilling:
    """
    Talks to mocks/ubilling_mock.py over HTTP, exactly as it would to the
    real thing. The only differences are the address and the fact that the
    money is imaginary. This is the mode that shows a customer the full
    invoice cycle.
    """
    name = "mock-ubilling"

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or os.getenv("UBILLING_URL",
                                               "http://127.0.0.1:8081")).rstrip("/")
        self.timeout = 4.0

    def create_invoice(self, *, external_client_id=None, account=None,
                       amount: float = 0, description: str = "",
                       phone: str | None = None) -> dict:
        r = httpx.post(f"{self.base_url}/api/invoices", timeout=self.timeout, json={
            "account": account, "subscriber_id": external_client_id,
            "phone": phone, "amount": amount, "description": description,
        })
        r.raise_for_status()
        d = r.json()
        return {"external_id": d["invoice_id"], "pay_link": d["pay_link"]}

    def check_payment(self, external_invoice_id: str) -> dict:
        r = httpx.get(f"{self.base_url}/api/invoices/{external_invoice_id}",
                      timeout=self.timeout)
        r.raise_for_status()
        d = r.json()
        return {"paid": d["status"] == "paid", "paid_at": d.get("paid_at"),
                "amount": d.get("amount"), "status": d.get("status")}

    def find_client(self, phone: str) -> dict | None:
        r = httpx.get(f"{self.base_url}/api/subscribers",
                      params={"phone": phone}, timeout=self.timeout)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def health(self) -> dict:
        try:
            r = httpx.get(f"{self.base_url}/health", timeout=2.0)
            return {"ok": r.status_code == 200, "mode": "mock",
                    "url": self.base_url, **r.json()}
        except Exception as e:
            return {"ok": False, "mode": "mock", "url": self.base_url,
                    "error": f"billing mock is not running: {e}"}


# ----------------------------------------------------- mode: production ----
class UbillingAdapter:
    """
    SKELETON FOR PRODUCTION U-BILLING.

    Every method is marked TODO - drop the real calls in once the API
    documentation arrives. The response shapes are already agreed with the
    core, so nothing outside this class needs to change.
    """
    name = "ubilling"

    def __init__(self):
        self.base_url = os.getenv("UBILLING_URL", "").rstrip("/")
        self.token = os.getenv("UBILLING_TOKEN", "")

    def _ready(self) -> bool:
        return bool(self.base_url and self.token)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def create_invoice(self, *, external_client_id=None, account=None,
                       amount: float = 0, description: str = "",
                       phone: str | None = None) -> dict:
        if not self._ready():
            raise NotImplementedError(
                "U-billing is not configured: set UBILLING_URL and UBILLING_TOKEN"
            )
        # TODO: real path and field names from the U-billing documentation
        # r = httpx.post(f"{self.base_url}/invoices", headers=self._headers(),
        #                json={"account": account, "sum": amount,
        #                      "comment": description})
        # d = r.json()
        # return {"external_id": d["id"], "pay_link": d["payment_url"]}
        raise NotImplementedError("Invoice creation against U-billing not implemented")

    def check_payment(self, external_invoice_id: str) -> dict:
        raise NotImplementedError("Payment check not implemented")

    def find_client(self, phone: str) -> dict | None:
        raise NotImplementedError("Subscriber lookup not implemented")

    def health(self) -> dict:
        return {"ok": self._ready(), "mode": "ubilling", "url": self.base_url,
                "note": "production mode; needs UBILLING_URL and UBILLING_TOKEN"}


def get_billing() -> BillingAdapter:
    which = os.getenv("BILLING_ADAPTER", "null").lower()
    if which in ("mock", "mock-ubilling"):
        return MockUbilling()
    if which == "ubilling":
        return UbillingAdapter()
    return NullBilling()
