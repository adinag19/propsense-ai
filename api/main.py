"""
PropSense.AI FastAPI backend.

Endpoints:
  GET  /health          — liveness check
  POST /score           — full RAG + score + narrative pipeline
  POST /score/quick     — score only (no LLM narrative), faster

Auth: Bearer token via Authorization header.
Rate limit: 10 req/min per IP (slowapi).

Usage:
    uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
"""
import json
import os
import time
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from api.filters import mask_pii_in_dict, sanitize_query
from rag.retriever import format_context, retrieve
from scoring.readiness import compute_score

load_dotenv()

BEARER_TOKEN = os.getenv("API_BEARER_TOKEN", "")
MAX_QUERY_LENGTH = 2000

_HDB_CSV          = os.path.join(os.path.dirname(__file__), "..", "data", "processed", "hdb_clean.csv")
_AMENITIES_CSV    = os.path.join(os.path.dirname(__file__), "..", "data", "processed", "area_amenities.csv")
_HDB_SUMMARY_CSV  = os.path.join(os.path.dirname(__file__), "..", "data", "processed", "hdb_area_summary.csv")
_URA_SUMMARY_CSV  = os.path.join(os.path.dirname(__file__), "..", "data", "processed", "ura_area_summary.csv")
_hdb_summary:     "pd.DataFrame | None" = None
_amenities_df:    "pd.DataFrame | None" = None
_hdb_summary_pre: "pd.DataFrame | None" = None
_ura_summary_pre: "pd.DataFrame | None" = None


def _load_amenities() -> pd.DataFrame:
    global _amenities_df
    if _amenities_df is None:
        _amenities_df = pd.read_csv(_AMENITIES_CSV)
    return _amenities_df


def _load_hdb_area_summary() -> pd.DataFrame:
    """Pre-computed HDB area summary — small file, always available on Render."""
    global _hdb_summary_pre
    if _hdb_summary_pre is None:
        _hdb_summary_pre = pd.read_csv(_HDB_SUMMARY_CSV)
    return _hdb_summary_pre


def _load_ura_area_summary() -> pd.DataFrame:
    """Pre-computed URA private condo district summary."""
    global _ura_summary_pre
    if _ura_summary_pre is None:
        _ura_summary_pre = pd.read_csv(_URA_SUMMARY_CSV)
    return _ura_summary_pre


def _load_hdb_summary() -> pd.DataFrame:
    """Compute median resale price, PSF, CAGR and avg lease per town+flat_type (cached)."""
    global _hdb_summary
    if _hdb_summary is not None:
        return _hdb_summary

    df = pd.read_csv(_HDB_CSV)

    # Recency-weighted approach:
    #   base  = 2022–2023 (post-cooling measure baseline — more representative than 2019-2021 peak)
    #   recent = 2024–2026 (current market — what buyers are actually paying now)
    base = df[df["year"].between(2022, 2023)]
    recent = df[df["year"] >= 2024]

    def _median(grp: pd.DataFrame, col: str) -> pd.DataFrame:
        return grp.groupby(["town", "flat_type"])[col].median().reset_index()

    base_psf = _median(base, "price_per_sqft").rename(columns={"price_per_sqft": "base_psf"})
    recent_psf = _median(recent, "price_per_sqft").rename(columns={"price_per_sqft": "latest_psf"})
    recent_price = _median(recent, "resale_price").rename(columns={"resale_price": "median_price"})
    avg_lease = _median(recent, "remaining_lease_years").rename(columns={"remaining_lease_years": "avg_lease_years"})

    summary = recent_psf \
        .merge(base_psf, on=["town", "flat_type"], how="left") \
        .merge(recent_price, on=["town", "flat_type"], how="left") \
        .merge(avg_lease, on=["town", "flat_type"], how="left")

    # CAGR over 2 years (2022-2023 base midpoint → 2024-2026 recent midpoint)
    summary["psf_cagr_pct"] = (
        ((summary["latest_psf"] / summary["base_psf"]) ** (1 / 2) - 1) * 100
    ).fillna(0).round(1)

    _hdb_summary = summary
    return _hdb_summary

# ── Rate limiter ──────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="PropSense.AI", version="1.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── Global LLM budget circuit breaker ────────────────────────────────────────
# Caps total LLM (narrative) calls per calendar day across ALL IPs.
# Resets on server restart or at next UTC midnight check.
# Set MAX_DAILY_LLM_CALLS=0 in .env to disable narrative entirely.
import threading
from datetime import date as _date

_MAX_DAILY_LLM_CALLS = int(os.getenv("MAX_DAILY_LLM_CALLS", "200"))
_llm_call_lock = threading.Lock()
_llm_calls_today = 0
_llm_calls_date = _date.today()


def _llm_budget_available() -> bool:
    """Return True if we are under the daily LLM call limit."""
    global _llm_calls_today, _llm_calls_date
    if _MAX_DAILY_LLM_CALLS == 0:
        return False
    with _llm_call_lock:
        today = _date.today()
        if today != _llm_calls_date:
            _llm_calls_today = 0
            _llm_calls_date = today
        return _llm_calls_today < _MAX_DAILY_LLM_CALLS


def _llm_budget_consume() -> int:
    """Increment counter, return new count."""
    global _llm_calls_today
    with _llm_call_lock:
        _llm_calls_today += 1
        return _llm_calls_today

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ── Auth ──────────────────────────────────────────────────────────────────────
security = HTTPBearer(auto_error=False)


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not BEARER_TOKEN:
        return  # no token configured → open (dev mode)
    if not credentials or credentials.credentials != BEARER_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Bearer token",
        )


# ── Request schema ────────────────────────────────────────────────────────────

