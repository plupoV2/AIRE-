import re
import time
import sqlite3
from dataclasses import dataclass
from urllib.parse import urlparse

import streamlit as st
import requests
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import LETTER
from reportlab.lib import colors

# ----------------------------
# AIRE™ — Institutional Underwriter (Demo-Ready)
# ----------------------------
APP_NAME = "AIRE™"
DB_PATH = "aire_app.db"
FREE_ANALYSES = 2  # free runs per email

DEFAULT_ACCENT = "#0B2E4A"   # deep navy
DEFAULT_ACCENT_2 = "#16A34A" # premium green
SOFT_BG = "#F6F8FB"
CARD_BG = "#FFFFFF"
MUTED = "#6B7280"

st.set_page_config(page_title=f"{APP_NAME} Property Grader", page_icon="🏠", layout="wide")

CSS = f"""<style>
  .main {{ background: {SOFT_BG}; }}
  .block-container {{ padding-top: 1.25rem; padding-bottom: 2.5rem; max-width: 1200px; }}
  .aire-header {{
    background: linear-gradient(90deg, {DEFAULT_ACCENT} 0%, #0F3D63 55%, #1C5D8B 100%);
    color: white;
    padding: 22px 22px;
    border-radius: 18px;
    box-shadow: 0 10px 25px rgba(0,0,0,.10);
  }}
  .aire-title {{ font-size: 28px; font-weight: 800; letter-spacing: .3px; margin: 0; }}
  .aire-sub {{ font-size: 14px; opacity: .92; margin-top: 6px; }}
  .aire-card {{
    background: {CARD_BG};
    border-radius: 18px;
    padding: 18px;
    box-shadow: 0 10px 20px rgba(0,0,0,.06);
    border: 1px solid rgba(15, 23, 42, .06);
  }}
  .aire-chip {{
    display:inline-block;
    padding: 6px 10px;
    border-radius: 999px;
    background: rgba(22,163,74,.10);
    color: {DEFAULT_ACCENT_2};
    font-weight: 700;
    font-size: 12px;
    margin-right: 8px;
  }}
  .aire-kpi {{
    background: {CARD_BG};
    border-radius: 18px;
    padding: 14px 14px;
    border: 1px solid rgba(15, 23, 42, .06);
  }}
  .stButton>button, .stDownloadButton>button {{
    border-radius: 12px;
    padding: 10px 14px;
    font-weight: 700;
  }}
</style>"""
st.markdown(CSS, unsafe_allow_html=True)

# ----------------------------
# Data Model
# ----------------------------
@dataclass
class PropertyData:
    address: str
    price: float
    monthly_rent: float
    monthly_expenses: float
    loan_payment: float
    vacancy_rate: float
    replacement_cost: float
    days_on_market: int
    job_diversity_index: float
    rent_regulation_risk: bool

