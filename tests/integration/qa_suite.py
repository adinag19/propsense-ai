"""
PropSense QA Suite — 25 rigorous test cases.

Verifies:
  - ABSD rates against IRAS 2026 official rates
  - BSD calculation against IRAS bands
  - LTV limits against MAS rules
  - Scoring logic for key scenarios
  - Direction filter for all 6 cardinal directions
  - Edge cases (short lease, TDSR breach, foreigner, etc.)

Run:
    python -m tests.integration.qa_suite
"""
import sys
from scoring.readiness import (
    compute_score, calc_absd, calc_bsd,
    monthly_repayment, ltv_for_property,
)
from api.main import (
    _parse_district_filter,
    _EAST_DISTRICTS, _EAST_COAST_DISTRICTS, _WEST_DISTRICTS,
    _NORTH_DISTRICTS, _NORTHEAST_DISTRICTS, _CENTRAL_DISTRICTS,
)

results = []
all_pass = True


def check(label, condition, got, expected_str=""):
    global all_pass
    ok = bool(condition)
    if not ok:
        all_pass = False
    results.append((ok, label, str(got), expected_str))


def pct(v):
    return f"{v*100:.1f}%"


def sgd(v):
    return f"S${v:,.0f}"


def score(profile, purpose="invest"):
    return compute_score(profile, [], purpose=purpose)


# ── ABSD RATES (verified against IRAS 2026) ──────────────────────────────────
# Source: https://www.iras.gov.sg/taxes/stamp-duty/absd
# SC 1st: 0%, SC 2nd: 20%, SC 3rd: 30%
# PR 1st: 5%, PR 2nd: 30%
# Foreigner: 60% (all properties)

check("ABSD SC 1st property = 0%",
      calc_absd(1_000_000, "SC", 0) == 0,
      sgd(calc_absd(1_000_000, "SC", 0)), "S$0")

check("ABSD SC 2nd property = 20% → S$300k on S$1.5M",
      abs(calc_absd(1_500_000, "SC", 1) - 300_000) < 1,
      sgd(calc_absd(1_500_000, "SC", 1)), "S$300,000")

check("ABSD SC 3rd property = 30% → S$450k on S$1.5M",
      abs(calc_absd(1_500_000, "SC", 2) - 450_000) < 1,
      sgd(calc_absd(1_500_000, "SC", 2)), "S$450,000")

check("ABSD PR 1st property = 5% → S$50k on S$1M",
      abs(calc_absd(1_000_000, "PR", 0) - 50_000) < 1,
      sgd(calc_absd(1_000_000, "PR", 0)), "S$50,000")

check("ABSD PR 2nd property = 30% → S$600k on S$2M",
      abs(calc_absd(2_000_000, "PR", 1) - 600_000) < 1,
      sgd(calc_absd(2_000_000, "PR", 1)), "S$600,000")

check("ABSD Foreigner 1st = 60% → S$1.2M on S$2M",
      abs(calc_absd(2_000_000, "FR", 0) - 1_200_000) < 1,
      sgd(calc_absd(2_000_000, "FR", 0)), "S$1,200,000")

check("ABSD Foreigner 2nd = 60% (same rate)",
      abs(calc_absd(2_000_000, "FR", 1) - 1_200_000) < 1,
      sgd(calc_absd(2_000_000, "FR", 1)), "S$1,200,000")

# ── BSD CALCULATION (verified against IRAS 2026) ─────────────────────────────
# Bands: 1%×$180k + 2%×$180k + 3%×$640k + 4%×$500k + 5%×$1.5M + 6%>$3M

check("BSD S$180k = S$1,800",
      abs(calc_bsd(180_000) - 1_800) < 1,
      sgd(calc_bsd(180_000)), "S$1,800")

check("BSD S$500k = S$9,600",
      abs(calc_bsd(500_000) - 9_600) < 1,
      sgd(calc_bsd(500_000)), "S$9,600")
# 1800 + 3600 + 4200 = 9,600

