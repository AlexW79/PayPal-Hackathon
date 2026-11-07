
import json
import re
import time
from typing import Optional

from pydantic import BaseModel

from . import config

# ---------------- schemas (also used as Gemini response schemas) ----------------

class ReceiptItem(BaseModel):
    name: str
    quantity: int
    total_price: float  # price for the whole line (quantity x unit price)


class Receipt(BaseModel):
    merchant: Optional[str]
    date: Optional[str]
    currency: str
    items: list[ReceiptItem]
    subtotal: Optional[float]
    tax: Optional[float]
    tip: Optional[float]
    service_charge: Optional[float]
    discount: Optional[float]
    total: Optional[float]


class Person(BaseModel):
    name: str
    email: Optional[str]
    is_me: bool


class Share(BaseModel):
    person: str
    weight: float  # relative portion, e.g. 2 for "two of the three beers"


class Assignment(BaseModel):
    item_index: int
    shares: list[Share]


class Ambiguity(BaseModel):
    said: str            # the name as the user said it, e.g. "Matt"
    options: list[str]   # contact full names it could be


class SplitPlan(BaseModel):
    people: list[Person]
    assignments: list[Assignment]
    tip_percent: Optional[float]  # only if the user asks to ADD a tip that isn't on the receipt
    notes: Optional[str]
    ambiguous: list[Ambiguity]    # names that match more than one contact


# ---------------- prompts ----------------

RECEIPT_PROMPT = """You are reading a photo of a restaurant or shop bill.
Extract every purchased line item exactly as printed. For each line give the name, the quantity
(1 if not shown) and the TOTAL price for that line (not the unit price).
Also extract the subtotal, tax, tip/gratuity, service charge, discount (as a positive number) and
grand total if they are printed; use null for anything not on the bill.
Use the ISO currency code (e.g. USD, GBP, EUR) based on the symbols and location; default to USD.
Do not invent items. If the image is not a bill, return an empty items list."""

SPLIT_PROMPT = """You split a bill between friends.

The person using the app (the one who paid the bill) is called "{payer}". Words like "I", "me",
"my", "mine" refer to them: set is_me=true for exactly that person and use the name "{payer}".

Bill items (index: name, line total):
{items}

The user's saved contacts (full name <email>):
{contacts}

What the user said about who had what:
\"\"\"{description}\"\"\"

Instructions:
- List every person mentioned, plus "{payer}".
- Match people to saved contacts: if a name (or first name, nickname or close misspelling) clearly
  matches exactly ONE contact, use that contact's full name and email. If a name could be more than one
  contact (e.g. "Matt" with contacts Matt Smith and Matt Peters), add it to `ambiguous` with the possible
  full names, and still use the name as said in people/assignments. Otherwise ambiguous is an empty list.
- For people who aren't contacts, include an email only if the user said it.
- For every item index, say who shares it and their relative weight. Equal sharing = weight 1 each.
  "Sam had two of the beers and I had one" -> Sam weight 2, {payer} weight 1.
- "everyone", "all of us", "we shared" means all people listed.
- Items nobody mentioned: share them equally between everyone and say so in notes.
- Match items loosely ("the pizza" matches "Margherita Pizza").
- Only set tip_percent if the user asks to add a tip that is NOT already on the bill; else null.
- notes: one or two short sentences about any assumptions you made, or null."""


# ---------------- Gemini ----------------

_CLIENT = None


def _client():
    # Keep one client alive for the whole app: a throwaway client can be closed by garbage
    # collection while its request is still running.
    global _CLIENT
    if _CLIENT is None:
        from google import genai
        from google.genai import types
        # Time limit per request, so a slow or stuck model can't freeze the app.
        _CLIENT = genai.Client(api_key=config.GEMINI_API_KEY,
                               http_options=types.HttpOptions(timeout=config.GEMINI_TIMEOUT_SECONDS * 1000))
    return _CLIENT