class ScoreRequest(BaseModel):
    # Natural language context (optional — used to retrieve relevant chunks)
    query: str = Field(
        default="",
        max_length=MAX_QUERY_LENGTH,
        description="Describe the property or investment question",
    )

    # Buyer profile
    citizenship: str = Field(default="SC", description="SC | PR | FR")
    age: int = Field(default=35, ge=21, le=75)
    cash: float = Field(default=200_000, ge=0, description="Liquid cash in SGD")
    cpf_oa: float = Field(default=80_000, ge=0, description="CPF OA balance in SGD")
    monthly_income: float = Field(default=8_000, ge=1)
    monthly_obligations: float = Field(default=0, ge=0, description="Existing loan repayments/month")

    # Property
    property_price: float = Field(default=1_000_000, ge=100_000)
    property_type: str = Field(default="private", description="hdb | private")
    is_hdb: bool = Field(default=False)
    flat_type: str = Field(default="", description="HDB flat type e.g. '4 ROOM'")
    district: str = Field(default="", description="URA district number e.g. '09'")
    planning_area: str = Field(default="", description="e.g. 'Tampines'")

    # Loan
    loan_tenure: int = Field(default=25, ge=5, le=35)
    annual_sora: float = Field(default=0.037, ge=0, le=0.15)
    bank_spread: float = Field(default=0.011, ge=0, le=0.05)

    # Ownership history
    num_existing_properties: int = Field(default=0, ge=0, le=5)
    existing_hdb: bool = Field(default=False)

    # Planning
    holding_years: int = Field(default=10, ge=1, le=30)
    lease_remaining: int = Field(default=99, ge=1, le=99)

    purpose: str = Field(default="invest", description="invest | live_in")

    include_narrative: bool = Field(
        default=False,
        description="Set True to include LLM narrative (costs tokens — opt-in only)",
    )


# ── Pipeline helpers ──────────────────────────────────────────────────────────

# District numbers by geography — Singapore-specific
_EAST_COAST_DISTRICTS = {15, 16}               # Marine Parade, East Coast, Bedok — the literal "East Coast" strip
_EAST_DISTRICTS    = {14, 15, 16, 17, 18, 19}   # Broader East: Geylang, Marine Parade, Bedok, Changi, Tampines/Pasir Ris, Hougang
_WEST_DISTRICTS    = {5, 22, 23}                 # Clementi, Jurong, Bukit Batok/Panjang/CCK
_NORTH_DISTRICTS   = {25, 26, 27, 28}            # Woodlands, Mandai, Sembawang/Yishun, Seletar
_CENTRAL_DISTRICTS = {1, 2, 6, 7, 8, 9, 10, 11} # CCR
_NORTHEAST_DISTRICTS = {19, 20}                  # Hougang/Sengkang/Punggol/Bishan

def _parse_district_filter(query: str) -> set[int] | None:
    """Parse location keywords from free text → set of allowed district numbers, or None (no filter)."""
    q = query.lower()
    # Pad with spaces so " east " won't match inside "northeast"
    p = f" {q} "

    # Named areas — most specific, checked first regardless of direction words
    NE_NAMES  = ["sengkang", "punggol", "hougang"]
    E_NAMES   = ["bedok", "tampines", "pasir ris", "marine parade", "geylang"]
    W_NAMES   = ["jurong", "clementi", "bukit batok", "bukit panjang", "choa chu kang"]
    N_NAMES   = ["woodlands", "yishun", "sembawang"]
    C_NAMES   = ["orchard", "raffles", "novena", "bishan"]

    if any(n in p for n in NE_NAMES) or "north east" in p or "northeast" in p or "north-east" in p:
        return _NORTHEAST_DISTRICTS
    # "East coast" specifically = D15-D16 (Marine Parade, East Coast Rd, Bedok)
    if "east coast" in p or "marine parade" in p or "siglap" in p or "katong" in p:
        return _EAST_COAST_DISTRICTS
    if any(n in p for n in E_NAMES) or "east side" in p or "eastern" in p or " east " in p:
        return _EAST_DISTRICTS
    if any(n in p for n in W_NAMES) or "west coast" in p or "west side" in p or "western" in p or " west " in p:
        return _WEST_DISTRICTS
    if any(n in p for n in N_NAMES) or "northern" in p or " north " in p:
        return _NORTH_DISTRICTS
    if any(n in p for n in C_NAMES) or "central" in p or " cbd " in p or "city centre" in p or "city center" in p:
        return _CENTRAL_DISTRICTS
    return None


def _parse_region_filter(query: str) -> list[str] | None:
    """Legacy wrapper — returns URA region codes. Use _parse_district_filter for accurate geo filtering."""
    d = _parse_district_filter(query)
    if d is None:
        return None
    if d == _CENTRAL_DISTRICTS:
        return ["CCR"]
    return ["OCR", "RCR"]  # east/west/north are all outside CCR


def _planning_area_to_district_str(planning_area: str) -> str:
    """Return 'District XX' string if planning area maps to a known district.
    Resolves sub-areas: Katong->Marine Parade (D15), Tiong Bahru->Bukit Merah (D3), etc.
    """
    area = planning_area.strip().upper()
    area = _SUBAREA_TO_PLANNING_AREA.get(area, area)
    districts = _AREA_TO_DISTRICTS.get(area.upper(), [])
    if districts:
        return f"District {districts[0]:02d}"
    return ""


def _build_retrieval_query(req: ScoreRequest) -> str:
    """Build a targeted retrieval query from the request."""
    parts = []
    if req.query:
        parts.append(req.query)
    if req.planning_area:
        parts.append(req.planning_area)
    # Always include district number for private condo so URA chunks match
    if req.district:
        parts.append(f"District {req.district}")
    elif req.planning_area and not req.is_hdb:
        d_str = _planning_area_to_district_str(req.planning_area)
        if d_str:
            parts.append(d_str)
    if req.is_hdb:
        parts.append("HDB resale")
    else:
        parts.append("private condo")
    return " ".join(parts) or "Singapore property investment"


