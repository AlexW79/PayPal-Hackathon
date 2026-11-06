import time
import uuid

import httpx

from . import config


class PayPalError(RuntimeError):
    pass


class PayPal:
    def __init__(self):
        self.http = httpx.Client(base_url=config.PAYPAL_BASE_URL, timeout=30)
        self._token, self._expiry = None, 0.0

    def _auth(self) -> str:
        if self._token and time.time() < self._expiry - 60:
            return self._token
        r = self.http.post("/v1/oauth2/token", auth=(config.PAYPAL_CLIENT_ID, config.PAYPAL_CLIENT_SECRET),
                           data={"grant_type": "client_credentials"})
        if r.status_code != 200:
            raise PayPalError(f"PayPal sign-in failed ({r.status_code}). Check your sandbox Client ID/Secret.")
        body = r.json()
        self._token, self._expiry = body["access_token"], time.time() + body.get("expires_in", 3000)
        return self._token

    def _call(self, method: str, path: str, **kw) -> dict:
        headers = {"Authorization": f"Bearer {self._auth()}", "Content-Type": "application/json",
                   **kw.pop("headers", {})}
        r = self.http.request(method, path, headers=headers, **kw)
        if r.status_code >= 400:
            try:
                d = r.json()
                msg = d.get("message", "") + " " + "; ".join(
                    f"{x.get('field', '')} {x.get('issue', '')} {x.get('description', '')}".strip()
                    for x in d.get("details", []))
            except ValueError:
                msg = r.text
            raise PayPalError(f"PayPal {method} {path.split('?')[0]} failed ({r.status_code}): {msg.strip()}")
        return r.json() if r.content else {}

    def send_invoice(self, person: dict, split: dict, payer: str, note: str) -> dict:
        cur = split["currency"]
        money = lambda cents: {"currency_code": cur, "value": f"{cents / 100:.2f}"}  # noqa: E731

        items = [{"name": l["label"][:200], "quantity": "1", "unit_amount": money(l["cents"])}
                 for l in person["lines"]]
        discount = None
        if person["extras_cents"] > 0:
            items.append({"name": "Share of tax, tip & service", "quantity": "1",
                          "unit_amount": money(person["extras_cents"])})
        elif person["extras_cents"] < 0:
            discount = {"invoice_discount": {"amount": money(-person["extras_cents"])}}

        where = split.get("merchant") or "the bill"
        body = {
            "detail": {
                "currency_code": cur,
                "note": note or f"Your share of {where}, paid by {payer}. Thanks!",
                "memo": "Created by SplitSnap",
                "payment_term": {"term_type": "DUE_ON_RECEIPT"},
            },
            "primary_recipients": [{"billing_info": {
                "name": {"given_name": person["name"]},
                "email_address": person["email"],
            }}],
            "items": items,
        }
        if discount:
            body["amount"] = {"breakdown": {"discount": discount}}
        try:
            body["detail"]["invoice_number"] = self._call(
                "POST", "/v2/invoicing/generate-next-invoice-number").get("invoice_number")
        except PayPalError:
            pass  # PayPal assigns one if we don't

        draft = self._call("POST", "/v2/invoicing/invoices", json=body,
                           headers={"Prefer": "return=representation"})
        inv_id = draft["id"]
        self._call("POST", f"/v2/invoicing/invoices/{inv_id}/send",
                   json={"send_to_invoicer": True, "send_to_recipient": True})
        sent = self._call("GET", f"/v2/invoicing/invoices/{inv_id}")
        return {
            "invoice_id": inv_id,
            "invoice_number": sent.get("detail", {}).get("invoice_number"),
            "status": sent.get("status", "SENT"),
            "pay_url": sent.get("detail", {}).get("metadata", {}).get("recipient_view_url"),
        }


    # ---------- tracking & reminders ----------
    def get_status(self, invoice_id: str) -> dict:
        inv = self._call("GET", f"/v2/invoicing/invoices/{invoice_id}")
        paid = inv.get("payments", {}).get("paid_amount", {}).get("value")
        due = inv.get("due_amount", {}).get("value")
        return {"status": inv.get("status", "UNKNOWN"),
                "paid_cents": round(float(paid) * 100) if paid else 0,
                "due_cents": round(float(due) * 100) if due else None}

    def remind(self, invoice_id: str, subject: str, note: str) -> None:
        self._call("POST", f"/v2/invoicing/invoices/{invoice_id}/remind", json={
            "subject": subject[:100], "note": note[:4000],
            "send_to_invoicer": False, "send_to_recipient": True})


