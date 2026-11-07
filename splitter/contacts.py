"""Saved contacts and "I owe them" balances, stored in data/contacts.json.

Contacts are added by hand or learned automatically from people you've invoiced.
"I owe" entries (IOUs) record money you owe a friend, so a new request to them can be
netted off instead of creating two payments."""
import json
import re
import threading
import uuid
from datetime import date

from . import config

_lock = threading.Lock()


def _load() -> list[dict]:
    path = config.CONTACTS_PATH
    if not path.exists():
        # First run: start from the sample contacts so the demo has people to pick from.
        if config.SAMPLE_CONTACTS_PATH.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(config.SAMPLE_CONTACTS_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("contacts", [])
    except (ValueError, OSError):
        return []


def _save(contacts: list[dict]) -> None:
    config.CONTACTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.CONTACTS_PATH.write_text(json.dumps({"contacts": contacts}, indent=1), encoding="utf-8")


def _key(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _public(c: dict) -> dict:
    open_ious = [i for i in c.get("i_owe", []) if i.get("cents", 0) > 0]
    return {**c, "i_owe": open_ious, "i_owe_cents": sum(i["cents"] for i in open_ious)}


def list_contacts() -> list[dict]:
    with _lock:
        return [_public(c) for c in sorted(_load(), key=lambda c: c["name"].lower())]


def find(name: str | None = None, email: str | None = None) -> dict | None:
    with _lock:
        contacts = _load()
    if email:
        for c in contacts:
            if _key(c.get("email")) == _key(email):
                return c
    if name:
        matches = [c for c in contacts if _key(c["name"]) == _key(name)]
        if len(matches) == 1:
            return matches[0]
    return None


def upsert(name: str, email: str | None) -> dict:
    name = re.sub(r"\s+", " ", name.strip())
    if not name:
        raise ValueError("Name is required.")
    with _lock:
        contacts = _load()
        existing = None
        if email:
            existing = next((c for c in contacts if _key(c.get("email")) == _key(email)), None)
        if existing is None:
            existing = next((c for c in contacts if _key(c["name"]) == _key(name)), None)
        if existing:
            existing["name"] = name if len(name) >= len(existing["name"]) else existing["name"]
            if email:
                existing["email"] = email.strip()
        else:
            existing = {"id": uuid.uuid4().hex[:8], "name": name, "email": (email or "").strip() or None,
                        "i_owe": []}
            contacts.append(existing)
        _save(contacts)
        return _public(existing)


def delete(contact_id: str) -> None:
    with _lock:
        _save([c for c in _load() if c["id"] != contact_id])


def add_iou(contact_id: str, cents: int, note: str, currency: str = "USD") -> dict:
    if cents <= 0:
        raise ValueError("Amount must be more than zero.")
    with _lock:
        contacts = _load()
        c = next((c for c in contacts if c["id"] == contact_id), None)
        if not c:
            raise ValueError("Contact not found.")
        c.setdefault("i_owe", []).append({"id": uuid.uuid4().hex[:8], "cents": cents, "currency": currency,
                                          "note": note.strip() or "Money I owe", "date": date.today().isoformat()})
        _save(contacts)
        return _public(c)


def remove_iou(contact_id: str, iou_id: str) -> None:
    with _lock:
        contacts = _load()
        for c in contacts:
            if c["id"] == contact_id:
                c["i_owe"] = [i for i in c.get("i_owe", []) if i["id"] != iou_id]
        _save(contacts)


def ious_for(email: str | None, name: str | None) -> tuple[dict | None, list[dict]]:
    c = find(name=name, email=email)
    if not c:
        return None, []
    return c, [i for i in c.get("i_owe", []) if i.get("cents", 0) > 0]


def settle_ious(contact_id: str, iou_ids: list[str], amount_cents: int) -> int:
    """Use up to `amount_cents` of the given IOUs (oldest first). Returns cents actually applied."""
    applied = 0
    with _lock:
        contacts = _load()
        c = next((c for c in contacts if c["id"] == contact_id), None)
        if not c:
            return 0
        for iou in c.get("i_owe", []):
            if iou["id"] not in iou_ids or applied >= amount_cents:
                continue
            use = min(iou["cents"], amount_cents - applied)
            iou["cents"] -= use
            applied += use
            if iou["cents"] == 0:
                iou["settled"] = date.today().isoformat()
        c["i_owe"] = [i for i in c.get("i_owe", []) if i["cents"] > 0]
        _save(contacts)
    return applied
