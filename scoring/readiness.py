"""
Deterministic Buy Readiness Score — two modes.

  invest mode (default): 5 dimensions (financial, market timing, area value,
                          opportunity cost, life-stage fit)
  live_in mode:          5 dimensions (financial sustainability, affordability
                          comfort, lifestyle fit, fair price, life-stage fit)

LLM is NOT used here. All arithmetic is deterministic.
Output is a strict JSON-serializable dict that narrator.py explains.

Score bands:
  invest:  80-100 Strong Buy / 60-79 Conditional Buy / 40-59 Marginal / 0-39 Do Not Buy
  live_in: 80-100 Very Ready / 60-79 Ready with Caveats / 40-59 Marginal Fit / 0-39 Not Ready

Usage:
    python -m scoring.readiness
"""
from __future__ import annotations

import re
from typing import Any

# ── ABSD rates (effective 2023-04-27) ────────────────────────────────────────
ABSD_RATES: dict[str, dict[int, float]] = {
    "SC":  {1: 0.00, 2: 0.20, 3: 0.30},
    "PR":  {1: 0.05, 2: 0.30, 3: 0.35},
    "FR":  {1: 0.60, 2: 0.60, 3: 0.60},
    "ENT": {1: 0.65, 2: 0.65, 3: 0.65},
}

# ── BSD bands (effective 2023-02-15) ─────────────────────────────────────────
BSD_BANDS: list[tuple[float, float]] = [
    (180_000,       0.01),
    (180_000,       0.02),
    (640_000,       0.03),
    (500_000,       0.04),
    (1_500_000,     0.05),
    (float("inf"),  0.06),
]

# ── LTV limits (effective 2022-09-30) ────────────────────────────────────────
LTV_LIMITS: dict[int, float] = {1: 0.75, 2: 0.45, 3: 0.35}

# ── Dimension weights ─────────────────────────────────────────────────────────
WEIGHTS: dict[str, float] = {
    "financial_readiness": 0.30,
    "market_timing":       0.20,
    "area_value":          0.20,
    "opportunity_cost":    0.15,
    "life_stage_fit":      0.15,
}

LIVE_IN_WEIGHTS: dict[str, float] = {
    "financial_sustainability": 0.35,
    "affordability_comfort":    0.25,
    "lifestyle_fit":            0.20,
    "fair_price":               0.10,
    "life_stage_fit":           0.10,
}

SCORE_BANDS: list[tuple[float, str]] = [
    (80, "Strong Buy"),
    (60, "Conditional Buy"),
    (40, "Marginal"),
    (0,  "Do Not Buy"),
]

LIVE_IN_BANDS: list[tuple[float, str]] = [
    (80, "Very Ready"),
    (60, "Ready with Caveats"),
    (40, "Marginal Fit"),
    (0,  "Not Ready"),
]

DISCLAIMER = "Not financial advice. Verify effective_date before transacting."

# Max credible CAGR per year from transaction data (guards against HDB/URA mixing)
_MAX_CAGR = 0.30


# ── Math helpers ──────────────────────────────────────────────────────────────

def calc_bsd(price: float) -> float:
    """Buyer's Stamp Duty on purchase price."""
    duty = 0.0
    remaining = price
    for band, rate in BSD_BANDS:
        taxable = min(remaining, band)
        duty += taxable * rate
        remaining -= taxable
        if remaining <= 0:
            break
    return duty


def calc_absd(price: float, citizenship: str, num_existing: int) -> float:
    """ABSD. num_existing = number of residential properties currently owned."""
    key = citizenship.upper()
    if key not in ABSD_RATES:
        key = "FR"
    nth = min(num_existing + 1, 3)
    rate = ABSD_RATES[key].get(nth, ABSD_RATES[key][3])
    return price * rate


def monthly_repayment(loan: float, annual_rate: float, tenure_years: int) -> float:
    """Standard annuity formula."""
    r = annual_rate / 12
    n = tenure_years * 12
    if r == 0 or n == 0:
        return loan / n if n else 0
    return loan * r * (1 + r) ** n / ((1 + r) ** n - 1)


def ltv_for_property(num_existing: int) -> float:
    return LTV_LIMITS.get(min(num_existing + 1, 3), 0.35)


# ── Chunk data extractors ─────────────────────────────────────────────────────

def _extract_psf_from_chunk(chunk: dict) -> float | None:
    """Pull median PSF from a URA transaction or HDB chunk."""
    m = re.search(r"PSF[^:]*:\s*SGD\s*([\d,]+)", chunk.get("text", ""))
    if m:
        return float(m.group(1).replace(",", ""))
    return None


def _extract_year_from_chunk(chunk: dict) -> int | None:
    m = re.search(r"\|\s*(\d{4})\b", chunk.get("text", ""))
    return int(m.group(1)) if m else None


def _extract_rental_psf_from_chunk(chunk: dict) -> float | None:
    m = re.search(r"Median rental PSF/month:\s*SGD\s*([\d.]+)", chunk.get("text", ""))
    if m:
        return float(m.group(1))
    return None