check("BSD S$1M = S$24,600",
      abs(calc_bsd(1_000_000) - 24_600) < 1,
      sgd(calc_bsd(1_000_000)), "S$24,600")
# 1800 + 3600 + 19200 = 24,600

check("BSD S$2M = S$69,600",
      abs(calc_bsd(2_000_000) - 69_600) < 1,
      sgd(calc_bsd(2_000_000)), "S$69,600")
# 1800+3600+19200+20000+25000 = 69,600 (5th band Feb 2023: 5% on $1.5M-$3M)

check("BSD S$3.5M = S$149,600",
      abs(calc_bsd(3_500_000) - 149_600) < 1,
      sgd(calc_bsd(3_500_000)), "S$149,600")
# 1800+3600+19200+20000+75000+30000 = 149,600

# ── LTV LIMITS (MAS, effective 2022-09-30) ────────────────────────────────────
check("LTV 1st property = 75%", ltv_for_property(0) == 0.75, "75%", "75%")
check("LTV 2nd property = 45%", ltv_for_property(1) == 0.45, "45%", "45%")
check("LTV 3rd+ property = 35%", ltv_for_property(2) == 0.35, "35%", "35%")

# ── SCORING LOGIC ─────────────────────────────────────────────────────────────

BASE = dict(annual_sora=0.037, bank_spread=0.011, monthly_obligations=0,
            existing_hdb=False, district="", planning_area="", flat_type="")

# SC first HDB — ABSD=0, should score well
p1 = {**BASE, "citizenship": "SC", "age": 30, "cash": 150_000, "cpf_oa": 80_000,
      "monthly_income": 8_000, "property_price": 550_000,
      "property_type": "hdb", "is_hdb": True, "flat_type": "4 ROOM",
      "num_existing_properties": 0, "loan_tenure": 25, "holding_years": 10,
      "lease_remaining": 90}
s1 = score(p1, "live_in")
check("SC 1st HDB — ABSD is S$0",
      s1["dimensions"]["financial_sustainability"]["details"]["absd"] == 0,
      sgd(s1["dimensions"]["financial_sustainability"]["details"]["absd"]), "S$0")
check("SC 1st HDB — no hard stops",
      len(s1["hard_stops"]) == 0, s1["hard_stops"], "[]")

# TDSR breach — income too low for property price
p2 = {**BASE, "citizenship": "SC", "age": 35, "cash": 300_000, "cpf_oa": 100_000,
      "monthly_income": 4_000, "property_price": 1_500_000,
      "property_type": "private", "is_hdb": False,
      "num_existing_properties": 0, "loan_tenure": 25, "holding_years": 10,
      "lease_remaining": 99}
s2 = score(p2, "invest")
tdsr2 = s2["dimensions"]["financial_readiness"]["details"]["tdsr"]
check("TDSR breach — ratio > 55%", tdsr2 > 0.55, pct(tdsr2), ">55%")
check("TDSR breach — hard stop fires",
      any("TDSR breach" in h["flag"] for h in s2["hard_stops"]),
      [h["flag"][:50] for h in s2["hard_stops"]], "TDSR breach flag")

# MSR breach — HDB too expensive for income
p3 = {**BASE, "citizenship": "PR", "age": 36, "cash": 300_000, "cpf_oa": 80_000,
      "monthly_income": 10_000, "property_price": 900_000,
      "property_type": "hdb", "is_hdb": True, "flat_type": "5 ROOM",
      "num_existing_properties": 0, "loan_tenure": 25, "holding_years": 10,
      "lease_remaining": 90}
s3 = score(p3, "live_in")
msr3 = s3["dimensions"]["financial_sustainability"]["details"]["msr"]
check("MSR breach — ratio > 30%", msr3 > 0.30, pct(msr3), ">30%")
check("MSR breach — hard stop fires",
      any("MSR breach" in h["flag"] for h in s3["hard_stops"]),
      [h["flag"][:50] for h in s3["hard_stops"]], "MSR breach flag")

