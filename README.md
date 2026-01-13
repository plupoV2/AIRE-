# AIRE™ Investor-Ready Demo (Streamlit)

This repo is a demo-ready MVP for your proprietary AIRE™ underwriting system.

## What it does
- Paste a listing link (Zillow or any site)
- Extracts a probable address from the URL path (NO scraping)
- Optional: Auto-fill value/price from legal data APIs (Estated / ATTOM)
- Produces A–F grade + verdict + risk flags
- Generates a PDF investment report
- Enforces a paywall after 2 free analyses per email (Stripe Payment Link)

## Quick start (local)
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy (Streamlit Community Cloud)
1) Push this repo to GitHub
2) Deploy on Streamlit Cloud (choose app.py)
3) Add Secrets:

### Payments
```text
STRIPE_PAYMENT_LINK_URL="https://buy.stripe.com/XXXX"
```

### Real Data (optional but recommended)
```text
ESTATED_TOKEN="YOUR_ESTATED_TOKEN"
ATTOM_APIKEY="YOUR_ATTOM_APIKEY"
```

### Optional demo admin unlock (recommended for pitches)
```text
ADMIN_UNLOCK_CODE="set-a-password"
```
Use it in-app under "Demo Admin" to unlock an email during a pitch.

## Notes
- Zillow is NOT scraped. The link is only used to guess an address.
- For fully automatic payment unlocking, add Stripe webhooks (next step).