# ── Dimension 1: Financial Readiness (invest) / Financial Sustainability (live-in) ──

def _score_financial_readiness(p: dict[str, Any]) -> dict:
    flags: list[str] = []
    citations: list[dict] = []

    price = p["property_price"]
    citizenship = p.get("citizenship", "SC").upper()
    num_existing = p.get("num_existing_properties", 0)
    is_hdb = p.get("is_hdb", False)
    cash = p.get("cash", 0.0)
    cpf_oa = p.get("cpf_oa", 0.0)
    monthly_income = p.get("monthly_income", 1.0)
    monthly_obligations = p.get("monthly_obligations", 0.0)
    loan_tenure = p.get("loan_tenure", 25)
    annual_sora = p.get("annual_sora", 0.037)
    bank_spread = p.get("bank_spread", 0.011)

    # LTV + down payment
    ltv = ltv_for_property(num_existing)
    loan_amount = price * ltv
    down_payment = price - loan_amount
    min_cash_down = price * 0.05  # at least 5% cash
    cpf_usable = min(cpf_oa, down_payment - min_cash_down)
    cash_for_down = max(down_payment - cpf_usable, min_cash_down)

    absd = calc_absd(price, citizenship, num_existing)
    bsd = calc_bsd(price)
    misc = price * 0.02  # legal + agent fees
    total_upfront = cash_for_down + absd + bsd + misc

    # Emergency fund: 6 months of mortgage payments (not income)
    # Income-based formula produces absurd results for high earners
    # (e.g. $140k/month income → $840k "emergency fund" for a $1M property)
    actual_rate = p.get("annual_sora", 0.037) + p.get("bank_spread", 0.011)
    monthly_repay_est = monthly_repayment(loan_amount, max(actual_rate, 0.04), loan_tenure)
    emergency_fund = max(monthly_repay_est * 6, 30_000)  # min $30k floor

    total_needed = total_upfront + emergency_fund
    cash_headroom = cash - total_needed

    citations += [
        {
            "source": "policy",
            "field": "LTV",
            "value": f"{ltv*100:.0f}%",
            "note": f"Property #{num_existing + 1}, effective 2022-09-30",
        },
        {
            "source": "policy",
            "field": "ABSD",
            "value": f"SGD {absd:,.0f} ({absd/price*100:.0f}% of price)",
            "note": f"{citizenship}, property #{num_existing + 1}, effective 2023-04-27",
        },
        {
            "source": "policy",
            "field": "BSD",
            "value": f"SGD {bsd:,.0f}",
            "note": "effective 2023-02-15",
        },
    ]

    # TDSR (stressed at max(actual_rate, 4%))
    actual_rate = annual_sora + bank_spread
    stressed_rate = max(actual_rate, 0.04)
    monthly_repay = monthly_repayment(loan_amount, stressed_rate, loan_tenure)
    tdsr = (monthly_obligations + monthly_repay) / monthly_income if monthly_income > 0 else 1.0

    citations.append({
        "source": "policy",
        "field": "TDSR",
        "value": f"{tdsr*100:.1f}%",
        "note": f"Stressed rate {stressed_rate*100:.1f}%, limit 55%, effective 2023-07-01",
    })

    # MSR (HDB/EC only)
    msr = None
    if is_hdb:
        msr = (monthly_obligations + monthly_repay) / monthly_income if monthly_income > 0 else 1.0
        citations.append({
            "source": "policy",
            "field": "MSR",
            "value": f"{msr*100:.1f}%",
            "note": "HDB/EC limit 30%, effective 2013-08-27",
        })

    # Scoring
    score = 100.0

    # Cash sufficiency (up to -40)
    if cash_headroom < 0:
        pct_deficit = abs(cash_headroom) / max(total_needed, 1)
        deduction = min(40, pct_deficit * 80)
        score -= deduction
        flags.append(
            f"Cash shortfall: need SGD {total_needed:,.0f} "
            f"(upfront + 6-month emergency fund), have SGD {cash:,.0f}"
        )

    # TDSR (up to -25)
    if tdsr > 0.55:
        score -= 25
        flags.append(f"TDSR breach: {tdsr*100:.1f}% exceeds 55% limit — loan likely rejected")
    elif tdsr > 0.45:
        score -= 12
        flags.append(f"TDSR tight: {tdsr*100:.1f}% (limit 55%) — limited income buffer")
    elif tdsr > 0.35:
        score -= 4

    # MSR (up to -20)
    if msr is not None:
        if msr > 0.30:
            score -= 20
            flags.append(f"MSR breach: {msr*100:.1f}% exceeds 30% HDB/EC limit")
        elif msr > 0.25:
            score -= 8
            flags.append(f"MSR tight: {msr*100:.1f}% (limit 30%)")

    # ABSD burden (up to -15)
    absd_pct_of_cash = absd / cash if cash > 0 else 1.0
    if absd_pct_of_cash > 0.5:
        score -= 15
        flags.append(
            f"ABSD SGD {absd:,.0f} = {absd_pct_of_cash*100:.0f}% of liquid cash — severe cash drain"
        )
    elif absd_pct_of_cash > 0.3:
        score -= 7
        flags.append(f"ABSD SGD {absd:,.0f} = {absd_pct_of_cash*100:.0f}% of liquid cash")

    score = max(0.0, min(100.0, score))
    return {
        "score": round(score, 1),
        "weight": WEIGHTS["financial_readiness"],
        "flags": flags,
        "citations": citations,
        "details": {
            "ltv": ltv,
            "loan_amount": round(loan_amount),
            "down_payment": round(down_payment),
            "absd": round(absd),
            "bsd": round(bsd),
            "total_upfront": round(total_upfront),
            "monthly_repayment": round(monthly_repay),
            "mortgage_rate_actual": round(actual_rate, 4),
            "tdsr": round(tdsr, 4),
            "msr": round(msr, 4) if msr is not None else None,
            "cash_headroom": round(cash_headroom),
        },
    }