# Short lease — CPF proration flag
p4 = {**BASE, "citizenship": "SC", "age": 50, "cash": 200_000, "cpf_oa": 100_000,
      "monthly_income": 8_000, "property_price": 350_000,
      "property_type": "hdb", "is_hdb": True, "flat_type": "4 ROOM",
      "num_existing_properties": 0, "loan_tenure": 15, "holding_years": 10,
      "lease_remaining": 40}
s4 = score(p4, "live_in")
lsf4_flags = s4["dimensions"]["life_stage_fit"]["flags"]
check("Short lease 40yr — CPF proration flag fires",
      any("lease" in f.lower() and "95" in f for f in lsf4_flags),
      lsf4_flags, "CPF proration flag")

# Foreigner — ABSD 60% hammers opportunity cost
p5 = {**BASE, "citizenship": "FR", "age": 45, "cash": 3_000_000, "cpf_oa": 0,
      "monthly_income": 40_000, "property_price": 3_500_000,
      "property_type": "private", "is_hdb": False,
      "num_existing_properties": 0, "loan_tenure": 20, "holding_years": 5,
      "lease_remaining": 99}
s5 = score(p5, "invest")
oc5 = s5["dimensions"]["opportunity_cost"]["score"]
check("Foreigner ABSD 60% — opp cost <= 40", oc5 <= 40, f"{oc5}", "<=40")

# High income comfortable — should be 75+
p6 = {**BASE, "citizenship": "SC", "age": 38, "cash": 800_000, "cpf_oa": 300_000,
      "monthly_income": 25_000, "property_price": 1_800_000,
      "property_type": "private", "is_hdb": False,
      "num_existing_properties": 0, "loan_tenure": 25, "holding_years": 10,
      "lease_remaining": 99}
s6 = score(p6, "invest")
check("Comfortable profile — score >= 70",
      s6["overall_score"] >= 70, f"{s6['overall_score']}", ">=70")
check("Comfortable profile — no hard stops",
      len(s6["hard_stops"]) == 0, s6["hard_stops"], "[]")

# ── DIRECTION FILTER (all 6 directions + edge cases) ─────────────────────────
dir_cases = [
    ("East coast area marine parade",       _EAST_COAST_DISTRICTS, "east coast = D15/16"),
    ("near katong or siglap",               _EAST_COAST_DISTRICTS, "katong/siglap = D15/16"),
    ("looking in the east tampines",        _EAST_DISTRICTS,       "east broad = D14-19"),
    ("bedok pasir ris area",                 _EAST_DISTRICTS,       "bedok = East"),
    ("west side jurong clementi",           _WEST_DISTRICTS,       "jurong = West"),
    ("bukit panjang or bukit batok",        _WEST_DISTRICTS,       "bukit batok = West"),
    ("north woodlands yishun sembawang",    _NORTH_DISTRICTS,      "woodlands = North"),
    ("looking in the north",               _NORTH_DISTRICTS,       "north = North"),
    ("north east punggol sengkang",        _NORTHEAST_DISTRICTS,   "punggol = NE"),
    ("hougang north east area",            _NORTHEAST_DISTRICTS,   "hougang = NE"),
    ("central orchard cbd raffles",        _CENTRAL_DISTRICTS,     "orchard = Central"),
    ("city centre novena bishan",          _CENTRAL_DISTRICTS,     "novena/bishan = Central"),
    ("good mrt no preference",             None,                   "no direction = None"),
    ("budget 700k near MRT",               None,                   "no location = None"),
]
for q, exp, label in dir_cases:
    got = _parse_district_filter(q)
    check(f"Direction: {label}", got == exp,
          str(sorted(got) if got else None),
          str(sorted(exp) if exp else None))


# ── RESULTS ───────────────────────────────────────────────────────────────────
print("\nPropSense QA Suite")
print("=" * 70)
passed = sum(1 for ok, *_ in results if ok)
for ok, label, got, expected in results:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {label}")
    if not ok:
        print(f"       Got:      {got}")
        print(f"       Expected: {expected}")
print("=" * 70)
print(f"\n{passed}/{len(results)} passed\n")
if not all_pass:
    sys.exit(1)
