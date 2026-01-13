# AIRE™ Enhanced App (More than a demo)

This version upgrades the investor demo into an actual app:
- Multi-page navigation (Analyze / History / Account / About)
- Credit-based metering (free credits) + Pro (effectively unlimited)
- Saved analysis history (SQLite) with key metrics
- Cleaner underwriting outputs (NOI, cap rate, cash-on-cash, stress DSCR)
- Auto-fill via legal APIs (Estated/ATTOM) with data-quality notes
- PDF report generation
- Stripe Payment Link button for upgrades
- Cached API calls for speed

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy (Streamlit Community Cloud)
1) Push this repo to GitHub
2) Deploy `app.py`
3) Add Secrets in Streamlit Cloud:

```text
STRIPE_PAYMENT_LINK_URL="https://buy.stripe.com/XXXX"
ESTATED_TOKEN="YOUR_ESTATED_TOKEN"
ATTOM_APIKEY="YOUR_ATTOM_APIKEY"
ADMIN_UNLOCK_CODE="set-a-password"   # optional, for manual unlock testing
```

Notes:
- Zillow is NOT scraped. Listing URL is only used to guess address in the URL path.
- For fully automatic subscription unlock, add Stripe webhooks (next step).