def _generate(contents, schema=None) -> str:
    from google.genai import types
    cfg = types.GenerateContentConfig(temperature=0)
    if schema is not None:
        cfg = types.GenerateContentConfig(temperature=0, response_mime_type="application/json",
                                          response_schema=schema)
    # When a model is busy (503) or rate-limited (429), move straight on to the next fallback
    # model; after one full pass, wait briefly and try the list once more.
    models = [config.GEMINI_MODEL] + [m for m in config.GEMINI_FALLBACK_MODELS if m != config.GEMINI_MODEL]
    last_error: Exception | None = None
    deadline = time.time() + 60  # never keep the user waiting much more than a minute
    for round_ in range(2):
        for model in models:
            if time.time() > deadline:
                break
            try:
                resp = _client().models.generate_content(model=model, contents=contents, config=cfg)
                return resp.text or ""
            except Exception as e:  # noqa: BLE001
                busy = any(s in str(e) for s in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED")) or \
                    "timeout" in type(e).__name__.lower() or "timed out" in str(e).lower()
                if not busy:
                    raise
                last_error = e
        if round_ == 0:
            time.sleep(3)
    raise last_error or RuntimeError("Gemini returned no response")


def read_receipt(image: bytes, mime_type: str) -> Receipt:
    if not config.ai_enabled():
        return demo_receipt()
    from google.genai import types
    text = _generate([types.Part.from_bytes(data=image, mime_type=mime_type), RECEIPT_PROMPT], Receipt)
    return Receipt.model_validate_json(text)


def plan_split(receipt: Receipt, description: str, payer: str, contacts: list[dict] | None = None) -> SplitPlan:
    contacts = contacts or []
    if not config.ai_enabled():
        return demo_split(receipt, description, payer, contacts)
    items = "\n".join(f"{i}: {it.name} (x{it.quantity}), {it.total_price:.2f}"
                      for i, it in enumerate(receipt.items))
    known = "\n".join(f"- {c['name']} <{c.get('email') or 'no email'}>" for c in contacts) or "(none)"
    prompt = SPLIT_PROMPT.format(payer=payer, items=items, description=description, contacts=known)
    return SplitPlan.model_validate_json(_generate([prompt], SplitPlan))


def transcribe(audio: bytes, mime_type: str) -> str:
    if not config.ai_enabled():
        raise RuntimeError("Voice transcription needs GEMINI_API_KEY (or use a browser with built-in speech).")
    from google.genai import types
    return _generate([types.Part.from_bytes(data=audio, mime_type=mime_type),
                      "Transcribe this voice note exactly. Return only the transcript text."]).strip()


# ---------------- payment reminders ----------------

class ReminderDraft(BaseModel):
    subject: str
    note: str


REMINDER_PROMPT = """Write a short payment reminder from {payer} to their friend {name}, who still owes their
share of a bill. It is sent through PayPal with a "Pay now" link, so don't include links or payment details.

Facts:
- Place: {merchant}
- {name}'s share: {amount}
- What {name} had: {items}
- Request sent: {age} ago
- This is reminder number {number}.

Tone by reminder number:
1 = light and friendly, casual, one emoji is fine.
2 = still warm, but clear that the money is outstanding.
3 or more = polite but firm and direct; no emoji, no guilt-tripping, no threats.

Rules: write as {payer} in the first person, address {name} by name, mention the amount,
note under 55 words, subject under 60 characters. Sign off with "{payer}"."""

_DEMO_REMINDERS = [
    ("Quick nudge about {merchant} 🍽️", "Hey {name}! Just a friendly reminder about your {amount} share from "
     "{merchant}. You can pay it with PayPal whenever you get a sec. Thanks! {payer}"),
    ("Reminder: your {amount} from {merchant}", "Hi {name}, just following up on your {amount} share from "
     "{merchant}. It's still outstanding, so it'd be great if you could settle it soon via PayPal. Thanks, {payer}"),
    ("Payment still outstanding: {amount}", "Hi {name}, your {amount} share from {merchant} is still unpaid "
     "after a few reminders. Please pay it via PayPal today. Thank you, {payer}"),
]


def _age(minutes: float) -> str:
    if minutes < 90:
        return f"{max(int(minutes), 1)} minutes"
    if minutes < 48 * 60:
        return f"{int(minutes // 60)} hours"
    return f"{int(minutes // 1440)} days"


def draft_reminder(name: str, payer: str, amount: str, merchant: str, items: list[str],
                   minutes_since_sent: float, number: int) -> ReminderDraft:
    facts = dict(name=name, payer=payer, amount=amount, merchant=merchant or "dinner",
                 items=", ".join(items) or "their share", age=_age(minutes_since_sent), number=number)
    if not config.ai_enabled():
        subject, note = _DEMO_REMINDERS[min(number, 3) - 1]
        return ReminderDraft(subject=subject.format(**facts), note=note.format(**facts))
    return ReminderDraft.model_validate_json(_generate([REMINDER_PROMPT.format(**facts)], ReminderDraft))


# ---------------- demo mode (no API key) ----------------

def demo_receipt() -> Receipt:
    return Receipt.model_validate(json.loads(config.SAMPLE_RECEIPT_JSON.read_text(encoding="utf-8")))


_STOP = {"I", "I'm", "The", "We", "And", "Me", "My", "Everyone", "All", "Our", "Us", "Then", "Also",
         "They", "He", "She", "Both", "Split", "Shared", "Had"}


def demo_split(receipt: Receipt, description: str, payer: str, contacts: list[dict] | None = None) -> SplitPlan:
    """Very simple rules so the UI can be tried without a key: each clause links the names
    and item keywords it mentions. Contact full names (and unique first names) are recognised."""
    contacts = contacts or []
    emails = dict(re.findall(r"\b([A-Z][a-z]+)'?s? email is (\S+@\S+?)[.,;]?(?:\s|$)", description))
    by_first: dict[str, list[dict]] = {}
    for c in contacts:
        by_first.setdefault(c["name"].split()[0].lower(), []).append(c)

    names: list[str] = []          # people, using contact full names where possible
    aliases: dict[str, str] = {}   # text that refers to a person -> their name
    ambiguous: list[Ambiguity] = []
    rest = description
    for c in contacts:             # full names first, e.g. "Matt Smith"
        if re.search(rf"\b{re.escape(c['name'])}\b", description, re.I):
            names.append(c["name"])
            aliases[c["name"]] = c["name"]
            rest = re.sub(rf"\b{re.escape(c['name'])}\b", " ", rest, flags=re.I)
    for w in re.findall(r"\b[A-Z][a-z]+\b", rest):
        if w in _STOP or w == payer or any(w.lower() in it.name.lower() for it in receipt.items):
            continue
        matches = by_first.get(w.lower(), [])
        name = matches[0]["name"] if len(matches) == 1 else w
        if len(matches) > 1 and not any(a.said == w for a in ambiguous):
            ambiguous.append(Ambiguity(said=w, options=[m["name"] for m in matches]))
        aliases.setdefault(w, name)
        if name not in names:
            names.append(name)
    contact_email = {c["name"]: c.get("email") for c in contacts}
    people = [Person(name=payer, email=None, is_me=True)] + [
        Person(name=n, email=emails.get(n.split()[0]) or contact_email.get(n), is_me=False) for n in names]
    everyone = [p.name for p in people]

    assignments: dict[int, list[str]] = {}
    for clause in re.split(r"[.;\n]", description):
        who = []
        for alias, n in aliases.items():
            if re.search(rf"\b{re.escape(alias)}\b", clause, re.I) and n not in who:
                who.append(n)
        if re.search(r"\b(I|me|my|I'll|I've)\b", clause):
            who.append(payer)
        if re.search(r"\b(everyone|all of us|we all|shared)\b", clause, re.I) and not who:
            who = everyone
        if not who:
            continue
        for i, it in enumerate(receipt.items):
            keywords = [k for k in re.findall(r"[a-z]{3,}", it.name.lower())]
            if any(re.search(rf"\b{k}", clause.lower()) for k in keywords):
                assignments.setdefault(i, [])
                assignments[i] += [w for w in who if w not in assignments[i]]

    result = []
    for i in range(len(receipt.items)):
        who = assignments.get(i, everyone)
        result.append(Assignment(item_index=i, shares=[Share(person=w, weight=1) for w in who]))
    unmentioned = len(receipt.items) - len(assignments)
    note = "Demo mode (no Gemini key): matched names and item words with simple rules."
    if unmentioned:
        note += f" {unmentioned} unmentioned item(s) were shared equally."
    return SplitPlan(people=people, assignments=result, tip_percent=None, notes=note, ambiguous=ambiguous)
