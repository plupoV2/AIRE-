import re
import time
import sqlite3
from dataclasses import dataclass, asdict
from urllib.parse import urlparse
from typing import Optional, Dict, Any, Tuple, List

import streamlit as st
import requests
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import LETTER
from reportlab.lib import colors

# =====================================
# AIRE™ — Production-ish Streamlit App
# =====================================
APP_NAME = "AIRE™"
APP_TAGLINE = "Institutional underwriting, simplified."
DB_PATH = "aire_app.db"

# Plans / metering
FREE_CREDITS = 2        # free analyses per email
PRO_CREDITS = 5000      # effectively "unlimited" for MVP
CREDIT_COST_PER_ANALYSIS = 1

# UI Theme vars (kept for CSS only; Streamlit theme set in config.toml)
SOFT_BG = "#F6F8FB"
CARD_BG = "#FFFFFF"
MUTED = "#6B7280"
ACCENT = "#0B2E4A"
SUCCESS = "#16A34A"
WARN = "#B45309"
DANGER = "#B91C1C"

st.set_page_config(page_title=f"{APP_NAME} | Property Grader", page_icon="🏠", layout="wide")

CSS = f"""
<style>
  .main {{ background: {SOFT_BG}; }}
  .block-container {{ padding-top: 1.25rem; padding-bottom: 2.5rem; max-width: 1200px; }}
  .aire-hero {{
    background: linear-gradient(90deg, {ACCENT} 0%, #0F3D63 55%, #1C5D8B 100%);
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
  .aire-kpi {{
    background: {CARD_BG};
    border-radius: 18px;
    padding: 14px 14px;
    border: 1px solid rgba(15, 23, 42, .06);
  }}
  .aire-muted {{ color: {MUTED}; }}
  .stButton>button, .stDownloadButton>button {{
    border-radius: 12px;
    padding: 10px 14px;
    font-weight: 700;
  }}
  .aire-pill {{
    display:inline-block;
    padding: 6px 10px;
    border-radius: 999px;
    background: rgba(22,163,74,.10);
    color: {SUCCESS};
    font-weight: 800;
    font-size: 12px;
    margin-right: 8px;
  }}
  .aire-pill-warn {{
    display:inline-block;
    padding: 6px 10px;
    border-radius: 999px;
    background: rgba(180,83,9,.12);
    color: {WARN};
    font-weight: 800;
    font-size: 12px;
    margin-right: 8px;
  }}
  .aire-pill-danger {{
    display:inline-block;
    padding: 6px 10px;
    border-radius: 999px;
    background: rgba(185,28,28,.10);
    color: {DANGER};
    font-weight: 800;
    font-size: 12px;
    margin-right: 8px;
  }}
  .aire-disclaimer {{
    font-size: 12px;
    color: {MUTED};
  }}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

# ----------------------------
# Data model
# ----------------------------
@dataclass
class PropertyData:
    address: str
    price: float
    down_payment_pct: float
    interest_rate_pct: float
    term_years: int
    monthly_rent: float
    monthly_expenses: float
    vacancy_rate: float
    replacement_cost: float
    days_on_market: int
    job_diversity_index: float
    rent_regulation_risk: bool

# ----------------------------
# Database
# ----------------------------
def _db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            credits INTEGER DEFAULT 0,
            paid INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT 0,
            updated_at INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            address TEXT,
            listing_url TEXT,
            grade TEXT,
            verdict TEXT,
            score REAL,
            dscr REAL,
            noi REAL,
            cap_rate REAL,
            coc_return REAL,
            json_payload TEXT
        )
    """)
    conn.commit()
    return conn

def _now() -> int:
    return int(time.time())

def get_user(email: str) -> Dict[str, Any]:
    conn = _db()
    cur = conn.execute("SELECT email, credits, paid FROM users WHERE email=?", (email,))
    row = cur.fetchone()
    now = _now()
    if not row:
        conn.execute(
            "INSERT INTO users(email, credits, paid, created_at, updated_at) VALUES(?,?,?,?,?)",
            (email, FREE_CREDITS, 0, now, now),
        )
        conn.commit()
        return {"email": email, "credits": FREE_CREDITS, "paid": 0}
    return {"email": row[0], "credits": int(row[1]), "paid": int(row[2])}