def _retrieve_all(req: ScoreRequest) -> list[dict]:
    """Retrieve relevant chunks across all applicable data sources."""
    query = _build_retrieval_query(req)
    chunks: list[dict] = []

    source_queries: list[tuple[str | None, str, int]] = [
        (None,     query,                        5),
        ("policy", "ABSD TDSR MSR LTV BSD CPF", 3),
    ]

    if req.is_hdb:
        area = req.planning_area or "HDB resale"
        source_queries.append(("hdb", f"HDB {area} resale prices", 4))
    else:
        # Use district number in the URA query for accurate chunk retrieval
        if req.planning_area:
            d_str = _planning_area_to_district_str(req.planning_area)
        elif req.district:
            d_str = f"District {req.district}"
        else:
            d_str = "condo"
        ura_query = f"PSF {d_str} condo" if d_str else "PSF condo"
        source_queries.append(("ura_transaction", ura_query, 4))
        source_queries.append(("ura_rental",      f"rental yield {d_str}", 3))

    seen_ids: set[str] = set()
    for source_filter, q, top_k in source_queries:
        result = retrieve(q, top_k=top_k, source_filter=source_filter)
        for chunk in result["chunks"]:
            cid = chunk.get("text", "")[:50]
            if cid not in seen_ids:
                seen_ids.add(cid)
                chunks.append(chunk)

    return chunks


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/parse-query")
@limiter.limit("20/minute")
async def parse_query(
    request: Request,
    body: dict = Body(...),
    _: None = Depends(verify_token),
):
    """
    Use GPT-4o-mini to extract a structured buyer profile from natural language.
    Returns partial profile fields — only the ones mentioned in the query.
    """
    query = str(body.get("query", "")).strip()[:500]
    if not query:
        return {"extracted": {}, "confidence": "none"}

    system_prompt = """You are a Singapore property advisor assistant. Extract structured buyer profile fields from natural language.

Return ONLY valid JSON with any of these fields (omit fields not mentioned):
{
  "citizenship": "SC" | "PR" | "FR",
  "cash": number (SGD savings/cash, convert "350k" → 350000),
  "cpf_oa": number (CPF OA balance if mentioned),
  "monthly_income": number (monthly income, convert annual if needed),
  "property_type": "hdb" | "private" | "landed",
  "flat_type": "3 ROOM" | "4 ROOM" | "5 ROOM" | "EXECUTIVE" (HDB only),
  "planning_area": string (ONLY if a specific named area is given e.g. "Tampines", "Queenstown", "Bishan" — NOT directions like "East coast", "north", "west"),
  "location_hint": string (directional hint ONLY: "East", "West", "North", "North East", "Central"),
  "num_bedrooms": number (2, 3, 4, 5),
  "purpose": "live_in" | "invest",
  "property_price": number (if specific price mentioned),
  "loan_tenure": number (bank loan repayment period ONLY — e.g. "25 year loan", "30 year mortgage"),
  "holding_years": number (how long they plan to HOLD/LIVE IN the property — "planning to live there X years", "X year horizon", "stay for X years"),
  "age": number,
  "num_existing_properties": number
}

Rules:
- "PR" = permanent resident, "SC" = Singapore citizen, "foreigner"/"expat" = "FR"
- "4-room", "4 room", "4rm" → flat_type "4 ROOM", property_type "hdb"
- "condo", "condominium", "private" → property_type "private"
- "east coast", "eastern", " east" → location_hint "East"
- "jurong", " west" → location_hint "West"
- "north ", "woodlands", "yishun", "sembawang" → location_hint "North"
- CRITICAL: planning_area must be a real Singapore planning area name (Tampines, Queenstown, Bishan etc). NEVER set it to "East coast", "east", "north", "west" or any directional description — those go in location_hint instead
- CRITICAL: "planning to live there for X years" / "X year horizon" / "stay X years" = holding_years NOT loan_tenure
- loan_tenure = only explicit loan/mortgage period. Default is 25 years — do NOT set it from living duration
- If savings/cash mentioned without specifying CPF, put in "cash"
- If user says "no CPF", "0 CPF", "no cpf savings", set cpf_oa to 0 explicitly
- Only include fields you are confident about
- Return {} if nothing extractable"""

    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=300,
            temperature=0,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": query},
            ],
        )
        raw = resp.choices[0].message.content.strip()
        import re as _re
        m = _re.search(r"\{[\s\S]*\}", raw)
        extracted = json.loads(m.group()) if m else {}
    except Exception as exc:
        return {"extracted": {}, "error": str(exc)}

    return {"extracted": extracted, "query": query}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "propsense-api", "version": "1.0.0"}


_AREA_CENTROIDS: dict[str, tuple[float, float]] = {
    "Ang Mo Kio": (1.3691, 103.8454), "Bedok": (1.3236, 103.9273),
    "Bishan": (1.3526, 103.8352), "Bukit Batok": (1.3590, 103.7637),
    "Bukit Merah": (1.2819, 103.8239), "Bukit Panjang": (1.3774, 103.7719),
    "Bukit Timah": (1.3294, 103.8021), "Choa Chu Kang": (1.3840, 103.7470),
    "Clementi": (1.3162, 103.7649), "Geylang": (1.3201, 103.8818),
    "Hougang": (1.3612, 103.8863), "Jurong East": (1.3329, 103.7436),
    "Jurong West": (1.3404, 103.7090), "Kallang": (1.3100, 103.8640),
    "Marine Parade": (1.3020, 103.8967), "Novena": (1.3203, 103.8431),
    "Pasir Ris": (1.3721, 103.9494), "Punggol": (1.4043, 103.9022),
    "Queenstown": (1.2942, 103.7861), "Sembawang": (1.4491, 103.8185),
    "Sengkang": (1.3868, 103.8914), "Serangoon": (1.3554, 103.8679),
    "Tampines": (1.3496, 103.9568), "Toa Payoh": (1.3343, 103.8563),
    "Woodlands": (1.4382, 103.7866), "Yishun": (1.4304, 103.8354),
    "Downtown Core": (1.2789, 103.8536), "Orchard": (1.3048, 103.8318),
    "River Valley": (1.2966, 103.8321), "Rochor": (1.3042, 103.8565),
}


