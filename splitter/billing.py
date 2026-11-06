
from fractions import Fraction

from .ai import Receipt, SplitPlan


def _cents(x: float | None) -> int:
    return int(round((x or 0) * 100))


def _largest_remainder(exact: dict[str, Fraction], total: int) -> dict[str, int]:
    """Round exact cent amounts to whole cents so they sum to `total`."""
    floors = {k: int(v // 1) for k, v in exact.items()}
    left = total - sum(floors.values())
    order = sorted(exact, key=lambda k: exact[k] - floors[k], reverse=True)
    for k in order[:max(left, 0)]:
        floors[k] += 1
    return floors


def _share_label(frac: Fraction) -> str:
    if frac == 1:
        return ""
    nice = {Fraction(1, 2): "½", Fraction(1, 3): "⅓", Fraction(2, 3): "⅔", Fraction(1, 4): "¼",
            Fraction(3, 4): "¾"}
    return f" ({nice.get(frac, f'{frac.numerator}/{frac.denominator}')} share)"


def compute(receipt: Receipt, plan: SplitPlan) -> dict:
    # --- people (dedupe, case-insensitive; names in shares may add people) ---
    people: dict[str, dict] = {}

    def person(name: str) -> dict:
        key = name.strip().lower()
        if key not in people:
            people[key] = {"name": name.strip(), "email": None, "is_me": False, "lines": []}
        return people[key]

    for p in plan.people:
        rec = person(p.name)
        rec["email"] = rec["email"] or (p.email or None)
        rec["is_me"] = rec["is_me"] or p.is_me
    for a in plan.assignments:
        for s in a.shares:
            person(s.person)
    if not people:
        raise ValueError("No people found in the description.")

    # --- items ---
    by_item = {a.item_index: a for a in plan.assignments}
    exact_items: dict[str, Fraction] = {k: Fraction(0) for k in people}
    unassigned = []
    for i, item in enumerate(receipt.items):
        price = _cents(item.total_price)
        a = by_item.get(i)
        shares = [s for s in (a.shares if a else []) if s.weight > 0]
        if not shares:
            unassigned.append(item.name)
            shares_w = {k: Fraction(1) for k in people}
        else:
            shares_w = {}
            for s in shares:
                k = s.person.strip().lower()
                shares_w[k] = shares_w.get(k, Fraction(0)) + Fraction(s.weight).limit_denominator(100)
        total_w = sum(shares_w.values())
        for k, w in shares_w.items():
            frac = w / total_w
            amount = Fraction(price) * frac
            exact_items[k] += amount
            people[k]["lines"].append({"item": item.name, "fraction": float(frac),
                                       "label": item.name + _share_label(frac),
                                       "exact_cents": amount})

    items_total = sum(_cents(i.total_price) for i in receipt.items)

    # --- extras (tax, tip, service, discount), split in proportion to what each person had ---
    tip = _cents(receipt.tip)
    added_tip = 0
    if not tip and plan.tip_percent:
        added_tip = round(items_total * plan.tip_percent / 100)
        tip = added_tip
    extras = _cents(receipt.tax) + tip + _cents(receipt.service_charge) - _cents(receipt.discount)
    grand_total = items_total + extras

    exact_totals = {k: exact_items[k] + (Fraction(extras) * exact_items[k] / items_total if items_total else 0)
                    for k in people}
    totals = _largest_remainder(exact_totals, grand_total)

    # --- build output: round each line, the extras line absorbs rounding so it sums exactly ---
    out_people = []
    for k, p in people.items():
        lines = [{"label": l["label"], "item": l["item"], "fraction": l["fraction"],
                  "cents": int(round(l["exact_cents"]))} for l in p["lines"]]
        lines = [l for l in lines if l["cents"] > 0]
        extras_cents = totals[k] - sum(l["cents"] for l in lines)
        out_people.append({
            "name": p["name"], "email": p["email"], "is_me": p["is_me"],
            "lines": lines,
            "items_cents": sum(l["cents"] for l in lines),
            "extras_cents": extras_cents,
            "total_cents": totals[k],
        })
    out_people.sort(key=lambda p: (not p["is_me"], p["name"].lower()))

    printed_total = _cents(receipt.total)
    return {
        "currency": receipt.currency or "USD",
        "merchant": receipt.merchant,
        "date": receipt.date,
        "items_cents": items_total,
        "tax_cents": _cents(receipt.tax),
        "tip_cents": tip,
        "added_tip_percent": plan.tip_percent if added_tip else None,
        "service_cents": _cents(receipt.service_charge),
        "discount_cents": _cents(receipt.discount),
        "total_cents": grand_total,
        "printed_total_cents": printed_total or None,
        "total_mismatch": bool(printed_total and abs(printed_total - grand_total) > 1 and not added_tip),
        "unassigned_items": unassigned,
        "notes": plan.notes,
        "people": out_people,
    }
