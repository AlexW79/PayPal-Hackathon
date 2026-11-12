# SplitSnap: split the bill with AI, get paid with PayPal

Snap a photo of the bill, **say or type** who had what ("Sam had the pizza, Priya and Sam shared the
nachos, everyone split dessert"), and SplitSnap sends each friend a **PayPal invoice** for exactly
their share, with tax and tip split fairly.

Built for the *Build What's Next with PayPal and AI* hackathon.
Try it here using Render: https://splitsnap-2rlf.onrender.com/      
View the Demo Video here: https://youtu.be/GA_44Pckg5s

## How it works

| Step | What happens | Tech |
|---|---|---|
| 1. Scan | Take a photo (phone camera or webcam) or upload an image. AI reads every line item, tax, tip and total. Everything is editable. | **Google Gemini** (vision, structured JSON output) |
| 2. Who had what | Type or **speak** a plain-English description. Voice uses the browser's speech recognition, or Gemini audio transcription as a fallback. | Web Speech API / **Gemini** |
| 3. Review | AI turns the description into item assignments (incl. "two of the three beers"). **Python does all the maths**: shares, proportional tax/tip, cent-exact rounding that always matches the bill total. | **Gemini** + deterministic Python |
| 4. Request | One click sends a PayPal invoice to each person, itemised with their share. Friends pay with PayPal. | **PayPal Invoicing v2 API** (sandbox) |
| Contacts & "which Matt?" | Saved contacts appear as chips under the description; tap one to insert their name. While you type or talk, SplitSnap asks about names that could be several people ("Which Matt? Matt Smith / Matt Peters") and likely misspellings ("Pirya" → Priya), and fixes the text with one tap. Gemini also gets your contacts, so it fills in emails and flags anything still unclear. | Browser + **Gemini** |
| Settle balances | Before sending, SplitSnap checks **existing balances**: unpaid PayPal invoices that person already owes you are rolled into the new request (the old invoice is cancelled), and money *you* owe them (logged in Contacts) is taken off. One payment instead of two; if your debt covers their share, no request is sent at all. | **PayPal Invoicing** (search, cancel, discounts) |
| 5. Track & remind | A live tracker checks each invoice every few seconds: "Collected $50 of $90", a status per friend, and a 🎉 when someone pays. **Remind** drafts a personal nudge whose tone escalates (friendly, then clear, then firm); you edit it and it's sent through PayPal. | **PayPal Invoicing** (status + remind) + **Gemini** |

**Design principle:** the AI understands, and code does the arithmetic. Gemini extracts items and interprets
who had what; `splitter/billing.py` calculates the money, so the totals are always exact.

## Run it

Requires Python 3.10+.

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # macOS/Linux: cp .env.example .env
uvicorn server:app --reload
```

Open **http://localhost:8000** (the camera and microphone work on `localhost`; on other hosts they need HTTPS).

### Deploy on Render

The repo includes a `render.yaml` blueprint, so it deploys as a free Render web service with HTTPS
(phones need HTTPS for the camera and microphone).

1. In the Render dashboard choose **New → Blueprint** and connect this GitHub repo.
2. Enter `GEMINI_API_KEY`, `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` when asked (leave any blank for demo mode).
3. Click **Apply**. The app is live at `https://splitsnap-xxxx.onrender.com` after a few minutes.

On the free plan the service sleeps after 15 minutes idle (the first visit takes about a minute to wake),
and saved contacts reset to the samples on each restart.

### Keys (both free)

| Key | Where | Without it |
|---|---|---|
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey | Demo mode: "Try a sample bill" + simple rule-based splitting |
| `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` | developer.paypal.com, then Apps & Credentials, then **Sandbox**, then create app | Demo mode: invoices are simulated |

The header badges show which mode each part is in.

### Testing with the PayPal sandbox

1. In the PayPal developer dashboard, go to **Sandbox accounts**. You'll see a *business* account (that's you,
   the invoicer, tied to your app's credentials) and a *personal* account.
2. In step 3, use the sandbox **personal** account's email for a friend.
3. After sending, click **View invoice ↗**, log in with the sandbox personal account and pay. The invoice
   shows as paid in your sandbox business account.

### Sample bill

`samples/sample_receipt.png` is a realistic receipt photo you can upload to test Gemini's reading
(regenerate with `python scripts/make_sample_receipt.py`, which needs `pip install pillow`).
Try this description:

> I had the cheeseburger and the lemonade. Sam had the pizza. Priya and Sam shared the nachos. Sam had two
> of the beers and I had one. Everyone split the lava cake. Add an 18% tip.

## Project layout

```
server.py              FastAPI app + API routes, serves the front end
splitter/ai.py         Gemini: receipt reading, split planning, transcription (+ demo mode)
splitter/billing.py    Exact money maths (fractions, proportional extras, largest-remainder rounding)
splitter/paypal.py     PayPal OAuth + Invoicing v2 (create, send, status, remind, search, cancel)
splitter/contacts.py   Saved contacts + money you owe them (data/contacts.json, not committed)
static/                Front end (plain HTML/CSS/JS, PayPal-inspired styling, no build step)
samples/               Sample receipt (JSON + photo)
```

## AI tools used

- **Google Gemini** (`gemini-3.8-flash` by default, set with `GEMINI_MODEL`) via the `google-genai` SDK:
  - *Vision:* reads the bill photo into structured JSON (items, quantities, tax, tip, total) using a response schema.
  - *Language understanding:* converts the user's free-form description into a structured assignment plan
    (people, emails, who shared which item, weighted shares, requested tip).
  - *Writing:* drafts payment reminders that adapt to the friend, amount, items, time since the request and how many reminders came before.
  - *Audio:* transcribes voice notes on browsers without built-in speech recognition.
- Browser **Web Speech API** for live voice input in Chrome, Edge and Safari.

## PayPal technology used

- **PayPal REST OAuth 2.0** (client credentials)
- **Invoicing v2 API**: `generate-next-invoice-number`, create invoice, send invoice, get invoice (payer link and live payment status), **remind** (send a payment reminder), **search invoices** (find a friend's unpaid balance), **cancel** (replace an old invoice when balances are combined), invoice discounts (net off money you owe)

## License

MIT, see [LICENSE](LICENSE).