_URA_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "processed", "ura_clean.csv")
_ura_df: "pd.DataFrame | None" = None

def _load_ura() -> pd.DataFrame:
    global _ura_df
    if _ura_df is None:
        _ura_df = pd.read_csv(_URA_CSV, low_memory=False)
    return _ura_df

# Planning area → URA district(s) mapping
_AREA_TO_DISTRICTS: dict[str, list[int]] = {
    "RAFFLES PLACE": [1], "SHENTON WAY": [2], "BUKIT MERAH": [3,4],
    "QUEENSTOWN": [3,4,10], "HARBOURFRONT": [4], "CLEMENTI": [5],
    "CITY HALL": [6,7], "ROCHOR": [7,8], "KALLANG": [12,13],
    "ORCHARD": [9,10], "RIVER VALLEY": [9], "BUKIT TIMAH": [10,21],
    "NEWTON": [11], "NOVENA": [11], "TANJONG PAGAR": [2,3],
    "TOA PAYOH": [12], "MACPHERSON": [13], "GEYLANG": [14],
    "MARINE PARADE": [15], "BEDOK": [16], "TAMPINES": [18],
    "PASIR RIS": [18], "SERANGOON": [19,20], "HOUGANG": [19],
    "SENGKANG": [19], "PUNGGOL": [19], "BISHAN": [20],
    "ANG MO KIO": [20], "BUKIT PANJANG": [23], "CHOA CHU KANG": [23],
    "JURONG EAST": [22], "JURONG WEST": [22], "CLEMENTI": [5],
    "WOODLANDS": [25,27], "YISHUN": [27], "SEMBAWANG": [27],
    "DOWNTOWN CORE": [1,2,6,7], "OUTRAM": [3],
}


_TXN_SUMMARY_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "processed", "transaction_summary.csv")
_txn_summary_df: "pd.DataFrame | None" = None

def _load_txn_summary() -> pd.DataFrame:
    global _txn_summary_df
    if _txn_summary_df is None:
        _txn_summary_df = pd.read_csv(_TXN_SUMMARY_CSV)
    return _txn_summary_df


@app.get("/transactions")
async def recent_transactions(
    planning_area: str = Query(...),
    property_type: str = Query(default="hdb"),
    flat_type: str = Query(default=""),
    years: int = Query(default=4, ge=1, le=8),
    _: None = Depends(verify_token),
):
    """Return recent transaction summary using pre-computed data."""
    planning_area_clean = planning_area.strip().upper()
    resolved = _SUBAREA_TO_PLANNING_AREA.get(planning_area_clean, planning_area_clean)

    try:
        df = _load_txn_summary()
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="Transaction data not available")

    if property_type == "hdb":
        mask = df["source"] == "hdb"
        mask &= df["area"].str.upper() == resolved
        if flat_type:
            mask &= df["flat_type"].str.upper() == flat_type.upper().strip()
        filtered = df[mask].nlargest(years, "year")

        if filtered.empty:
            return {"transactions": [], "note": f"No HDB data for {planning_area.title()}"}

        rows = []
        for _, row in filtered.iterrows():
            yr = int(row["year"])
            rows.append({
                "year": yr,
                "median_price": int(row["median_price"]),
                "median_psf": int(row["median_psf"]),
                "transactions": int(row["count"]),
                "min_price": int(row["min_price"]),
                "max_price": int(row["max_price"]),
                "note": f"Jan–May {yr} (partial)" if yr == 2026 else "",
            })

        flat_label = flat_type.title() if flat_type else "all flat types"
        return {
            "transactions": rows, "area": planning_area.title(),
            "property_type": "HDB Resale", "flat_type": flat_label,
            "source": "HDB Resale Transaction Registry",
        }

    else:
        source_val = "landed" if property_type == "landed" else "private"
        districts = _AREA_TO_DISTRICTS.get(resolved, [])
        if not districts:
            return {"transactions": [], "note": f"No district mapping for {planning_area.title()}"}

        mask = (df["source"] == source_val) & (df["district"].isin(districts))
        filtered = df[mask].groupby("year").agg(
            median_price=("median_price", "median"),
            median_psf=("median_psf", "median"),
            count=("count", "sum"),
            min_price=("min_price", "min"),
            max_price=("max_price", "max"),
        ).reset_index().nlargest(years, "year")

        if filtered.empty:
            return {"transactions": [], "note": f"No data for {planning_area.title()}"}

        rows = []
        for _, row in filtered.iterrows():
            yr = int(row["year"])
            rows.append({
                "year": yr,
                "median_price": int(row["median_price"]),
                "median_psf": int(row["median_psf"]),
                "transactions": int(row["count"]),
                "min_price": int(row["min_price"]),
                "max_price": int(row["max_price"]),
                "note": f"Jan–May {yr} (partial)" if yr == 2026 else "",
            })

        district_label = "/".join(f"D{d:02d}" for d in sorted(districts))
        return {
            "transactions": rows,
            "area": f"{planning_area.title()} ({district_label})",
            "property_type": "Landed (Terrace / Semi-D / Bungalow)" if property_type == "landed" else "Private Condo / Apartment",
            "flat_type": "",
            "source": "URA Private Residential Transactions 2021–2026",
        }