def set_paid(email: str, paid: int = 1):
    conn = _db()
    credits = PRO_CREDITS if paid else FREE_CREDITS
    conn.execute("UPDATE users SET paid=?, credits=?, updated_at=? WHERE email=?", (paid, credits, _now(), email))
    conn.commit()

def spend_credit(email: str, amount: int = CREDIT_COST_PER_ANALYSIS) -> bool:
    conn = _db()
    cur = conn.execute("SELECT credits, paid FROM users WHERE email=?", (email,))
    row = cur.fetchone()
    if not row:
        return False
    credits, paid = int(row[0]), int(row[1])
    if paid:
        return True
    if credits < amount:
        return False
    conn.execute("UPDATE users SET credits = credits - ?, updated_at=? WHERE email=?", (amount, _now(), email))
    conn.commit()
    return True

def json_dumps(obj: Any) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)

def save_analysis(email: str, address: str, listing_url: str, result: Dict[str, Any], payload: Dict[str, Any]):
    conn = _db()
    conn.execute(
        """INSERT INTO analyses(email, created_at, address, listing_url, grade, verdict, score, dscr, noi, cap_rate, coc_return, json_payload)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            email,
            _now(),
            address,
            listing_url,
            result.get("grade"),
            result.get("verdict"),
            float(result.get("score", 0)),
            float(result.get("dscr", 0)),
            float(result.get("noi", 0)),
            float(result.get("cap_rate", 0)),
            float(result.get("coc_return", 0)),
            json_dumps(payload),
        ),
    )
    conn.commit()

def fetch_analyses(email: str, limit: int = 50) -> List[Dict[str, Any]]:
    conn = _db()
    cur = conn.execute(
        "SELECT created_at, address, listing_url, grade, verdict, score, dscr, noi, cap_rate, coc_return FROM analyses WHERE email=? ORDER BY created_at DESC LIMIT ?",
        (email, limit),
    )
    rows = cur.fetchall()
    out = []
    for r in rows:
        out.append({
            "created_at": int(r[0]), "address": r[1], "listing_url": r[2],
            "grade": r[3], "verdict": r[4], "score": r[5],
            "dscr": r[6], "noi": r[7], "cap_rate": r[8], "coc_return": r[9],
        })
    return out

# ----------------------------
# Formatting helpers
# ----------------------------
def fmt_money(x: float) -> str:
    try:
        return f"${x:,.0f}"
    except Exception:
        return str(x)

def ts_to_str(ts: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))

# ----------------------------
# URL → Address (NO scraping)
# ----------------------------
def extract_address_from_url(url: str) -> Optional[str]:
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
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_estated(address: str) -> Optional[Dict[str, Any]]:
    token = st.secrets.get("ESTATED_TOKEN", None)
    if not token:
        return None
    url = "https://apis.estated.com/v4/property"
    params = {"token": token, "combined_address": address}
    r = requests.get(url, params=params, timeout=20)
    if r.status_code != 200:
        return None
    return r.json()

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_attom(address: str) -> Optional[Dict[str, Any]]:
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

def smart_prefill(address: str) -> Tuple[Dict[str, Any], List[str]]:
    suggested = {"price": None, "replacement_cost": None, "days_on_market": None}
    notes = []

    est = fetch_estated(address)
    if isinstance(est, dict):
        valuation = est.get("valuation", {}) or {}
        price = valuation.get("market_value") or valuation.get("value")
        if price:
            suggested["price"] = float(price)
            notes.append("Pulled estimated value from Estated.")
        else:
            notes.append("Estated available, but no valuation field found.")

    att = fetch_attom(address)
    if isinstance(att, dict):
        try:
            prop = None
            if "property" in att and isinstance(att["property"], list) and att["property"]:
                prop = att["property"][0]
            if isinstance(prop, dict):
                sale = prop.get("sale", {}) or {}
                assessment = prop.get("assessment", {}) or {}
                p2 = sale.get("amount") or assessment.get("market", {}).get("mktTtlValue")
                if p2 and not suggested["price"]:
                    suggested["price"] = float(p2)
                    notes.append("Pulled price/value from ATTOM.")
        except Exception:
            notes.append("ATTOM available, but response shape differed.")

    if not notes:
        notes.append("No API keys set — manual mode.")
    return suggested, notes

# ----------------------------
# Finance + Underwriting
# ----------------------------
def monthly_payment(principal: float, annual_rate_pct: float, term_years: int) -> float:
    r = (annual_rate_pct / 100) / 12.0
    n = term_years * 12
    if r <= 0:
        return principal / max(n, 1)
    return principal * (r * (1 + r) ** n) / ((1 + r) ** n - 1)

def get_weights(rate_env: str):
    if rate_env.upper() == "HIGH":
        return {"cashflow": 0.32, "downside": 0.25, "location": 0.12, "yield": 0.10, "liquidity": 0.10, "optionality": 0.06, "ai_risk": 0.05}
    return {"cashflow": 0.28, "downside": 0.20, "location": 0.12, "yield": 0.15, "liquidity": 0.10, "optionality": 0.10, "ai_risk": 0.05}

def kill_switch(dscr_stress: float, rent_reg_risk: bool, dom: int) -> bool:
    return (dscr_stress < 1.0) or rent_reg_risk or (dom > 180)

def compute_core_numbers(p: PropertyData) -> Dict[str, float]:
    loan_amount = p.price * (1 - p.down_payment_pct / 100)
    pay = monthly_payment(loan_amount, p.interest_rate_pct, p.term_years)

    eff_rent = p.monthly_rent * (1 - p.vacancy_rate)
    noi_month = eff_rent - p.monthly_expenses
    noi_year = noi_month * 12

    cap_rate = noi_year / max(p.price, 1.0)

    cash_flow_month = noi_month - pay
    cash_flow_year = cash_flow_month * 12
    cash_invested = p.price * (p.down_payment_pct / 100)
    coc = cash_flow_year / max(cash_invested, 1.0)

    stressed_rent = p.monthly_rent * 0.80 * (1 - p.vacancy_rate)
    stressed_noi_m = stressed_rent - p.monthly_expenses
    dscr = stressed_noi_m / max(pay, 1.0)

    return {"loan_payment": pay, "noi_year": noi_year, "cap_rate": cap_rate, "coc_return": coc, "dscr_stress": dscr, "cash_flow_month": cash_flow_month}

def calculate_metrics(p: PropertyData, nums: Dict[str, float]) -> Dict[str, float]:
    cashflow = max(0.0, min(nums["dscr_stress"] / 1.50, 1.0))
    downside = max(0.0, min((p.replacement_cost / max(p.price, 1.0)) / 1.20, 1.0))
    location = max(0.0, min(p.job_diversity_index, 1.0))
    yield_quality = max(0.0, min(nums["cap_rate"] / 0.10, 1.0))
    liquidity = max(0.0, 1 - (p.days_on_market / 180))
    return {"cashflow": cashflow, "downside": downside, "location": location, "yield": yield_quality, "liquidity": liquidity, "optionality": 0.60, "ai_risk": 1.0}

def ai_flags(p: PropertyData, nums: Dict[str, float]) -> List[str]:
    flags = []
    gross_yield = (p.monthly_rent * 12) / max(p.price, 1.0)
    if gross_yield > 0.14:
        flags.append("Rent-to-price looks aggressive (verify comps).")
    if p.vacancy_rate < 0.05:
        flags.append("Vacancy assumption looks optimistic.")
    if p.monthly_expenses < (p.monthly_rent * 0.20):
        flags.append("Expenses might be understated.")
    if nums["cap_rate"] < 0.045:
        flags.append("Low cap rate; deal relies on appreciation/execution.")
    if p.rent_regulation_risk:
        flags.append("Regulatory pressure risk.")
    return flags

def ai_penalty(flags: List[str]) -> float:
    base = 0.0
    for f in flags:
        if "aggressive" in f:
            base += 0.06
        elif "Vacancy" in f:
            base += 0.08
        elif "Expenses" in f:
            base += 0.06
        elif "Low cap" in f:
            base += 0.06
        elif "Regulatory" in f:
            base += 0.20
    return min(base, 0.35)

def score(metrics: Dict[str, float], weights: Dict[str, float]) -> float:
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

def narrative_summary(p: PropertyData, nums: Dict[str, float], flags: List[str]):
    strengths = []
    risks = flags[:] if flags else []
    if nums["dscr_stress"] >= 1.25:
        strengths.append("Strong stress-tested coverage (DSCR ≥ 1.25).")
    if nums["cap_rate"] >= 0.07:
        strengths.append("Healthy cap rate relative to price and expenses.")
    if p.replacement_cost >= p.price:
        strengths.append("Downside buffer: at/below replacement cost.")
    if p.days_on_market <= 45:
        strengths.append("Liquidity profile looks solid (faster exit).")
    if not strengths:
        strengths.append("Neutral strength profile; upside depends on execution and pricing discipline.")
    if not risks:
        risks.append("No major risk flags detected; validate rent comps and true expenses.")
    return strengths[:3], risks[:4]

def build_pdf(path: str, p: PropertyData, nums: Dict[str, float], result: Dict[str, Any], strengths: List[str], risks: List[str], data_notes: List[str]):
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(path, pagesize=LETTER)
    story = []
    story.append(Paragraph(f"{APP_NAME} — Investment Report", styles["Title"]))
    story.append(Spacer(1, 8))
    story.append(Paragraph(f"<b>Address:</b> {p.address}", styles["Normal"]))
    story.append(Paragraph(f"<b>Grade:</b> {result['grade']} &nbsp;&nbsp; <b>Score:</b> {result['score']:.1f} &nbsp;&nbsp; <b>Verdict:</b> {result['verdict']}", styles["Normal"]))
    story.append(Paragraph(f"<b>Stress DSCR:</b> {nums['dscr_stress']:.2f} (rent -20%) &nbsp;&nbsp; <b>Cap Rate:</b> {nums['cap_rate']*100:.2f}% &nbsp;&nbsp; <b>CoC:</b> {nums['coc_return']*100:.2f}%", styles["Normal"]))
    story.append(Spacer(1, 10))
    data = [
        ["Metric", "Value"],
        ["Price", f"${p.price:,.0f}"],
        ["Down Payment", f"{p.down_payment_pct:.1f}%"],
        ["Interest Rate", f"{p.interest_rate_pct:.2f}%"],
        ["Term", f"{p.term_years} years"],
        ["Monthly Rent", f"${p.monthly_rent:,.0f}"],
        ["Monthly Expenses", f"${p.monthly_expenses:,.0f}"],
        ["Vacancy Rate", f"{p.vacancy_rate*100:.1f}%"],
        ["Loan Payment (est.)", f"${nums['loan_payment']:,.0f}"],
        ["NOI (annual)", f"${nums['noi_year']:,.0f}"],
        ["Days on Market", str(p.days_on_market)],
    ]
    table = Table(data, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("PADDING", (0,0), (-1,-1), 6),
    ]))
    story.append(table)
    story.append(Spacer(1, 10))
    story.append(Paragraph("Top Strengths", styles["Heading2"]))
    for s in strengths:
        story.append(Paragraph(f"• {s}", styles["Normal"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Top Risks / Flags", styles["Heading2"]))
    for r in risks:
        story.append(Paragraph(f"• {r}", styles["Normal"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Data Notes", styles["Heading2"]))
    for n in data_notes:
        story.append(Paragraph(f"• {n}", styles["Normal"]))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Disclaimer: This report is for informational purposes and is not financial advice. Verify all inputs and assumptions.", styles["Normal"]))
    doc.build(story)

def render_paywall():
    st.markdown('<div class="aire-card">', unsafe_allow_html=True)
    st.markdown("### Upgrade to Pro")
    st.write("You’ve used your free credits. Upgrade to keep running unlimited analyses and generating reports.")
    pay_link = st.secrets.get("STRIPE_PAYMENT_LINK_URL", "")
    if pay_link:
        st.link_button("Subscribe (Stripe)", pay_link)
    else:
        st.info("Add STRIPE_PAYMENT_LINK_URL in Streamlit secrets to enable payments.")
    st.caption("Automatic unlock via Stripe webhooks is the next upgrade.")
    st.markdown("</div>", unsafe_allow_html=True)

def demo_admin_unlock(email: str):
    unlock_code = st.secrets.get("ADMIN_UNLOCK_CODE", "")
    with st.expander("Admin (demo only)", expanded=False):
        st.caption("Unlock a user during testing if you haven’t added webhooks yet.")
        code = st.text_input("Admin unlock code", type="password", key="admin_code")
        if st.button("Unlock this account"):
            if unlock_code and code == unlock_code:
                set_paid(email, 1)
                st.success("Unlocked. Refresh the page.")
            else:
                st.error("Invalid unlock code.")

# ----------------------------
# Header + sidebar
# ----------------------------
st.markdown(
    f"""
    <div class="aire-hero">
      <div class="aire-title">{APP_NAME}</div>
      <div class="aire-sub">{APP_TAGLINE}</div>
    </div>
    """,
    unsafe_allow_html=True,
)
st.write("")

with st.sidebar:
    st.markdown(f"## {APP_NAME}")
    st.caption("Real underwriting • Clean workflow • Saved history")
    page = st.radio("Navigate", ["Analyze", "History", "Account", "About"], index=0)
    st.divider()
    st.caption("Status")
    st.write(f"Estated: {'✅' if bool(st.secrets.get('ESTATED_TOKEN','')) else '❌'}")
    st.write(f"ATTOM: {'✅' if bool(st.secrets.get('ATTOM_APIKEY','')) else '❌'}")
    st.write(f"Stripe: {'✅' if bool(st.secrets.get('STRIPE_PAYMENT_LINK_URL','')) else '❌'}")

# ----------------------------
# Account identity (simple email)
# ----------------------------
st.session_state.setdefault("email", "")

# Top bar for identity
c1, c2 = st.columns([2, 1])
with c1:
    email_in = st.text_input("Email", value=st.session_state["email"], placeholder="you@example.com")
    if email_in:
        st.session_state["email"] = email_in
with c2:
    rate_env = st.selectbox("Rate environment", ["HIGH", "NORMAL"], index=0)

if not st.session_state["email"]:
    st.info("Enter your email to continue.")
    st.stop()

user = get_user(st.session_state["email"])
st.write("")

# ============================
# PAGES
# ============================
if page == "Analyze":
    if (not user["paid"]) and (user["credits"] < CREDIT_COST_PER_ANALYSIS):
        render_paywall()
        demo_admin_unlock(st.session_state["email"])
        st.stop()

    st.markdown('<div class="aire-card">', unsafe_allow_html=True)
    st.markdown("### Analyze a property")
    st.caption("Paste a listing link (optional), confirm address, and run underwriting. Your results are saved in History.")

    colA, colB = st.columns([2, 1], gap="large")

    with colA:
        listing_url = st.text_input("Listing URL (optional)", placeholder="https://www.zillow.com/...")
        auto_addr = extract_address_from_url(listing_url) if listing_url else None
        address = st.text_input("Property address", value=(auto_addr or ""), placeholder="123 Main St, City, ST 12345")

        b1, b2 = st.columns([1, 2])
        with b1:
            do_autofill = st.button("✨ Auto-fill")
        with b2:
            st.caption("Auto-fill uses Estated/ATTOM if configured. Otherwise, you’ll enter values manually.")

        data_notes = []
        prefill = st.session_state.get("prefill", {})
        if do_autofill and address.strip():
            with st.spinner("Fetching property data..."):
                prefill, data_notes = smart_prefill(address.strip())
            st.session_state["prefill"] = prefill
            st.session_state["data_notes"] = data_notes
        else:
            data_notes = st.session_state.get("data_notes", ["Manual mode."])
            prefill = st.session_state.get("prefill", {})

    with colB:
        st.markdown("**Plan**")
        if user["paid"]:
            st.markdown('<span class="aire-pill">PRO</span>', unsafe_allow_html=True)
            st.write("Unlimited analyses")
        else:
            st.markdown('<span class="aire-pill-warn">FREE</span>', unsafe_allow_html=True)
            st.write(f"Credits remaining: **{user['credits']}**")
        st.markdown("**Outputs**")
        st.write("• Grade & Verdict")
        st.write("• NOI, Cap Rate, CoC")
        st.write("• Stress DSCR")
        st.write("• PDF report")
        st.write("• Saved history")

    st.markdown("</div>", unsafe_allow_html=True)
    st.write("")

    st.markdown('<div class="aire-card">', unsafe_allow_html=True)
    st.markdown("### Inputs")

    def v(key, default):
        val = prefill.get(key)
        return default if val is None else val

    c1, c2, c3, c4 = st.columns(4)
    price = c1.number_input("Price ($)", min_value=0.0, value=float(v("price", 400000.0)), step=1000.0)
    down_payment_pct = c2.number_input("Down payment (%)", min_value=0.0, max_value=100.0, value=20.0, step=1.0)
    interest_rate_pct = c3.number_input("Interest rate (%)", min_value=0.0, max_value=30.0, value=7.25, step=0.05)
    term_years = c4.number_input("Term (years)", min_value=1, max_value=40, value=30, step=1)

    d1, d2, d3, d4 = st.columns(4)
    monthly_rent = d1.number_input("Monthly rent ($)", min_value=0.0, value=3000.0, step=50.0)
    monthly_expenses = d2.number_input("Monthly expenses ($)", min_value=0.0, value=1100.0, step=50.0)
    vacancy_rate = d3.slider("Vacancy rate", min_value=0.0, max_value=0.25, value=0.08, step=0.01)
    days_on_market = d4.number_input("Days on market", min_value=0, value=int(v("days_on_market", 45)), step=1)

    e1, e2, e3, e4 = st.columns(4)
    replacement_cost = e1.number_input("Replacement cost ($)", min_value=0.0, value=float(v("replacement_cost", 450000.0)), step=1000.0)
    job_div = e2.slider("Job diversity (0–1)", min_value=0.0, max_value=1.0, value=0.74, step=0.01)
    reg_risk = e3.checkbox("Rent regulation risk", value=False)
    stress_rent_cut = e4.slider("Extra rent stress", min_value=0.0, max_value=0.30, value=0.00, step=0.01,
                                help="Optional: extra rent cut beyond baseline -20% for your own stress testing.")

    st.markdown("</div>", unsafe_allow_html=True)
    st.write("")

    st.markdown('<div class="aire-card">', unsafe_allow_html=True)
    st.markdown("### Results")

    if st.button("✅ Run underwriting", type="primary"):
        if not spend_credit(st.session_state["email"], CREDIT_COST_PER_ANALYSIS):
            st.error("No credits remaining.")
            st.markdown("</div>", unsafe_allow_html=True)
            render_paywall()
            st.stop()

        p = PropertyData(
            address=address.strip() or "Unknown address",
            price=price,
            down_payment_pct=down_payment_pct,
            interest_rate_pct=interest_rate_pct,
            term_years=int(term_years),
            monthly_rent=monthly_rent,
            monthly_expenses=monthly_expenses,
            vacancy_rate=vacancy_rate,
            replacement_cost=replacement_cost,
            days_on_market=int(days_on_market),
            job_diversity_index=job_div,
            rent_regulation_risk=reg_risk,
        )

        nums = compute_core_numbers(p)
        dscr_display = nums["dscr_stress"] * (1 - stress_rent_cut)

        weights = get_weights(rate_env)
        metrics = calculate_metrics(p, nums)
        flags = ai_flags(p, nums)
        penalty = ai_penalty(flags)

        killed = kill_switch(nums["dscr_stress"], p.rent_regulation_risk, p.days_on_market)
        base_score = score(metrics, weights)
        final_score = max(base_score * (1 - penalty), 0)
        g, verdict = grade(final_score, killed)

        strengths, risks = narrative_summary(p, nums, flags)

        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("Grade", g)
        k2.metric("Score", f"{final_score:.1f}")
        k3.metric("Verdict", verdict)
        k4.metric("Stress DSCR", f"{dscr_display:.2f}")
        k5.metric("Cap Rate", f"{nums['cap_rate']*100:.2f}%")

        s1, s2 = st.columns(2, gap="large")
        with s1:
            st.markdown("**Strengths**")
            for s in strengths:
                st.write(f"• {s}")
            st.write("")
            st.markdown("**Key Numbers**")
            st.write(f"NOI (annual): **{fmt_money(nums['noi_year'])}**")
            st.write(f"Loan payment (est.): **{fmt_money(nums['loan_payment'])}/mo**")
            st.write(f"Cash flow (est.): **{fmt_money(nums['cash_flow_month'])}/mo**")
            st.write(f"Cash-on-cash: **{nums['coc_return']*100:.2f}%**")
        with s2:
            st.markdown("**Risks / Flags**")
            for r in risks:
                st.write(f"• {r}")
            st.write("")
            st.markdown("**Data Notes**")
            for n in data_notes:
                st.write(f"• {n}")

        result = {
            "grade": g,
            "verdict": verdict,
            "score": float(final_score),
            "dscr": float(nums["dscr_stress"]),
            "noi": float(nums["noi_year"]),
            "cap_rate": float(nums["cap_rate"]),
            "coc_return": float(nums["coc_return"]),
            "kill_switch": bool(killed),
            "ai_penalty": float(penalty),
            "rate_env": rate_env,
        }

        payload = {
            "property": asdict(p),
            "numbers": nums,
            "metrics": metrics,
            "weights": weights,
            "flags": flags,
            "data_notes": data_notes,
            "result": result,
        }

        save_analysis(st.session_state["email"], p.address, listing_url, result, payload)

        pdf_name = f"AIRE_Report_{int(time.time())}.pdf"
        build_pdf(pdf_name, p, nums, result, strengths, risks, data_notes)
        with open(pdf_name, "rb") as f:
            st.download_button("⬇️ Download PDF report", f, file_name=pdf_name, mime="application/pdf")

        with st.expander("Details (audit trail)", expanded=False):
            st.json(payload)

    st.markdown("</div>", unsafe_allow_html=True)
    st.write("")
    st.markdown(f'<div class="aire-disclaimer">Disclaimer: {APP_NAME} is informational and not financial advice. Always verify inputs and assumptions.</div>', unsafe_allow_html=True)

elif page == "History":
    st.markdown('<div class="aire-card">', unsafe_allow_html=True)
    st.markdown("### History")
    st.caption("Your last analyses are saved here.")

    items = fetch_analyses(st.session_state["email"], limit=50)
    if not items:
        st.info("No analyses yet. Run one in Analyze.")
        st.markdown("</div>", unsafe_allow_html=True)
        st.stop()

    for it in items[:20]:
        cols = st.columns([2.2, 1.2, 0.8, 1.2, 1.0, 1.0, 1.0])
        cols[0].write(f"**{it['address'] or 'Unknown'}**\n\n{ts_to_str(it['created_at'])}")
        cols[1].write(it["verdict"])
        cols[2].write(f"**{it['grade']}**")
        cols[3].write(f"{it['score']:.1f}")
        cols[4].write(f"{it['dscr']:.2f}")
        cols[5].write(f"{it['cap_rate']*100:.2f}%")
        cols[6].write(f"{it['coc_return']*100:.2f}%")
        st.divider()

    st.markdown("</div>", unsafe_allow_html=True)

elif page == "Account":
    st.markdown('<div class="aire-card">', unsafe_allow_html=True)
    st.markdown("### Account")
    st.write(f"Signed in as: **{st.session_state['email']}**")
    if user["paid"]:
        st.markdown('<span class="aire-pill">PRO</span>', unsafe_allow_html=True)
        st.write("Status: Subscribed")
    else:
        st.markdown('<span class="aire-pill-warn">FREE</span>', unsafe_allow_html=True)
        st.write(f"Credits remaining: **{user['credits']}**")

    st.write("")
    st.markdown("**Upgrade**")
    pay_link = st.secrets.get("STRIPE_PAYMENT_LINK_URL", "")
    if pay_link:
        st.link_button("Subscribe (Stripe)", pay_link)
    else:
        st.info("Add STRIPE_PAYMENT_LINK_URL in secrets to enable payments.")

    st.write("")
    demo_admin_unlock(st.session_state["email"])
    st.markdown("</div>", unsafe_allow_html=True)

else:
    st.markdown('<div class="aire-card">', unsafe_allow_html=True)
    st.markdown("### About")
    st.write("AIRE™ is a deterministic underwriting system with AI-style risk flagging and clean reporting.")
    st.write("It is designed to be auditable: math drives scores; flags only reduce score (never inflate).")
    st.write("")
    st.markdown("**Roadmap**")
    st.write("• Automated Stripe webhooks (true subscription unlock)")
    st.write("• Rent comps module (range + confidence)")
    st.write("• Portfolio analytics dashboard")
    st.write("• Team accounts & shared workspaces")
    st.markdown("</div>", unsafe_allow_html=True)
