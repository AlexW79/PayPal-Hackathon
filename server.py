import logging

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from splitter import ai, billing, config, contacts, paypal

log = logging.getLogger("splitsnap")
app = FastAPI(title="SplitSnap")


@app.middleware("http")
async def no_cache(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache"
    return response

MAX_UPLOAD = 12 * 1024 * 1024


def _ai_error(e: Exception) -> HTTPException:
    log.exception("AI call failed")
    msg = str(e)
    if not config.ai_enabled():
        pass  # demo-mode messages are already user-friendly
    elif "API key" in msg or "API_KEY" in msg or "PERMISSION_DENIED" in msg:
        msg = "Gemini rejected the API key. Check GEMINI_API_KEY in .env."
    elif "timed out" in msg.lower() or "timeout" in type(e).__name__.lower():
        msg = "Gemini didn't respond in time. Please try again in a minute."
    elif "UNAVAILABLE" in msg or "503" in msg:
        msg = "Gemini is very busy right now. Please try again in a minute."
    elif "RESOURCE_EXHAUSTED" in msg or "429" in msg:
        msg = "Gemini free-tier rate limit reached. Wait a minute and try again."
    elif "NOT_FOUND" in msg and "model" in msg.lower():
        msg = f"Gemini model '{config.GEMINI_MODEL}' not found. Set GEMINI_MODEL in .env to a current model."
    return HTTPException(502, f"AI step failed: {msg}")


@app.get("/api/config")
def get_config():
    return {
        "ai": "gemini" if config.ai_enabled() else "demo",
        "model": config.GEMINI_MODEL if config.ai_enabled() else None,
        "paypal": config.PAYPAL_ENV if config.paypal_enabled() else "demo",
    }


@app.post("/api/receipt")
async def read_receipt(image: UploadFile = File(...)):
    data = await image.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Image is too large (max 12 MB).")
    try:
        receipt = ai.read_receipt(data, image.content_type or "image/jpeg")
    except Exception as e:  # noqa: BLE001
        raise _ai_error(e)
    if not receipt.items:
        raise HTTPException(422, "Couldn't find any items on that image. Try a clearer, flatter photo.")
    return receipt


@app.get("/api/sample-receipt")
def sample_receipt():
    return ai.demo_receipt()


@app.post("/api/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    data = await audio.read()
    try:
        return {"text": ai.transcribe(data, (audio.content_type or "audio/ogg").split(";")[0])}
    except Exception as e:  # noqa: BLE001
        raise _ai_error(e)


class SplitRequest(BaseModel):
    receipt: ai.Receipt
    description: str
    payer: str = "Me"


@app.post("/api/split")
def split(req: SplitRequest):
    if not req.description.strip():
        raise HTTPException(400, "Describe who had what first.")
    if not req.receipt.items:
        raise HTTPException(400, "The bill has no items.")
    payer = req.payer.strip() or "Me"
    saved = contacts.list_contacts()
    try:
        plan = ai.plan_split(req.receipt, req.description, payer, saved)
    except Exception as e:  # noqa: BLE001
        raise _ai_error(e)
    # A name could be more than one contact: ask the user before splitting.
    unclear = [a.model_dump() for a in plan.ambiguous if len(a.options) > 1]
    if unclear:
        return {"clarify": unclear}
    try:
        result = billing.compute(req.receipt, plan)
    except ValueError as e:
        raise HTTPException(422, str(e))
    for p in result["people"]:  # fill in emails from saved contacts
        if not p["is_me"] and not p["email"]:
            c = contacts.find(name=p["name"])
            if c and c.get("email"):
                p["email"] = c["email"]
    return result


class SendRequest(BaseModel):
    split: dict
    payer: str = "Me"
    note: str = ""
    adjustments: dict = {}


@app.post("/api/send")
def send(req: SendRequest):
    return {"results": paypal.send_all(req.split, req.payer.strip() or "Me", req.note.strip(), req.adjustments),
            "mode": config.PAYPAL_ENV if config.paypal_enabled() else "demo"}


# contacts and balances

@app.get("/api/contacts")
def get_contacts():
    return {"contacts": contacts.list_contacts()}


class ContactRequest(BaseModel):
    name: str
    email: str | None = None


@app.post("/api/contacts")
def save_contact(req: ContactRequest):
    try:
        return contacts.upsert(req.name, req.email)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/contacts/{contact_id}")
def delete_contact(contact_id: str):
    contacts.delete(contact_id)
    return {"ok": True}


class IouRequest(BaseModel):
    amount: float
    note: str = ""
    currency: str = "USD"


@app.post("/api/contacts/{contact_id}/iou")
def add_iou(contact_id: str, req: IouRequest):
    try:
        return contacts.add_iou(contact_id, round(req.amount * 100), req.note, req.currency)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/contacts/{contact_id}/iou/{iou_id}")
def delete_iou(contact_id: str, iou_id: str):
    contacts.remove_iou(contact_id, iou_id)
    return {"ok": True}


class BalancesRequest(BaseModel):
    people: list[dict]


@app.post("/api/balances")
def get_balances(req: BalancesRequest):
    return {"balances": paypal.balances(req.people[:20])}


#tracking and reminders

class StatusRequest(BaseModel):
    invoice_ids: list[str]


@app.post("/api/status")
def status(req: StatusRequest):
    return {"statuses": paypal.statuses(req.invoice_ids[:50])}


class DraftRequest(BaseModel):
    invoice_id: str
    name: str
    payer: str = "Me"
    amount: str
    merchant: str | None = None
    items: list[str] = []
    minutes_since_sent: float = 0


@app.post("/api/reminder/draft")
def reminder_draft(req: DraftRequest):
    number = paypal.REMINDERS.get(req.invoice_id, 0) + 1
    try:
        draft = ai.draft_reminder(req.name, req.payer.strip() or "Me", req.amount, req.merchant or "",
                                  req.items, req.minutes_since_sent, number)
    except Exception as e:  # noqa: BLE001
        raise _ai_error(e)
    return {**draft.model_dump(), "number": number}


class RemindRequest(BaseModel):
    invoice_id: str
    subject: str
    note: str


@app.post("/api/reminder/send")
def reminder_send(req: RemindRequest):
    if not req.note.strip():
        raise HTTPException(400, "The reminder message is empty.")
    try:
        return paypal.send_reminder(req.invoice_id, req.subject.strip() or "Payment reminder", req.note.strip())
    except paypal.PayPalError as e:
        raise HTTPException(502, str(e))


class DemoPayRequest(BaseModel):
    invoice_id: str


@app.post("/api/demo/pay")
def demo_pay(req: DemoPayRequest):
    try:
        return paypal.demo_pay(req.invoice_id)
    except paypal.PayPalError as e:
        raise HTTPException(400, str(e))


app.mount("/", StaticFiles(directory=config.ROOT / "static", html=True), name="static")