# Common sub-area names → parent planning area (for amenity lookup)
_SUBAREA_TO_PLANNING_AREA: dict[str, str] = {
    "KATONG": "Marine Parade", "SIGLAP": "Marine Parade",
    "TANJONG KATONG": "Marine Parade", "MARINE TERRACE": "Marine Parade",
    "EAST COAST": "Marine Parade",
    "TIONG BAHRU": "Bukit Merah", "TELOK BLANGAH": "Bukit Merah",
    "ALEXANDRA": "Queenstown", "DAWSON": "Queenstown",
    "COMMONWEALTH": "Queenstown", "STIRLING": "Queenstown",
    "DOVER": "Clementi", "WEST COAST": "Clementi",
    "HILLVIEW": "Bukit Batok", "UPPER BUKIT TIMAH": "Bukit Panjang",
    "DAKOTA": "Geylang", "ALJUNIED": "Geylang", "MACPHERSON": "Geylang",
    "KOVAN": "Serangoon", "LORONG CHUAN": "Serangoon",
    "COMPASSVALE": "Sengkang", "ANCHORVALE": "Sengkang",
    "FERNVALE": "Sengkang", "RIVERVALE": "Sengkang",
    "WATERWAY": "Punggol", "NORTHSHORE": "Punggol", "CANBERRA": "Sembawang",
    "BOON LAY": "Jurong West", "LAKESIDE": "Jurong East",
    "DHOBY GHAUT": "Rochor", "LITTLE INDIA": "Rochor", "FARRER PARK": "Rochor",
    "BUGIS": "Rochor", "BENDEMEER": "Kallang", "BRAS BASAH": "Downtown Core",
    "TANJONG PAGAR": "Downtown Core", "CITY HALL": "Downtown Core",
    "ORCHARD": "Orchard", "SCOTTS": "Orchard",
    "NEWTON": "Novena", "BALESTIER": "Toa Payoh",
    "UPPER THOMSON": "Bishan", "THOMSON": "Bishan",
    "BUONA VISTA": "Queenstown", "ONE NORTH": "Queenstown",
    "PASIR PANJANG": "Queenstown",
}


def _svy21_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    """Convert SVY21 (x=easting, y=northing) to WGS84 (lat, lon)."""
    import math
    a  = 6378137.0
    f  = 1.0 / 298.257223563
    b  = a * (1 - f)
    e2 = 1 - (b / a) ** 2

    lat0 = math.radians(1 + 22/60 + 2.9154/3600)
    lon0 = math.radians(103 + 49/60 + 31.9752/3600)
    FN, FE, k0 = 38744.572, 28001.642, 1.0

    e4, e6 = e2**2, e2**3
    M0 = a * (
        (1 - e2/4 - 3*e4/64 - 5*e6/256) * lat0
        - (3*e2/8 + 3*e4/32 + 45*e6/1024) * math.sin(2*lat0)
        + (15*e4/256 + 45*e6/1024) * math.sin(4*lat0)
        - 35*e6/3072 * math.sin(6*lat0)
    )
    M = M0 + (northing - FN) / k0
    mu = M / (a * (1 - e2/4 - 3*e4/64 - 5*e6/256))

    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (mu
            + (3*e1/2 - 27*e1**3/32) * math.sin(2*mu)
            + (21*e1**2/16 - 55*e1**4/32) * math.sin(4*mu)
            + (151*e1**3/96) * math.sin(6*mu)
            + (1097*e1**4/512) * math.sin(8*mu))

    N1 = a / math.sqrt(1 - e2 * math.sin(phi1)**2)
    T1 = math.tan(phi1)**2
    C1 = e2 / (1 - e2) * math.cos(phi1)**2
    R1 = a * (1 - e2) / (1 - e2 * math.sin(phi1)**2)**1.5
    D  = (easting - FE) / (N1 * k0)

    lat = phi1 - (N1 * math.tan(phi1) / R1) * (
        D**2/2
        - (5 + 3*T1 + 10*C1 - 4*C1**2 - 9*e2/(1-e2)) * D**4/24
        + (61 + 90*T1 + 298*C1 + 45*T1**2 - 252*e2/(1-e2) - 3*C1**2) * D**6/720
    )
    lon = lon0 + (
        D - (1 + 2*T1 + C1)*D**3/6
        + (5 - 2*C1 + 28*T1 - 3*C1**2 + 8*e2/(1-e2) + 24*T1**2)*D**5/120
    ) / math.cos(phi1)

    return round(math.degrees(lat), 6), round(math.degrees(lon), 6)


_PROJECT_LOC_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "processed", "project_locations.csv")
_project_loc_df: "pd.DataFrame | None" = None

def _load_project_locations() -> pd.DataFrame:
    global _project_loc_df
    if _project_loc_df is None:
        _project_loc_df = pd.read_csv(_PROJECT_LOC_CSV)
    return _project_loc_df


@app.get("/projects-geo")
async def projects_geo(
    planning_area: str = Query(...),
    _: None = Depends(verify_token),
):
    """Return pre-computed project-level markers for a planning area."""
    planning_area_upper = planning_area.strip().upper()
    resolved = _SUBAREA_TO_PLANNING_AREA.get(planning_area_upper, planning_area_upper)
    districts = _AREA_TO_DISTRICTS.get(resolved, [])
    if not districts:
        return {"projects": [], "note": "No district mapping for this planning area"}

    try:
        df = _load_project_locations()
    except FileNotFoundError:
        return {"projects": [], "note": "Project location data not available"}

    subset = df[df["district"].isin(districts)].copy()
    if subset.empty:
        return {"projects": [], "note": "No project data for this area"}

    subset = subset.nlargest(80, "txn_count")
    projects = []
    for _, row in subset.iterrows():
        projects.append({
            "project": str(row["project"]),
            "street": str(row["street"]),
            "lat": float(row["lat"]),
            "lng": float(row["lng"]),
            "median_psf_2024_26": int(row["median_psf"]) if pd.notna(row["median_psf"]) else None,
            "median_psf_all": int(row["median_psf"]) if pd.notna(row["median_psf"]) else None,
            "txn_count_2024_26": int(row["txn_count"]),
            "txn_count_all": int(row["txn_count"]),
            "latest_year": 2026,
            "property_type": str(row["property_type"]),
        })

    return {
        "projects": projects,
        "area": planning_area.title(),
        "districts": districts,
        "note": "Resale transactions only — new launches not included",
    }