# ── Dimension 2: Market Timing (invest only) ──────────────────────────────────

def _score_market_timing(p: dict[str, Any], chunks: list[dict]) -> dict:
    flags: list[str] = []
    citations: list[dict] = []
    score = 65.0  # neutral baseline

    price = p["property_price"]
    is_hdb = p.get("is_hdb", False)
    annual_sora = p.get("annual_sora", 0.037)
    bank_spread = p.get("bank_spread", 0.011)
    mortgage_rate = annual_sora + bank_spread

    # Use only the appropriate data source — never mix HDB and URA PSF
    if is_hdb:
        txn_chunks = [c for c in chunks if c.get("source") == "hdb"]
    else:
        txn_chunks = [c for c in chunks if c.get("source") == "ura_transaction"]

    year_psf: dict[int, float] = {}
    for c in txn_chunks:
        psf = _extract_psf_from_chunk(c)
        yr = _extract_year_from_chunk(c)
        if psf and yr:
            year_psf[yr] = psf
            citations.append({
                "source": c["source"],
                "field": f"psf_{yr}",
                "value": f"SGD {psf:,.0f}/sqft",
                "relevance_score": c["score"],
            })

    if len(year_psf) >= 2:
        years = sorted(year_psf)

        # Current market: 2024–2026 weighted average (what buyers are paying now)
        recent_years = [y for y in years if y >= 2024]
        trend_years  = [y for y in years if y < 2024]

        if recent_years:
            weights = {y: (y - 2023) for y in recent_years}  # 2024=1, 2025=2, 2026=3
            total_w = sum(weights.values())
            current_psf = sum(year_psf[y] * weights[y] for y in recent_years) / total_w
        else:
            current_psf = year_psf[years[-1]]

        # Historical baseline: pre-2024 average (trend reference)
        trend_psf = (
            sum(year_psf[y] for y in trend_years) / len(trend_years)
        ) if trend_years else current_psf

        # Market timing: is the MARKET currently at a premium vs its own history?
        # (Independent of the entered price — that's what fair_price checks)
        premium = (current_psf - trend_psf) / trend_psf if trend_psf > 0 else 0

        citations.append({
            "source": txn_chunks[0]["source"] if txn_chunks else "market",
            "field": "market_timing_psf",
            "value": f"Current SGD {current_psf:,.0f}/sqft vs historical SGD {trend_psf:,.0f}/sqft",
            "note": f"Current = {min(recent_years) if recent_years else years[-1]}–{max(recent_years) if recent_years else years[-1]} weighted avg",
        })

        if premium > 0.20:
            score -= 20
            flags.append(
                f"Market at premium: current PSF SGD {current_psf:,.0f} is "
                f"{premium*100:.0f}% above pre-2024 baseline SGD {trend_psf:,.0f} — late-cycle entry"
            )
        elif premium > 0.08:
            score -= 10
            flags.append(
                f"Market slightly elevated: current PSF {premium*100:.0f}% above pre-2024 baseline"
            )
        elif premium < -0.10:
            score += 15
            flags.append(
                f"Market below historical baseline: current PSF SGD {current_psf:,.0f} "
                f"({abs(premium)*100:.0f}% below pre-2024) — potential value entry"
            )
        elif premium < -0.03:
            score += 5

    # Rental yield vs mortgage rate (private only — HDB can't rent whole flat)
    if not is_hdb:
        rental_chunks = [c for c in chunks if c.get("source") == "ura_rental"]
        rental_psf_vals = [_extract_rental_psf_from_chunk(c) for c in rental_chunks]
        rental_psf_vals = [v for v in rental_psf_vals if v]

        if rental_psf_vals and year_psf:
            median_rental_psf = sorted(rental_psf_vals)[len(rental_psf_vals) // 2]
            latest_year = max(year_psf)
            purchase_psf = year_psf[latest_year]
            if purchase_psf > 0:
                gross_yield = (median_rental_psf * 12) / purchase_psf
                net_yield = gross_yield - 0.01  # ~1% for maintenance/vacancy

                citations.append({
                    "source": "ura_rental",
                    "field": "gross_yield",
                    "value": f"{gross_yield*100:.2f}%",
                    "note": (
                        f"Rental PSF SGD {median_rental_psf:.2f}/mo "
                        f"vs purchase PSF SGD {purchase_psf:,.0f}; "
                        f"mortgage {mortgage_rate*100:.2f}%"
                    ),
                })

                if net_yield >= mortgage_rate:
                    score += 10
                    flags.append(
                        f"Positive carry: net yield {net_yield*100:.2f}% > "
                        f"mortgage {mortgage_rate*100:.2f}%"
                    )
                else:
                    gap = mortgage_rate - net_yield
                    score -= min(15, gap * 150)
                    flags.append(
                        f"Negative carry: net yield {net_yield*100:.2f}% < "
                        f"mortgage {mortgage_rate*100:.2f}% (gap {gap*100:.2f}pp)"
                    )

    score = max(0.0, min(100.0, score))
    return {
        "score": round(score, 1),
        "weight": WEIGHTS["market_timing"],
        "flags": flags,
        "citations": citations,
    }


# ── Dimension 3: Area Value (invest only) ────────────────────────────────────

def _score_area_value(p: dict[str, Any], chunks: list[dict]) -> dict:
    flags: list[str] = []
    citations: list[dict] = []
    score = 65.0

    is_hdb = p.get("is_hdb", False)

    # Filter to matching data source only — never mix HDB and URA PSF
    if is_hdb:
        txn_chunks = [c for c in chunks if c.get("source") == "hdb"]
    else:
        txn_chunks = [c for c in chunks if c.get("source") == "ura_transaction"]

    year_psf: dict[int, float] = {}
    for c in txn_chunks:
        psf = _extract_psf_from_chunk(c)
        yr = _extract_year_from_chunk(c)
        if psf and yr:
            year_psf[yr] = psf
            citations.append({
                "source": c["source"],
                "field": f"psf_{yr}",
                "value": f"SGD {psf:,.0f}/sqft",
                "relevance_score": c["score"],
            })

    if len(year_psf) >= 2:
        years = sorted(year_psf)
        oldest, newest = year_psf[years[0]], year_psf[years[-1]]
        n_years = years[-1] - years[0]
        if n_years > 0 and oldest > 0:
            raw_cagr = (newest / oldest) ** (1 / n_years) - 1
            cagr = min(raw_cagr, _MAX_CAGR)  # cap at 30%/yr
            if raw_cagr > _MAX_CAGR:
                flags.append(
                    f"PSF data quality warning: raw CAGR {raw_cagr*100:.0f}% capped at "
                    f"{_MAX_CAGR*100:.0f}% — possible mixed data source in Pinecone"
                )
            if cagr >= 0.06:
                score += 20
                flags.append(f"Strong PSF CAGR {cagr*100:.1f}%/yr ({years[0]}–{years[-1]})")
            elif cagr >= 0.03:
                score += 10
                flags.append(f"Moderate PSF CAGR {cagr*100:.1f}%/yr")
            elif cagr >= 0.00:
                score += 3
            else:
                score -= 15
                flags.append(f"Declining PSF: {cagr*100:.1f}%/yr — weak area value")

    if not txn_chunks:
        flags.append("No area PSF data retrieved — area value score uses neutral default")

    score = max(0.0, min(100.0, score))
    return {
        "score": round(score, 1),
        "weight": WEIGHTS["area_value"],
        "flags": flags,
        "citations": citations,
    }


# ── Dimension 4: Opportunity Cost (invest only) ───────────────────────────────

def _score_opportunity_cost(p: dict[str, Any]) -> dict:
    flags: list[str] = []
    citations: list[dict] = []

    price = p["property_price"]
    citizenship = p.get("citizenship", "SC").upper()
    num_existing = p.get("num_existing_properties", 0)
    cpf_oa = p.get("cpf_oa", 0.0)

    absd = calc_absd(price, citizenship, num_existing)
    bsd = calc_bsd(price)
    ltv = ltv_for_property(num_existing)
    down_payment = price * (1 - ltv)
    capital_locked = down_payment + absd + bsd

    citations.append({
        "source": "policy",
        "field": "CPF OA rate",
        "value": "2.5% per annum (guaranteed)",
        "note": f"On SGD {capital_locked:,.0f} capital committed; effective 2021-10-01",
    })

    score = 70.0

    # ABSD is sunk cost that cannot be recovered
    absd_pct = absd / price if price > 0 else 0
    if absd_pct >= 0.20:
        score -= 30
        flags.append(
            f"ABSD {absd_pct*100:.0f}% = SGD {absd:,.0f} — large irreversible sunk cost; "
            "very high hurdle rate to outperform CPF/equities"
        )
    elif absd_pct >= 0.05:
        score -= 8
        flags.append(f"ABSD SGD {absd:,.0f} reduces capital efficiency vs alternatives")
    elif absd_pct > 0:
        flags.append(f"ABSD SGD {absd:,.0f} — modest impact on returns")

    # CPF OA opportunity cost
    cpf_used = min(cpf_oa, down_payment * 0.95)
    if cpf_used > 50_000:
        score -= 5
        flags.append(
            f"SGD {cpf_used:,.0f} CPF OA deployed — "
            f"forgoes ~SGD {cpf_used*0.025:,.0f}/yr guaranteed return"
        )

    score = max(0.0, min(100.0, score))
    return {
        "score": round(score, 1),
        "weight": WEIGHTS["opportunity_cost"],
        "flags": flags,
        "citations": citations,
        "details": {
            "capital_locked": round(capital_locked),
            "cpf_oa_annual_return_forgone": round(capital_locked * 0.025),
        },
    }


# ── Dimension 5: Life-Stage Fit (both modes) ─────────────────────────────────

def _score_life_stage_fit(p: dict[str, Any]) -> dict:
    flags: list[str] = []
    citations: list[dict] = []

    price = p["property_price"]
    citizenship = p.get("citizenship", "SC").upper()
    num_existing = p.get("num_existing_properties", 0)
    is_hdb = p.get("is_hdb", False)
    age = p.get("age", 35)
    holding_years = p.get("holding_years", 10)
    loan_tenure = p.get("loan_tenure", 25)
    lease_remaining = p.get("lease_remaining", 99)

    absd = calc_absd(price, citizenship, num_existing)
    bsd = calc_bsd(price)
    transaction_cost_pct = (absd + bsd + price * 0.04) / price

    # Breakeven holding period assuming 3% annual appreciation
    assumed_appreciation = 0.03
    breakeven_years = (
        transaction_cost_pct / assumed_appreciation if assumed_appreciation > 0 else 99
    )

    score = 70.0

    if holding_years < breakeven_years:
        shortfall = breakeven_years - holding_years
        score -= min(25, shortfall * 4)
        flags.append(
            f"Planned hold {holding_years}yr < breakeven ~{breakeven_years:.0f}yr "
            f"(at 3% appreciation) — short exit crystallises a loss"
        )
    else:
        score += 5

    # Lease decay (HDB only)
    if is_hdb and lease_remaining < 99:
        age_at_lease_end = age + lease_remaining
        coverage_to_95 = lease_remaining >= (95 - age)
        if not coverage_to_95:
            score -= 25
            flags.append(
                f"Lease {lease_remaining}yr does not cover buyer to age 95 "
                f"(only to {age_at_lease_end}) — CPF Withdrawal Limit fully prorated"
            )
            citations.append({
                "source": "policy",
                "field": "CPF Withdrawal Limit",
                "value": "Prorated when lease < (95 - buyer age)",
                "note": f"Lease covers to {age_at_lease_end}; CPF requires 95",
            })
        elif lease_remaining < 60:
            score -= 12
            flags.append(f"Short lease {lease_remaining}yr — reduced CPF access, lower bank valuation")
        elif lease_remaining < 75:
            score -= 5
            flags.append(f"Lease {lease_remaining}yr — minor CPF proration may apply")

    # High ABSD + short hold = almost impossible to break even
    absd_pct = absd / price if price > 0 else 0
    if absd_pct >= 0.20 and holding_years < 5:
        score -= 20
        flags.append(
            f"ABSD {absd_pct*100:.0f}% + hold {holding_years}yr = "
            "extremely difficult to break even"
        )

    # Loan tenure vs retirement
    retirement_age = 65
    years_to_retire = max(0, retirement_age - age)
    if loan_tenure > years_to_retire + 5:
        overshoot = loan_tenure - years_to_retire
        score -= min(10, overshoot * 2)
        flags.append(
            f"Loan tenure {loan_tenure}yr extends {overshoot}yr past retirement — "
            "post-retirement repayment risk"
        )

    score = max(0.0, min(100.0, score))
    return {
        "score": round(score, 1),
        "weight": WEIGHTS["life_stage_fit"],
        "flags": flags,
        "citations": citations,
        "details": {
            "transaction_cost_pct": round(transaction_cost_pct * 100, 2),
            "breakeven_years": round(breakeven_years, 1),
        },
    }


# ── Live-in Dimension A: Affordability Comfort ────────────────────────────────

def _score_affordability_comfort(p: dict[str, Any]) -> dict:
    """
    How comfortably can you service this mortgage long-term?
    No yield penalty — buying to live in, not rent out.
    """
    flags: list[str] = []
    citations: list[dict] = []

    price = p["property_price"]
    monthly_income = p.get("monthly_income", 1.0)
    monthly_obligations = p.get("monthly_obligations", 0.0)
    num_existing = p.get("num_existing_properties", 0)
    loan_tenure = p.get("loan_tenure", 25)
    annual_sora = p.get("annual_sora", 0.037)
    bank_spread = p.get("bank_spread", 0.011)

    actual_rate = annual_sora + bank_spread
    ltv = ltv_for_property(num_existing)
    loan_amount = price * ltv
    monthly_repay = monthly_repayment(loan_amount, actual_rate, loan_tenure)
    emi_ratio = monthly_repay / monthly_income if monthly_income > 0 else 1.0
    total_ratio = (monthly_repay + monthly_obligations) / monthly_income if monthly_income > 0 else 1.0

    citations.append({
        "source": "policy",
        "field": "Monthly EMI",
        "value": f"SGD {monthly_repay:,.0f}/month",
        "note": f"At {actual_rate*100:.2f}% on SGD {loan_amount:,.0f} loan over {loan_tenure}yr",
    })

    score = 75.0

    if emi_ratio <= 0.25:
        score += 15
        flags.append(
            f"Comfortable EMI ratio: {emi_ratio*100:.0f}% of income — "
            "strong discretionary buffer retained"
        )
    elif emi_ratio <= 0.33:
        score += 5
    elif emi_ratio <= 0.45:
        score -= 12
        flags.append(
            f"Stretched EMI: {emi_ratio*100:.0f}% of income — "
            "limited room for lifestyle spending or emergencies"
        )
    else:
        score -= 25
        flags.append(
            f"Heavy EMI burden: {emi_ratio*100:.0f}% of income — "
            "very little left after mortgage; financial stress risk"
        )

    # Rate-rise stress: +2% SORA scenario
    stressed_rate = actual_rate + 0.02
    stressed_repay = monthly_repayment(loan_amount, stressed_rate, loan_tenure)
    stressed_ratio = stressed_repay / monthly_income if monthly_income > 0 else 1.0

    citations.append({
        "source": "policy",
        "field": "Stress-tested EMI (+2% SORA)",
        "value": f"SGD {stressed_repay:,.0f}/month ({stressed_ratio*100:.0f}% of income)",
        "note": "Scenario: SORA rises 200bps above current level",
    })

    if stressed_ratio > 0.55:
        score -= 15
        flags.append(
            f"Rate-rise risk: +2% SORA pushes EMI to {stressed_ratio*100:.0f}% of income "
            "— near TDSR limit, bank may review"
        )
    elif stressed_ratio > 0.45:
        score -= 7
        flags.append(
            f"Rate sensitivity: +2% SORA raises EMI to {stressed_ratio*100:.0f}% of income"
        )

    # Monthly surplus check
    monthly_surplus = monthly_income - monthly_repay - monthly_obligations
    if monthly_surplus < monthly_income * 0.20:
        score -= 8
        flags.append(
            f"Low monthly surplus SGD {monthly_surplus:,.0f} — "
            "less than 20% of income left after all commitments"
        )

    score = max(0.0, min(100.0, score))
    return {
        "score": round(score, 1),
        "weight": LIVE_IN_WEIGHTS["affordability_comfort"],
        "flags": flags,
        "citations": citations,
        "details": {
            "monthly_repayment": round(monthly_repay),
            "emi_ratio": round(emi_ratio, 4),
            "monthly_surplus": round(monthly_surplus),
            "stressed_repayment": round(stressed_repay),
            "stressed_ratio": round(stressed_ratio, 4),
        },
    }


# ── Live-in Dimension B: Lifestyle Fit ───────────────────────────────────────

def _score_lifestyle_fit(p: dict[str, Any], chunks: list[dict]) -> dict:
    """
    Score how well the area suits daily living: MRT access, schools, parks, CBD.
    Requires ROI chunks (fetched when planning_area is specified).
    """
    flags: list[str] = []
    citations: list[dict] = []
    score = 55.0  # neutral; only improves with good area data

    # Pull from profile (injected by API from area_amenities.csv — LTA + MOE data)
    mrt_m    = p.get("mrt_dist_m")
    mrt_name = p.get("mrt_name", "")
    sch_m    = p.get("school_dist_m")
    sch_name = p.get("school_name", "")

    if mrt_m is not None:
        citations.append({"source": "lta", "field": "Nearest MRT",
                          "value": f"{mrt_name} — {mrt_m}m", "note": "LTA MRT Station data"})
        if mrt_m <= 300:
            score += 25
            flags.append(f"Excellent MRT access: {mrt_name} {mrt_m}m away (< 5 min walk)")
        elif mrt_m <= 600:
            score += 15
            flags.append(f"Good MRT access: {mrt_name} {mrt_m}m away (~8 min walk)")
        elif mrt_m <= 1000:
            score += 8
            flags.append(f"Moderate MRT access: {mrt_name} {mrt_m}m away (~12 min walk)")
        else:
            flags.append(f"MRT is {mrt_m}m away ({mrt_name}) — likely car-dependent")
    else:
        flags.append("MRT data not available for this area")

    if sch_m is not None:
        citations.append({"source": "moe", "field": "Nearest primary school",
                          "value": f"{sch_name} — {sch_m}m", "note": "MOE School Directory"})
        if sch_m <= 500:
            score += 10
            flags.append(f"Top primary school nearby: {sch_name} ({sch_m}m) — great for families")
        elif sch_m <= 1000:
            score += 6
            flags.append(f"Primary school within 1km: {sch_name} ({sch_m}m)")
        elif sch_m > 2000:
            score -= 5

    if mrt_m is None and sch_m is None:
        flags.append(
            "Lifestyle data not available — specify a planning area for MRT and school scoring"
        )

    score = max(0.0, min(100.0, score))
    return {
        "score": round(score, 1),
        "weight": LIVE_IN_WEIGHTS["lifestyle_fit"],
        "flags": flags,
        "citations": citations,
        "details": {"mrt_dist_m": mrt_m, "mrt_name": mrt_name,
                    "school_dist_m": sch_m, "school_name": sch_name},
    }


# ── Live-in Dimension C: Fair Price ──────────────────────────────────────────

def _score_fair_price(p: dict[str, Any], chunks: list[dict]) -> dict:
    """
    Is the price reasonable vs area comparables?
    Uses property-type-appropriate chunks only (no HDB/URA mixing).
    """
    flags: list[str] = []
    citations: list[dict] = []
    score = 65.0

    price = p["property_price"]
    is_hdb = p.get("is_hdb", False)

    if is_hdb:
        txn_chunks = [c for c in chunks if c.get("source") == "hdb"]
    else:
        txn_chunks = [c for c in chunks if c.get("source") == "ura_transaction"]

    # HDB flat sizes by type (Singapore median sqft) — used for PSF fallback only
    _HDB_SQFT = {"3 ROOM": 700, "4 ROOM": 950, "5 ROOM": 1200, "EXECUTIVE": 1400}
    flat_type_key = str(p.get("flat_type", "")).upper().strip()
    if is_hdb:
        assumed_sqft = float(_HDB_SQFT.get(flat_type_key, 1000))
    else:
        assumed_sqft = 700.0

    # ── Use pre-computed benchmark from CSV if available (most reliable) ─────
    area_median_price = p.get("area_median_price_2024_26")
    area_median_psf   = p.get("area_median_psf_2024_26")

    if area_median_price and area_median_psf:
        # Compare entered price directly against area median price (no sqft assumption needed)
        median_psf  = float(area_median_psf)
        bench_label = "2024–2026 area median (from transaction registry)"
        citations.append({
            "source": "hdb" if is_hdb else "ura_transaction",
            "field": "area_median_price_2024_26",
            "value": f"SGD {area_median_price:,.0f} (median) | SGD {area_median_psf:,.0f}/sqft",
            "note": bench_label,
        })
        # Compare entered price vs area median price
        premium = (price - area_median_price) / area_median_price if area_median_price > 0 else 0
        entered_label = f"S${price:,.0f}"
        market_label  = f"S${area_median_price:,.0f} area median"

    else:
        # ── Fallback: parse chunks with recency weighting ─────────────────
        # assumed_sqft already set above based on flat_type
        entered_psf  = price / assumed_sqft
        entered_label = f"~S${entered_psf:,.0f}/sqft"

        year_psf: dict[int, float] = {}
        for c in txn_chunks:
            psf = _extract_psf_from_chunk(c)
            yr  = _extract_year_from_chunk(c)
            if psf and yr:
                year_psf[yr] = psf
                citations.append({
                    "source": c["source"],
                    "field": f"area_psf_{yr}",
                    "value": f"SGD {psf:,.0f}/sqft",
                    "note": f"relevance score {c.get('score', 'n/a')}",
                })

        if year_psf:
            recent = {y: v for y, v in year_psf.items() if y >= 2024}
            if recent:
                weights = {y: (y - 2023) for y in recent}
                total_w = sum(weights.values())
                median_psf = sum(recent[y] * weights[y] for y in recent) / total_w
                bench_label = f"{min(recent)}–{max(recent)} weighted avg"
            else:
                vals = list(year_psf.values())
                median_psf = sorted(vals)[len(vals) // 2]
                bench_label = "historical avg"
            premium = (entered_psf - median_psf) / median_psf if median_psf > 0 else 0
            market_label = f"S${median_psf:,.0f}/sqft {bench_label}"
        else:
            median_psf = 0.0
            premium = 0.0
            bench_label = "no data"
            market_label = "no comparables"

        area_median_price = None  # not available in fallback

    if premium != 0 or area_median_price is not None or median_psf > 0:
        if premium <= 0:
            score += 15
            flags.append(f"At or below area median ({entered_label} vs {market_label}) — fair value")
        elif premium <= 0.10:
            score += 5
            flags.append(f"Slight premium: {premium*100:.0f}% above {market_label}")
        elif premium <= 0.20:
            score -= 8
            flags.append(f"Moderate premium: {premium*100:.0f}% above {market_label}")
        elif premium <= 0.35:
            score -= 18
            flags.append(f"Significant premium: {premium*100:.0f}% above {market_label} — get independent valuation")
        else:
            score -= 28
            flags.append(f"Large premium: {premium*100:.0f}% above {market_label} — possible overpay")
    else:
        flags.append(
            "No area price data for comparison — specify a planning area for fair price analysis"
        )

    score = max(0.0, min(100.0, score))
    return {
        "score": round(score, 1),
        "weight": LIVE_IN_WEIGHTS["fair_price"],
        "flags": flags,
        "citations": citations,
    }


# ── Public entry point ────────────────────────────────────────────────────────

def get_band(score: float) -> str:
    for threshold, label in SCORE_BANDS:
        if score >= threshold:
            return label
    return "Do Not Buy"


def get_live_in_band(score: float) -> str:
    for threshold, label in LIVE_IN_BANDS:
        if score >= threshold:
            return label
    return "Not Ready"


def compute_score(profile: dict[str, Any], chunks: list[dict], purpose: str = "invest") -> dict:
    """
    Args:
        profile: user profile dict
        chunks:  list of retrieved chunk dicts from rag.retriever.retrieve()
        purpose: "invest" (default) or "live_in"

    Returns:
        JSON-serializable dict with overall_score, band, dimensions, hard_stops, disclaimer
    """
    if purpose == "live_in":
        fin = _score_financial_readiness(profile)
        fin["weight"] = LIVE_IN_WEIGHTS["financial_sustainability"]

        lst = _score_life_stage_fit(profile)
        lst["weight"] = LIVE_IN_WEIGHTS["life_stage_fit"]

        dims = {
            "financial_sustainability": fin,
            "affordability_comfort":    _score_affordability_comfort(profile),
            "lifestyle_fit":            _score_lifestyle_fit(profile, chunks),
            "fair_price":               _score_fair_price(profile, chunks),
            "life_stage_fit":           lst,
        }
        band_fn = get_live_in_band
    else:
        dims = {
            "financial_readiness": _score_financial_readiness(profile),
            "market_timing":       _score_market_timing(profile, chunks),
            "area_value":          _score_area_value(profile, chunks),
            "opportunity_cost":    _score_opportunity_cost(profile),
            "life_stage_fit":      _score_life_stage_fit(profile),
        }
        band_fn = get_band

    overall = sum(d["score"] * d["weight"] for d in dims.values())

    hard_stops = []
    for dim_name, dim in dims.items():
        for flag in dim.get("flags", []):
            if any(kw in flag.lower() for kw in ("breach", "shortfall", "rejected")):
                hard_stops.append({"dimension": dim_name, "flag": flag})

    return {
        "overall_score": round(overall, 1),
        "band": band_fn(overall),
        "purpose": purpose,
        "dimensions": dims,
        "hard_stops": hard_stops,
        "disclaimer": DISCLAIMER,
    }


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    test_cases = [
        {
            "label": "SC first-time buyer — HDB Tampines 4-room [INVEST]",
            "purpose": "invest",
            "profile": {
                "citizenship": "SC", "age": 32, "cash": 120_000, "cpf_oa": 80_000,
                "monthly_income": 8_000, "monthly_obligations": 0,
                "property_price": 620_000, "property_type": "hdb", "is_hdb": True,
                "num_existing_properties": 0, "loan_tenure": 25,
                "annual_sora": 0.037, "bank_spread": 0.011,
                "holding_years": 10, "lease_remaining": 90,
            },
        },
        {
            "label": "SC first-time buyer — HDB Tampines 4-room [LIVE IN]",
            "purpose": "live_in",
            "profile": {
                "citizenship": "SC", "age": 32, "cash": 120_000, "cpf_oa": 80_000,
                "monthly_income": 8_000, "monthly_obligations": 0,
                "property_price": 620_000, "property_type": "hdb", "is_hdb": True,
                "num_existing_properties": 0, "loan_tenure": 25,
                "annual_sora": 0.037, "bank_spread": 0.011,
                "holding_years": 10, "lease_remaining": 90,
            },
        },
        {
            "label": "Foreigner — District 9 condo [INVEST]",
            "purpose": "invest",
            "profile": {
                "citizenship": "FR", "age": 45, "cash": 2_000_000, "cpf_oa": 0,
                "monthly_income": 30_000, "monthly_obligations": 0,
                "property_price": 3_000_000, "property_type": "private", "is_hdb": False,
                "num_existing_properties": 0, "loan_tenure": 20,
                "annual_sora": 0.037, "bank_spread": 0.011,
                "holding_years": 5, "lease_remaining": 99,
            },
        },
    ]

    for tc in test_cases:
        result = compute_score(tc["profile"], [], purpose=tc["purpose"])
        print(f"\n{'='*60}")
        print(f"Case: {tc['label']}")
        print(f"Overall: {result['overall_score']} — {result['band']}")
        for dim_name, dim in result["dimensions"].items():
            print(f"  {dim_name}: {dim['score']} (weighted {dim['score']*dim['weight']:.1f})")
            for flag in dim["flags"]:
                print(f"    FLAG: {flag}")
        if result["hard_stops"]:
            print("  HARD STOPS:")
            for hs in result["hard_stops"]:
                print(f"    [{hs['dimension']}] {hs['flag']}")
        print(f"  {result['disclaimer']}")