_client: PayPal | None = None


def client() -> PayPal | None:
    """Shared PayPal client (keeps the access token between calls), or None in demo mode."""
    global _client
    if not config.paypal_enabled():
        return None
    if _client is None:
        _client = PayPal()
    return _client


# In-memory records for this server run: reminder counts, and simulated invoices in demo mode.
REMINDERS: dict[str, int] = {}
DEMO_INVOICES: dict[str, dict] = {}

PAID_STATES = {"PAID", "MARKED_AS_PAID"}


def _state(status: str) -> str:
    if status in PAID_STATES:
        return "paid"
    if status in ("PARTIALLY_PAID", "MARKED_AS_PARTIALLY_PAID"):
        return "partial"
    if status in ("CANCELLED", "REFUNDED", "PARTIALLY_REFUNDED", "MARKED_AS_REFUNDED"):
        return "cancelled"
    return "waiting"


def statuses(invoice_ids: list[str]) -> dict[str, dict]:
    pp = client()
    out = {}
    for inv_id in invoice_ids:
        try:
            if inv_id in DEMO_INVOICES or pp is None:
                demo = DEMO_INVOICES.get(inv_id, {"status": "UNPAID", "paid_cents": 0})
                info = {"status": demo["status"], "paid_cents": demo["paid_cents"], "due_cents": None}
            else:
                info = pp.get_status(inv_id)
            out[inv_id] = {**info, "state": _state(info["status"]), "reminders": REMINDERS.get(inv_id, 0)}
        except (PayPalError, httpx.HTTPError) as e:
            out[inv_id] = {"status": "UNKNOWN", "state": "unknown", "error": str(e),
                           "reminders": REMINDERS.get(inv_id, 0)}
    return out


def send_reminder(invoice_id: str, subject: str, note: str) -> dict:
    pp = client()
    simulated = invoice_id in DEMO_INVOICES or pp is None
    if not simulated:
        pp.remind(invoice_id, subject, note)
    REMINDERS[invoice_id] = REMINDERS.get(invoice_id, 0) + 1
    return {"ok": True, "simulated": simulated, "reminders": REMINDERS[invoice_id]}


def demo_pay(invoice_id: str) -> dict:
    """Demo mode only: pretend the friend paid, so the tracker can be shown without the sandbox."""
    if invoice_id not in DEMO_INVOICES:
        raise PayPalError("Only simulated (demo) invoices can be marked paid here.")
    DEMO_INVOICES[invoice_id].update(status="PAID", paid_cents=DEMO_INVOICES[invoice_id]["total_cents"])
    return {"ok": True}


def send_all(split: dict, payer: str, note: str) -> list[dict]:
    """Send an invoice to everyone except the payer. Returns one result per person."""
    pp = client()
    results = []
    for p in split["people"]:
        if p["is_me"] or p["total_cents"] <= 0:
            continue
        base = {"name": p["name"], "email": p["email"], "total_cents": p["total_cents"],
                "items": [l["label"] for l in p["lines"]]}
        if not p.get("email"):
            results.append({**base, "ok": False, "error": "Missing email address"})
            continue
        if pp is None:
            inv_id = f"DEMO-{uuid.uuid4().hex[:8].upper()}"
            DEMO_INVOICES[inv_id] = {"status": "UNPAID", "paid_cents": 0, "total_cents": p["total_cents"]}
            results.append({**base, "ok": True, "simulated": True, "status": "UNPAID",
                            "invoice_id": inv_id, "pay_url": None})
            continue
        try:
            results.append({**base, "ok": True, "simulated": False, **pp.send_invoice(p, split, payer, note)})
        except (PayPalError, httpx.HTTPError) as e:
            results.append({**base, "ok": False, "error": str(e)})
    return results