_AREA_METRICS_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "processed", "area_metrics.csv")
_area_metrics_df: "pd.DataFrame | None" = None

def _load_area_metrics() -> pd.DataFrame:
    global _area_metrics_df
    if _area_metrics_df is None:
        _area_metrics_df = pd.read_csv(_AREA_METRICS_CSV)
    return _area_metrics_df


@app.get("/areas-geo")
async def areas_geo(_: None = Depends(verify_token)):
    """Return pre-computed area metrics for the Hot Areas Map."""
    try:
        df = _load_area_metrics()
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="Area metrics not available")

    areas = []
    for _, row in df.iterrows():
        areas.append({
            "planning_area": str(row["planning_area"]),
            "region": str(row.get("region", "OCR")),
            "lat": float(row["lat"]),
            "lng": float(row["lng"]),
            "psf_cagr_pct": round(float(row["psf_cagr_pct"]), 1),
            "gross_yield_pct": None,
            "latest_psf": int(round(row["latest_psf"])),
        })

    # Deduplicate by planning_area (take highest CAGR if multiple districts map to same area)
    seen: dict[str, dict] = {}
    for a in areas:
        name = a["planning_area"]
        if name not in seen or a["psf_cagr_pct"] > seen[name]["psf_cagr_pct"]:
            seen[name] = a

    return {"areas": list(seen.values())}


def _compute_max_price(
    monthly_income: float,
    cash: float,
    cpf_oa: float,
    annual_sora: float,
    bank_spread: float,
    loan_tenure: int,
    citizenship: str,
    property_type: str,
) -> tuple[float, str]:
    """Return (max_affordable_price, binding_constraint_label)."""
    rate = annual_sora + bank_spread
    n = loan_tenure * 12
    r = rate / 12

    # Servicing ratio limits
    msr_limit = 0.30 if property_type == "hdb" else None
    tdsr_limit = 0.55

    max_payment_msr = monthly_income * msr_limit if msr_limit else float("inf")
    max_payment_tdsr = monthly_income * tdsr_limit
    max_payment = min(max_payment_msr, max_payment_tdsr)

    # Max loan from servicing ratio
    if r > 0:
        max_loan = max_payment * (1 - (1 + r) ** (-n)) / r
    else:
        max_loan = max_payment * n

    max_price_from_income = max_loan / 0.75  # 75% LTV

    # Upfront cash needed per dollar of price
    absd_rate = {"SC": 0.0, "PR": 0.05, "FR": 0.60}.get(citizenship.upper(), 0.05)
    bsd_rate = 0.024   # approximate BSD on typical price range
    min_cash_down = 0.05  # 5% must be cash (bank loan rules)
    emergency = monthly_income * 6

    # CPF covers the remaining 20% down payment up to cpf_oa balance
    # cash outlay per price = 5% cash down + ABSD + BSD
    cash_per_unit_price = min_cash_down + absd_rate + bsd_rate
    available_cash = cash - emergency
    if available_cash <= 0:
        max_price_from_cash = 0.0
        constraint = "insufficient cash for emergency fund"
    else:
        max_price_from_cash = available_cash / cash_per_unit_price
        constraint = "cash + upfront costs"

    if max_price_from_income <= max_price_from_cash:
        constraint = "MSR limit (30%)" if property_type == "hdb" else "TDSR limit (55%)"
        max_price = max_price_from_income
    else:
        max_price = max_price_from_cash

    return round(max_price / 10_000) * 10_000, constraint


@app.get("/suggest-areas")
async def suggest_areas(
    monthly_income: float = Query(ge=1),
    cash: float = Query(ge=0),
    cpf_oa: float = Query(ge=0),
    annual_sora: float = Query(default=0.037),
    bank_spread: float = Query(default=0.011),
    loan_tenure: int = Query(default=25),
    citizenship: str = Query(default="SC"),
    property_type: str = Query(default="private"),
    flat_type: str = Query(default=""),
    query: str = Query(default=""),
    top_n: int = Query(default=5, ge=1, le=10),
    _: None = Depends(verify_token),
):
    """Compute max affordable price from profile, then return best matching areas."""
    max_price, constraint = _compute_max_price(
        monthly_income, cash, cpf_oa, annual_sora, bank_spread,
        loan_tenure, citizenship, property_type,
    )
    if max_price < 100_000:
        return {
            "suggestions": [],
            "max_affordable_price": max_price,
            "constraint": constraint,
            "note": "Based on your profile, affordable price is below $100,000. Review your income or savings.",
        }

    district_filter = _parse_district_filter(query) if query else None

    if property_type == "hdb":
        result = _suggest_hdb(max_price, top_n, flat_type.upper().strip() or None, district_filter)
    else:
        result = _suggest_private(max_price, top_n, district_filter=district_filter)

    result["max_affordable_price"] = int(max_price)
    result["constraint"] = constraint
    return result


def _rank_and_slice(df: pd.DataFrame, score_col: str, top_n: int) -> pd.DataFrame:
    if len(df) > 1:
        rng = df[score_col].max() - df[score_col].min()
        df = df.copy()
        df["_norm"] = (df[score_col] - df[score_col].min()) / (rng or 1)
    else:
        df = df.copy()
        df["_norm"] = 1.0
    return df.nlargest(top_n, "_norm")


# HDB town → approximate district for geo filtering
_HDB_TOWN_DISTRICT: dict[str, int] = {
    "ANG MO KIO": 20, "BEDOK": 16, "BISHAN": 20, "BUKIT BATOK": 23,
    "BUKIT MERAH": 3, "BUKIT PANJANG": 23, "BUKIT TIMAH": 21,
    "CHOA CHU KANG": 23, "CLEMENTI": 5, "GEYLANG": 14,
    "HOUGANG": 19, "JURONG EAST": 22, "JURONG WEST": 22,
    "KALLANG": 12, "MARINE PARADE": 15, "NOVENA": 11,
    "PASIR RIS": 18, "PUNGGOL": 19, "QUEENSTOWN": 3,
    "SEMBAWANG": 27, "SENGKANG": 19, "SERANGOON": 19,
    "TAMPINES": 18, "TOA PAYOH": 12, "WOODLANDS": 25,
    "YISHUN": 27, "CENTRAL AREA": 1,
}