# ----------------------------
# DB: usage + subscription flag (simple MVP)
# ----------------------------
def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            analyses_used INTEGER DEFAULT 0,
            paid INTEGER DEFAULT 0,
            updated_at INTEGER DEFAULT 0
        )
        """
    )
    conn.commit()
    return conn

def get_user(email: str):
    conn = _db()
    cur = conn.execute("SELECT email, analyses_used, paid FROM users WHERE email=?", (email,))
    row = cur.fetchone()
    if not row:
        conn.execute(
            "INSERT INTO users(email, analyses_used, paid, updated_at) VALUES(?,?,?,?)",
            (email, 0, 0, int(time.time())),
        )
        conn.commit()
        return {"email": email, "analyses_used": 0, "paid": 0}
    return {"email": row[0], "analyses_used": row[1], "paid": row[2]}

def inc_usage(email: str):
    conn = _db()
    conn.execute(
        "UPDATE users SET analyses_used = analyses_used + 1, updated_at=? WHERE email=?",
        (int(time.time()), email),
    )
    conn.commit()

def set_paid(email: str, paid: int = 1):
    conn = _db()
    conn.execute(
        "UPDATE users SET paid=?, updated_at=? WHERE email=?",
        (paid, int(time.time()), email),
    )
    conn.commit()

# ----------------------------
# URL → Address (NO scraping)
# ----------------------------
def extract_address_from_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        if not path:
            return None
        segments = [s for s in path.split("/") if any(ch.isdigit() for ch in s)]
        if not segments:
            return None
        candidate = max(segments, key=len)
        candidate = re.sub(r"_rb/?$", "", candidate)
        addr = candidate.replace("-", " ")
        addr = re.sub(r"\d{6,}$", "", addr).strip()
        return addr if len(addr) >= 8 else None
    except Exception:
        return None

# ----------------------------
# Real Data Connectors (LEGAL)
# ----------------------------
def fetch_estated(address: str):
    token = st.secrets.get("ESTATED_TOKEN", None)
    if not token:
        return None
    url = "https://apis.estated.com/v4/property"
    params = {"token": token, "combined_address": address}
    r = requests.get(url, params=params, timeout=20)
    if r.status_code != 200:
        return None
    return r.json()

def fetch_attom(address: str):
    apikey = st.secrets.get("ATTOM_APIKEY", None)
    if not apikey:
        return None
    url = "https://api.gateway.attomdata.com/propertyapi/v1.0.0/property/basicprofile"
    headers = {"accept": "application/json", "apikey": apikey}
    params = {"address": address}
    r = requests.get(url, headers=headers, params=params, timeout=20)
    if r.status_code != 200:
        return None
    return r.json()

def smart_prefill(address: str):
    suggested = {"price": None, "days_on_market": None, "replacement_cost": None}

    est = fetch_estated(address)
    if isinstance(est, dict):
        valuation = est.get("valuation", {}) or {}
        suggested["price"] = valuation.get("market_value") or valuation.get("value") or None

    att = fetch_attom(address)
    if isinstance(att, dict):
        try:
            prop = None
            if "property" in att and isinstance(att["property"], list) and att["property"]:
                prop = att["property"][0]
            if isinstance(prop, dict):
                sale = prop.get("sale", {}) or {}
                assessment = prop.get("assessment", {}) or {}
                suggested["price"] = suggested["price"] or sale.get("amount") or assessment.get("market", {}).get("mktTtlValue")
        except Exception:
            pass

    return suggested

# ----------------------------
# AIRE™ Underwriting (deterministic)
# ----------------------------
def get_weights(rate_env: str):
    if rate_env.upper() == "HIGH":
        return {"cashflow": 0.30, "downside": 0.25, "location": 0.15, "yield": 0.10, "liquidity": 0.10, "optionality": 0.05, "ai_risk": 0.05}
    return {"cashflow": 0.25, "downside": 0.20, "location": 0.15, "yield": 0.15, "liquidity": 0.10, "optionality": 0.10, "ai_risk": 0.05}

def kill_switch(p: PropertyData) -> bool:
    stressed_rent = p.monthly_rent * 0.80
    net = stressed_rent - p.monthly_expenses
    dscr = net / max(p.loan_payment, 1.0)
    if dscr < 1.0:
        return True
    if p.rent_regulation_risk:
        return True
    if p.days_on_market > 180:
        return True
    return False

def calculate_metrics(p: PropertyData):
    stressed_rent = p.monthly_rent * 0.80
    net = stressed_rent - p.monthly_expenses
    dscr = net / max(p.loan_payment, 1.0)

    cashflow = max(0.0, min(dscr / 1.50, 1.0))
    downside = max(0.0, min((p.replacement_cost / max(p.price, 1.0)) / 1.20, 1.0))
    location = max(0.0, min(p.job_diversity_index, 1.0))
    yld = (p.monthly_rent * 12) / max(p.price, 1.0)
    yield_quality = max(0.0, min(yld / 0.12, 1.0))
    liquidity = max(0.0, 1 - (p.days_on_market / 180))
    optionality = 0.60
    ai_risk = 1.0

    metrics = {"cashflow": cashflow, "downside": downside, "location": location, "yield": yield_quality, "liquidity": liquidity, "optionality": optionality, "ai_risk": ai_risk}
    return metrics, float(dscr)

def ai_flags(p: PropertyData):
    flags = []
    if (p.monthly_rent * 12) / max(p.price, 1.0) > 0.14:
        flags.append("Overstated yield vs price")
    if p.vacancy_rate < 0.05:
        flags.append("Vacancy assumption looks optimistic")
    if p.rent_regulation_risk:
        flags.append("Regulatory pressure risk")
    if p.monthly_expenses < (p.monthly_rent * 0.20):
        flags.append("Expenses might be understated")
    return flags

def ai_penalty(flags):
    base = 0.0
    for f in flags:
        if "Overstated" in f:
            base += 0.05
        elif "Vacancy" in f:
            base += 0.08
        elif "Regulatory" in f:
            base += 0.20
        elif "Expenses" in f:
            base += 0.06
    return min(base, 0.35)

def score(metrics, weights):
    return sum(metrics[k] * weights[k] for k in metrics) * 100

def grade(score_val: float, killed: bool):
    if killed:
        return "F", "PASS"
    if score_val >= 90:
        return "A", "STRONG BUY"
    if score_val >= 80:
        return "B", "BUY"
    if score_val >= 70:
        return "C", "WATCH"
    if score_val >= 60:
        return "D", "SPECULATIVE"
    return "F", "PASS"

def narrative_summary(p: PropertyData, score_val: float, g: str, verdict: str, flags: list, dscr: float):
    strengths = []
    risks = flags[:] if flags else []

    if dscr >= 1.25:
        strengths.append("Strong stress-tested cash flow (DSCR ≥ 1.25).")
    if p.replacement_cost >= p.price:
        strengths.append("Downside buffer: priced at/below replacement cost.")
    if p.days_on_market <= 45:
        strengths.append("Healthy liquidity profile (fast exit).")

    if not strengths:
        strengths.append("Neutral strength profile: upside depends on execution and pricing discipline.")

    if not risks:
        risks.append("No major risk flags detected; verify rents/expenses with comps.")

    return strengths[:3], risks[:3]

# ----------------------------
# PDF Report
# ----------------------------
def build_pdf(path: str, p: PropertyData, result: dict, strengths: list, risks: list, dscr: float):
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(path, pagesize=LETTER)
    story = []

    story.append(Paragraph(f"{APP_NAME} Investment Report", styles["Title"]))
    story.append(Spacer(1, 10))
    story.append(Paragraph(f"<b>Address:</b> {p.address}", styles["Normal"]))
    story.append(Paragraph(f"<b>Grade:</b> {result['grade']} &nbsp;&nbsp; <b>Score:</b> {result['score']:.1f} &nbsp;&nbsp; <b>Verdict:</b> {result['verdict']}", styles["Normal"]))
    story.append(Paragraph(f"<b>Kill Switch:</b> {result['kill_switch']} &nbsp;&nbsp; <b>Stress DSCR:</b> {dscr:.2f} (rent -20%)", styles["Normal"]))
    story.append(Spacer(1, 12))

    data = [
        ["Metric", "Value"],
        ["Price", f"${p.price:,.0f}"],
        ["Monthly Rent", f"${p.monthly_rent:,.0f}"],
        ["Monthly Expenses", f"${p.monthly_expenses:,.0f}"],
        ["Loan Payment", f"${p.loan_payment:,.0f}"],
        ["Vacancy Rate", f"{p.vacancy_rate:.0%}"],
        ["Replacement Cost", f"${p.replacement_cost:,.0f}"],
        ["Days on Market", str(p.days_on_market)],
        ["Job Diversity Index", f"{p.job_diversity_index:.2f}"],
    ]
    table = Table(data, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("PADDING", (0,0), (-1,-1), 6),
    ]))
    story.append(table)
    story.append(Spacer(1, 12))

    story.append(Paragraph("Top Strengths", styles["Heading2"]))
    for s in strengths:
        story.append(Paragraph(f"• {s}", styles["Normal"]))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Top Risks / Flags", styles["Heading2"]))
    for r in risks:
        story.append(Paragraph(f"• {r}", styles["Normal"]))

    doc.build(story)

# ----------------------------
# Payments (Stripe Payment Link)
# ----------------------------
def render_paywall():
    st.warning("You’ve used your free analyses. Subscribe to unlock unlimited reports.")
    pay_link = st.secrets.get("STRIPE_PAYMENT_LINK_URL", "")
    if pay_link:
        st.link_button("Subscribe (Stripe)", pay_link)
    else:
        st.info("Add STRIPE_PAYMENT_LINK_URL in Streamlit secrets to enable payments.")
    st.caption("Tip: Payment Link is the fastest investor-demo paywall.")

def demo_unlock_controls(email: str):
    unlock_code = st.secrets.get("ADMIN_UNLOCK_CODE", "")
    with st.expander("Demo Admin (optional)", expanded=False):
        st.caption("For demos only: unlock an email without building Stripe webhooks yet.")
        code = st.text_input("Admin unlock code", type="password")
        if st.button("Unlock this email") and unlock_code and code == unlock_code:
            set_paid(email, 1)
            st.success("Unlocked. Run again.")
        if not unlock_code:
            st.caption("Set ADMIN_UNLOCK_CODE in secrets to enable this.")

# ----------------------------
# Header
# ----------------------------
st.markdown(
    f"""
    <div class="aire-header">
      <div class="aire-title">{APP_NAME} — Property Underwriter</div>
      <div class="aire-sub">Paste a listing link → auto-fill real data → A–F grade + investment memo (PDF).</div>
    </div>
    """,
    unsafe_allow_html=True,
)
st.write("")

# Sidebar controls
with st.sidebar:
    st.markdown(f"### {APP_NAME} Controls")
    st.caption("Keep it simple during the pitch.")
    rate_env = st.selectbox("Rate environment", ["HIGH", "NORMAL"], index=0)
    st.write("")
    st.markdown("**Real Data Keys**")
    st.write(f"- Estated: {'✅' if bool(st.secrets.get('ESTATED_TOKEN','')) else '❌'}")
    st.write(f"- ATTOM: {'✅' if bool(st.secrets.get('ATTOM_APIKEY','')) else '❌'}")
    st.write("")
    st.markdown("**Payments**")
    st.write(f"- Stripe link: {'✅' if bool(st.secrets.get('STRIPE_PAYMENT_LINK_URL','')) else '❌'}")
    st.caption("No Zillow scraping. Address only.")

# Main: Step 1
colL, colR = st.columns([2, 1], gap="large")

with colL:
    st.markdown('<div class="aire-card">', unsafe_allow_html=True)
    st.markdown("### 1) Paste Listing Link")
    email = st.text_input("Email (usage + subscription)", placeholder="you@example.com")
    if not email:
        st.info("Enter your email to start.")
        st.markdown("</div>", unsafe_allow_html=True)
        st.stop()

    user = get_user(email)
    analyses_left = max(FREE_ANALYSES - user["analyses_used"], 0)

    zlink = st.text_input("Listing URL", placeholder="https://www.zillow.com/...")
    auto_addr = extract_address_from_url(zlink) if zlink else None
    address = st.text_input("Confirm address", value=(auto_addr or ""), placeholder="123 Main St, City, ST 12345")

    c1, c2, c3 = st.columns(3)
    c1.markdown(f'<div class="aire-kpi"><b>Free left</b><br>{analyses_left}</div>', unsafe_allow_html=True)
    c2.markdown(f'<div class="aire-kpi"><b>Subscribed</b><br>{"Yes" if user["paid"] else "No"}</div>', unsafe_allow_html=True)
    c3.markdown(f'<div class="aire-kpi"><b>Rate env</b><br>{rate_env}</div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

with colR:
    st.markdown('<div class="aire-card">', unsafe_allow_html=True)
    st.markdown("### Investor Demo Script")
    st.markdown('<span class="aire-chip">Clean</span><span class="aire-chip">Fast</span><span class="aire-chip">Credible</span>', unsafe_allow_html=True)
    st.write("1) Paste link")
    st.write("2) Auto-fill (real data)")
    st.write("3) Analyze")
    st.write("4) Download PDF")
    st.write("5) Show Subscribe button")
    st.markdown("</div>", unsafe_allow_html=True)

st.write("")

# Paywall
if (not user["paid"]) and (user["analyses_used"] >= FREE_ANALYSES):
    render_paywall()
    demo_unlock_controls(email)
    st.stop()

# Step 2 Inputs
st.markdown('<div class="aire-card">', unsafe_allow_html=True)
st.markdown("### 2) Deal Inputs (Auto-fill + Quick Adjust)")

prefill = st.session_state.get("prefill", {})

b1, b2, b3 = st.columns([1, 1, 2])
with b1:
    autofill = st.button("✨ Auto-fill (real data)")
with b2:
    demo_fill = st.button("⚡ Load demo deal")
with b3:
    st.caption("Auto-fill uses Estated/ATTOM if configured; otherwise demo with manual inputs.")

if autofill and address.strip():
    st.session_state["prefill"] = smart_prefill(address.strip())
    prefill = st.session_state["prefill"]

if demo_fill:
    st.session_state["prefill"] = {"price": 485000, "days_on_market": 28, "replacement_cost": 525000}
    prefill = st.session_state["prefill"]

def val(key, default):
    v = prefill.get(key)
    return default if v is None else v

a, b, c = st.columns(3)
price = a.number_input("Purchase Price ($)", min_value=0.0, value=float(val("price", 400000.0)), step=1000.0)
monthly_rent = b.number_input("Monthly Rent ($)", min_value=0.0, value=3000.0, step=50.0)
loan_payment = c.number_input("Monthly Loan Payment ($)", min_value=0.0, value=1850.0, step=50.0)

d, e, f = st.columns(3)
monthly_expenses = d.number_input("Monthly Expenses ($)", min_value=0.0, value=1100.0, step=50.0)
vacancy_rate = e.slider("Vacancy Rate", min_value=0.0, max_value=0.25, value=0.08, step=0.01)
days_on_market = f.number_input("Days on Market", min_value=0, value=int(val("days_on_market", 45)), step=1)

g1, g2, g3 = st.columns(3)
replacement_cost = g1.number_input("Replacement Cost ($)", min_value=0.0, value=float(val("replacement_cost", 450000.0)), step=1000.0)
job_div = g2.slider("Job Diversity Index (0–1)", min_value=0.0, max_value=1.0, value=0.74, step=0.01)
reg_risk = g3.checkbox("Rent regulation risk", value=False)

st.markdown("</div>", unsafe_allow_html=True)
st.write("")

# Step 3 Analyze
st.markdown('<div class="aire-card">', unsafe_allow_html=True)
st.markdown("### 3) Analyze → Grade → Download Report")

if st.button("✅ Analyze & Generate PDF", type="primary"):
    p = PropertyData(
        address=address.strip() or "Unknown address",
        price=price,
        monthly_rent=monthly_rent,
        monthly_expenses=monthly_expenses,
        loan_payment=loan_payment,
        vacancy_rate=vacancy_rate,
        replacement_cost=replacement_cost,
        days_on_market=int(days_on_market),
        job_diversity_index=job_div,
        rent_regulation_risk=reg_risk,
    )

    killed = kill_switch(p)
    metrics, dscr = calculate_metrics(p)
    weights = get_weights(rate_env)
    flags = ai_flags(p)
    penalty = ai_penalty(flags)

    base_score = score(metrics, weights)
    final_score = max(base_score * (1 - penalty), 0)

    g, verdict = grade(final_score, killed)
    strengths, risks = narrative_summary(p, final_score, g, verdict, flags, dscr)

    inc_usage(email)

    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Grade", g)
    r2.metric("Score", f"{final_score:.1f}")
    r3.metric("Verdict", verdict)
    r4.metric("Stress DSCR", f"{dscr:.2f}")

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("**Top Strengths**")
        for s in strengths:
            st.write(f"• {s}")
    with right:
        st.markdown("**Top Risks / Flags**")
        for r in risks:
            st.write(f"• {r}")

    pdf_name = f"AIRE_Report_{int(time.time())}.pdf"
    build_pdf(pdf_name, p, {"score": final_score, "grade": g, "verdict": verdict, "kill_switch": killed}, strengths, risks, dscr)

    with open(pdf_name, "rb") as f:
        st.download_button("⬇️ Download PDF Investment Report", f, file_name=pdf_name, mime="application/pdf")

    with st.expander("Underwriting details (for firms/investors)", expanded=False):
        st.write("Weights:", weights)
        st.write("Metrics (0–1):", metrics)
        st.write("AI penalty:", round(penalty, 3))

st.markdown("</div>", unsafe_allow_html=True)
st.write("")
st.caption("AIRE™ is deterministic underwriting. AI flags only reduce score; they never inflate it. Zillow is never scraped.")