def _suggest_hdb(property_price: float, top_n: int, flat_type: str | None = None,
                 district_filter: set[int] | None = None) -> dict:
    try:
        summary = _load_hdb_area_summary()
    except FileNotFoundError:
        try:
            summary = _load_hdb_summary()  # fallback to full CSV if available locally
        except FileNotFoundError:
            raise HTTPException(status_code=503, detail="HDB data not available")

    if flat_type:
        pool = summary[summary["flat_type"].str.upper() == flat_type]
        if pool.empty:
            pool = summary
    else:
        pool = summary

    mask = (
        (pool["median_price"] >= property_price * 0.6) &
        (pool["median_price"] <= property_price * 1.4) &
        (pool["avg_lease_years"] >= 55)
    )
    filtered = pool[mask].copy()
    if filtered.empty:
        filtered = pool[pool["avg_lease_years"] >= 55].copy()

    # Apply geo filter if specified
    if district_filter and not filtered.empty:
        geo_mask = filtered["town"].map(
            lambda t: _HDB_TOWN_DISTRICT.get(str(t).upper(), 0) in district_filter
        )
        geo_filtered = filtered[geo_mask]
        if not geo_filtered.empty:
            filtered = geo_filtered  # only apply if it gives results

    if len(filtered) > 1:
        cagr_r = filtered["psf_cagr_pct"].max() - filtered["psf_cagr_pct"].min()
        lease_r = filtered["avg_lease_years"].max() - filtered["avg_lease_years"].min()
        filtered["_score"] = (
            0.7 * (filtered["psf_cagr_pct"] - filtered["psf_cagr_pct"].min()) / (cagr_r or 1) +
            0.3 * (filtered["avg_lease_years"] - filtered["avg_lease_years"].min()) / (lease_r or 1)
        )
    else:
        filtered["_score"] = 1.0

    top = filtered.nlargest(top_n, "_score")
    suggestions = []
    for _, row in top.iterrows():
        suggestions.append({
            "planning_area": str(row["town"]).title(),
            "flat_type": str(row["flat_type"]),
            "region": "",
            "latest_psf": int(round(row["latest_psf"])),
            "estimated_price": int(round(float(row["median_price"]) / 10_000) * 10_000),
            "psf_cagr_pct": round(float(row["psf_cagr_pct"]), 1),
            "gross_yield_pct": None,
            "avg_lease_years": int(round(row["avg_lease_years"])),
            "mrt_dist_m": None, "cbd_dist_km": None,
            "school_dist_m": None, "mall_dist_m": None, "park_dist_m": None,
        })

    return {
        "suggestions": suggestions,
        "note": "Ranked by price appreciation (70%) + lease health (30%). Source: HDB resale 2024–2026.",
        "data_source": "HDB Resale Transaction Registry",
    }


def _suggest_private(property_price: float, top_n: int,
                     region_filter: list[str] | None = None,
                     district_filter: set[int] | None = None) -> dict:
    """Suggest private condo areas using pre-computed area summary."""
    # Load pre-computed area metrics (always available on Render)
    am = _load_area_metrics()  # has planning_area, lat, lng, psf_cagr_pct, latest_psf, region
    ura_sum = _load_ura_area_summary()  # has district, median_price, median_psf

    # Invert district → area mapping
    district_to_area: dict[int, str] = {}
    for area, districts in _AREA_TO_DISTRICTS.items():
        for d in districts:
            if d not in district_to_area:
                district_to_area[d] = area.title()

    # Build district→price lookup from ura_area_summary
    price_by_district = dict(zip(ura_sum["district"], ura_sum["median_price"]))
    psf_by_district = dict(zip(ura_sum["district"], ura_sum["median_psf"]))

    rows = []
    for _, am_row in am.iterrows():
        area_name = str(am_row["planning_area"])
        districts = _AREA_TO_DISTRICTS.get(area_name.upper(), [])
        if not districts:
            continue
        d = districts[0]
        med_price = price_by_district.get(d)
        if not med_price:
            continue
        med_price = int(round(float(med_price) / 10_000) * 10_000)

        # Hard budget cap
        if med_price > property_price * 1.15:
            continue

        region = str(am_row.get("region", "OCR"))

        # District-level geo filter
        if district_filter and d not in district_filter:
            continue
        # Fallback region filter
        if region_filter and not district_filter and region not in region_filter:
            continue

        cagr = round(float(am_row.get("psf_cagr_pct", 0)), 1)
        latest_psf = int(round(float(am_row.get("latest_psf", psf_by_district.get(d, 0)))))
        rows.append({
            "planning_area": area_name,
            "flat_type": None,
            "region": region,
            "latest_psf": latest_psf,
            "estimated_price": med_price,
            "psf_cagr_pct": cagr,
            "gross_yield_pct": None,
            "avg_lease_years": None,
            "mrt_dist_m": None, "cbd_dist_km": None,
            "school_dist_m": None, "mall_dist_m": None, "park_dist_m": None,
            "_cagr": cagr,
        })

    if not rows:
        return {"suggestions": [], "note": "No areas match your budget range."}

    rows.sort(key=lambda x: x["_cagr"], reverse=True)
    # Deduplicate by planning_area
    seen: set[str] = set()
    suggestions = []
    for r in rows:
        if r["planning_area"] not in seen:
            seen.add(r["planning_area"])
            r.pop("_cagr")
            suggestions.append(r)
        if len(suggestions) >= top_n:
            break

    return {
        "suggestions": suggestions,
        "note": "Ranked by price appreciation. Source: URA private residential 2024–2026.",
        "data_source": "URA Private Residential Transactions",
    }


@app.post("/score")
@limiter.limit("10/minute;100/day")
async def score_endpoint(
    request: Request,
    body: ScoreRequest = Body(...),
    _: None = Depends(verify_token),
):
    if len(body.query) > MAX_QUERY_LENGTH:
        raise HTTPException(status_code=400, detail="Query too long (max 2000 chars)")

    t0 = time.perf_counter()

    # Sanitise string inputs
    body.citizenship = body.citizenship.upper().strip()

    # Prompt injection detection on free-text query
    body.query, injection_flagged = sanitize_query(body.query)
    if body.citizenship not in ("SC", "PR", "FR"):
        raise HTTPException(status_code=400, detail="citizenship must be SC, PR, or FR")

    # Retrieve context
    chunks = _retrieve_all(body)
    sufficient = len(chunks) >= 3

    # Deterministic score
    profile = body.model_dump(
        include={
            "citizenship", "age", "cash", "cpf_oa", "monthly_income",
            "monthly_obligations", "property_price", "property_type",
            "is_hdb", "flat_type", "district", "planning_area", "loan_tenure",
            "annual_sora", "bank_spread", "num_existing_properties",
            "existing_hdb", "holding_years", "lease_remaining", "purpose",
        }
    )

    # Inject real MRT + school distances from amenities CSV
    if body.planning_area:
        try:
            am = _load_amenities()
            lookup_area = body.planning_area.strip()
            # Resolve sub-areas to parent planning area for amenity lookup
            lookup_area = _SUBAREA_TO_PLANNING_AREA.get(lookup_area.upper(), lookup_area)
            row = am[am["planning_area"].str.lower() == lookup_area.lower()]
            if len(row) > 0:
                r = row.iloc[0]
                if pd.notna(r.get("mrt_dist_m")):
                    profile["mrt_dist_m"]     = int(r["mrt_dist_m"])
                    profile["mrt_name"]        = str(r["mrt_name"])
                if pd.notna(r.get("school_dist_m")):
                    profile["school_dist_m"]   = int(r["school_dist_m"])
                    profile["school_name"]      = str(r["school_name"])
        except Exception:
            pass

    # Inject pre-computed area benchmark — uses small committed CSVs (works on Render)
    if body.planning_area:
        try:
            if body.is_hdb:
                hdb_s = _load_hdb_area_summary()
                flat_t = (body.flat_type or "").upper().strip()
                mask = hdb_s["town"].str.upper() == body.planning_area.strip().upper()
                if flat_t:
                    mask &= hdb_s["flat_type"].str.upper() == flat_t
                row = hdb_s[mask]
                if len(row) > 0:
                    profile["area_median_price_2024_26"] = int(row["median_price"].median())
                    profile["area_median_psf_2024_26"] = int(row["latest_psf"].median())
            else:
                lookup = _SUBAREA_TO_PLANNING_AREA.get(
                    body.planning_area.strip().upper(), body.planning_area.strip()
                )
                districts = _AREA_TO_DISTRICTS.get(lookup.upper(), [])
                if districts:
                    ura_s = _load_ura_area_summary()
                    row = ura_s[ura_s["district"].isin(districts)]
                    if len(row) > 0:
                        profile["area_median_price_2024_26"] = int(row["median_price"].median())
                        profile["area_median_psf_2024_26"] = int(row["median_psf"].median())
        except Exception:
            pass

    score_result = compute_score(profile, chunks, purpose=body.purpose)

    # LLM narrative (optional) — gated by global daily budget
    if body.include_narrative and sufficient:
        if not _llm_budget_available():
            score_result["narrative_error"] = "Daily LLM budget exhausted — narrative unavailable until tomorrow"
        else:
            try:
                from llm.narrator import narrate
                score_result = narrate(score_result, chunks)
                _llm_budget_consume()
            except Exception as exc:
                score_result["narrative_error"] = str(exc)

    # LLM-as-judge: validate narrative on high-risk outputs
    try:
        from llm.judge import judge
        judge_result = judge(score_result, profile)
        if judge_result["triggered"]:
            score_result["judge"] = {
                "triggered": True,
                "reasons": judge_result["reasons"],
                "contradiction": judge_result["contradiction"],
                "issue": judge_result["issue"],
            }
    except Exception:
        pass

    # Auto-suggest areas when no planning_area provided
    if not body.planning_area.strip():
        try:
            max_price, constraint = _compute_max_price(
                body.monthly_income, body.cash, body.cpf_oa,
                body.annual_sora, body.bank_spread, body.loan_tenure,
                body.citizenship, body.property_type,
            )
            district_filter = _parse_district_filter(body.query) if body.query else None
            if body.is_hdb:
                flat_t = getattr(body, "flat_type", "") or ""
                area_result = _suggest_hdb(max_price, 3,
                                           flat_type=flat_t.upper().strip() or None,
                                           district_filter=district_filter)
            else:
                area_result = _suggest_private(max_price, 3, district_filter=district_filter)
            score_result["suggested_areas"] = area_result.get("suggestions", [])
            score_result["max_affordable_price"] = int(max_price)
            score_result["afford_constraint"] = constraint
        except Exception:
            score_result["suggested_areas"] = []

    elapsed = round(time.perf_counter() - t0, 3)

    response = {
        **score_result,
        "context_chunks_used": len(chunks),
        "sufficient_context": sufficient,
        "elapsed_seconds": elapsed,
        **({"injection_attempt_detected": True} if injection_flagged else {}),
    }

    # PII filter before returning
    response = mask_pii_in_dict(response)
    return JSONResponse(content=response)


@app.post("/score/quick")
@limiter.limit("20/minute;200/day")
async def score_quick(
    request: Request,
    body: ScoreRequest = Body(...),
    _: None = Depends(verify_token),
):
    """Score-only endpoint — no LLM call, ~10x faster."""
    body.include_narrative = False
    return await score_endpoint(request, body, _)
