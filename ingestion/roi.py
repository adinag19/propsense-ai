"""
Area Investment Profile ingestion pipeline.

Combines four data sources into one rich chunk per planning area:

  1. Amenity distances (Master Data sheet, Excel) — MRT, CBD, school, mall, park
  2. Current PSF trend 2021-2026 (data/processed/ura_clean.csv) via district mapping
  3. Rental yield estimate (data/processed/ura_rental_clean.csv) via district mapping
  4. PSF forecast direction 2024-2026 (Reco_Engine sheet, Excel)

Excel: C:/Users/gadea/Downloads/Real_Estate_1909 (Autosaved).xlsm
Output: data/processed/roi_chunks.csv  (one row per planning area)

Usage:
    python -m ingestion.roi
"""
from __future__ import annotations

import warnings
import statistics
from collections import defaultdict
from pathlib import Path

import openpyxl
import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

EXCEL_PATH = Path(r"C:\Users\gadea\Downloads\Real_Estate_1909 (Autosaved).xlsm")
PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"

MODEL_VINTAGE = "2022"

# MRT distance tiers (meters)
MRT_TIERS = [(500, "Excellent (≤500m)"), (1000, "Good (500-1000m)"),
             (1500, "Moderate (1-1.5km)"), (float("inf"), "Low (>1.5km)")]

# PSF years to include in chunk from URA data
PSF_YEARS = [2021, 2022, 2023, 2024, 2025]

# Forecast years from Reco_Engine
FORECAST_YEARS = [2023, 2024, 2025, 2026]

# BHK labels for forecast section
BHK_LABELS = {1: "1-bed", 2: "2-bed", 3: "3-bed", 4: "4-bed"}


# ── Excel loaders ─────────────────────────────────────────────────────────────

def _load_amenities_and_mapping(wb: openpyxl.Workbook) -> tuple[dict, dict]:
    """
    From Master Data:
      - Returns amenities: {planning_area: {metric: avg_meters}}
      - Returns district_map: {district_str: dominant_planning_area}
    """
    ws = wb["Master Data"]
    header = [str(c) if c else "" for c in next(ws.iter_rows(max_row=1, values_only=True))]

    nd_cols = {col: i for i, col in enumerate(header) if col.startswith("ND-")}
    pa_col   = header.index("Planning Area")
    pa_reg_col = header.index("Planning Region")
    dist_col = header.index("Postal District")

    amenity_data: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {col: [] for col in nd_cols}
    )
    region_map: dict[str, str] = {}
    district_votes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for row in ws.iter_rows(min_row=2, values_only=True):
        pa   = row[pa_col]
        reg  = row[pa_reg_col]
        dist = row[dist_col]
        if not pa:
            continue
        pa = str(pa)
        if reg:
            region_map[pa] = str(reg)
        if dist:
            district_votes[str(dist)][pa] += 1
        for col, idx in nd_cols.items():
            v = row[idx]
            try:
                amenity_data[pa][col].append(float(v))
            except (TypeError, ValueError):
                pass

    # Aggregate to means
    amenities: dict[str, dict[str, float]] = {}
    for pa, cols in amenity_data.items():
        amenities[pa] = {col: statistics.mean(vals) for col, vals in cols.items() if vals}

    # Build PA → dominant district mapping (inverted)
    # For each planning area, find which district has the most transactions
    pa_to_district: dict[str, str] = {}
    # district_votes is {district: {pa: count}} — invert it
    pa_dist_votes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for dist, pa_counts in district_votes.items():
        for pa, cnt in pa_counts.items():
            pa_dist_votes[pa][dist] += cnt
    for pa, dist_counts in pa_dist_votes.items():
        pa_to_district[pa] = max(dist_counts, key=dist_counts.get)

    return amenities, pa_to_district, region_map


def _load_reco_engine(wb: openpyxl.Workbook) -> dict[str, dict]:
    """
    Reco_Engine: PSF forecasts 2017-2030 by planning area + BHK.
    Returns {planning_area: {bhk: {year: psf}}}
    """
    ws = wb["Reco_Engine"]
    rows = list(ws.iter_rows(values_only=True))
    header = rows[1]

    year_col: dict[int, int] = {}
    for idx, val in enumerate(header):
        try:
            yr = int(val)
            if 2017 <= yr <= 2030 and idx <= 16:
                year_col[yr] = idx
        except (TypeError, ValueError):
            pass

    result: dict[str, dict[int, dict[int, float]]] = defaultdict(lambda: defaultdict(dict))
    for row in rows[2:]:
        area = row[1]
        bhk  = row[2]
        if not (area and bhk):
            continue
        try:
            bhk_int = int(bhk)
        except (TypeError, ValueError):
            continue
        for yr, col in year_col.items():
            try:
                result[str(area)][bhk_int][yr] = float(row[col])
            except (TypeError, ValueError):
                pass
    return dict(result)


# ── URA data loaders ──────────────────────────────────────────────────────────

def _load_ura_psf(pa_to_district: dict[str, str]) -> dict[str, dict]:
    """
    From ura_clean.csv: compute median PSF per planning area per year.
    Uses PA→district mapping to find the right district for each planning area.
    Returns {planning_area: {year_psf, cagr_pct, latest_psf, n_transactions, district}}
    """
    path = PROCESSED_DIR / "ura_clean.csv"
    if not path.exists():
        return {}

    df = pd.read_csv(path)
    if "year" not in df.columns:
        from ingestion.ura import parse_contract_date
        parsed = df["contractDate"].apply(parse_contract_date)
        df["year"] = parsed.apply(lambda x: x[0])
    if "price_psf" not in df.columns:
        SQM_TO_SQFT = 10.7639
        df["area_sqm"] = pd.to_numeric(df["area"], errors="coerce")
        df["area_sqft"] = df["area_sqm"] * SQM_TO_SQFT
        df["price_psf"] = pd.to_numeric(df["price"], errors="coerce") / df["area_sqft"]

    df = df[df["year"].between(2020, 2026)]
    df["district_str"] = df["district"].astype(str)

    result: dict[str, dict] = {}
    for pa, dist in pa_to_district.items():
        grp = df[df["district_str"] == dist].copy()
        if grp.empty:
            continue

        yr_psf: dict[int, float] = {}
        for yr, ygrp in grp.groupby("year"):
            if len(ygrp) >= 3:
                yr_psf[int(yr)] = round(ygrp["price_psf"].median(), 0)

        if not yr_psf:
            continue

        years = sorted(yr_psf)
        cagr = None
        if len(years) >= 2:
            y0, yn = years[0], years[-1]
            v0, vn = yr_psf[y0], yr_psf[yn]
            n = yn - y0
            if n > 0 and v0 > 0:
                cagr = round(((vn / v0) ** (1 / n) - 1) * 100, 2)

        result[pa] = {
            "year_psf": yr_psf,
            "cagr_pct": cagr,
            "latest_year": max(yr_psf),
            "latest_psf": yr_psf[max(yr_psf)],
            "n_transactions": len(grp),
            "district": dist,
        }
    return result


def _load_ura_rental(pa_to_district: dict[str, str]) -> dict[str, dict]:
    """
    From ura_rental_clean.csv: get most recent median rental PSF per planning area.
    Returns {planning_area: {rental_psf: float, ref_period: str}}
    """
    path = PROCESSED_DIR / "ura_rental_clean.csv"
    if not path.exists():
        return {}

    df = pd.read_csv(path)
    df["district_str"] = df["district"].astype(str)

    result: dict[str, dict] = {}
    for pa, dist in pa_to_district.items():
        grp = df[df["district_str"] == dist]
        if grp.empty:
            continue
        # Most recent period with at least 3 properties tracked
        grp = grp[grp.get("n_projects", grp.shape[0]) >= 3] if "n_projects" in grp.columns else grp
        if grp.empty:
            continue
        latest_row = grp.loc[grp["refPeriod"].idxmax()]
        result[pa] = {
            "rental_psf": round(float(latest_row["median"]), 2),
            "ref_period": str(latest_row["refPeriod"]),
        }
    return result


# ── MRT tier helper ───────────────────────────────────────────────────────────

def _mrt_tier(dist_m: float) -> str:
    for threshold, label in MRT_TIERS:
        if dist_m < threshold:
            return label
    return "Low (>1.5km)"


# ── Chunk builder ─────────────────────────────────────────────────────────────

def build_chunks(
    amenities: dict[str, dict[str, float]],
    region_map: dict[str, str],
    forecast: dict[str, dict],
    ura_psf: dict[str, dict],
    ura_rental: dict[str, dict],
) -> list[dict]:

    all_areas = (
        set(amenities) | set(forecast) | set(ura_psf) | set(ura_rental)
    )

    chunks = []
    for area in sorted(all_areas):
        region = region_map.get(area, "")
        am  = amenities.get(area, {})
        fct = forecast.get(area, {})
        psf = ura_psf.get(area, {})
        rnt = ura_rental.get(area, {})

        lines = [f"Area Investment Profile — {area} | {region}", ""]

        # ── Amenity Access ────────────────────────────────────────────────
        mrt_dist = am.get("ND-MRT")
        cbd_dist = am.get("ND-CBD")
        if mrt_dist or cbd_dist:
            lines.append("Location & Accessibility:")
            if mrt_dist:
                lines.append(
                    f"  MRT access: {mrt_dist:.0f}m avg — {_mrt_tier(mrt_dist)}"
                )
            if cbd_dist:
                lines.append(f"  Distance to CBD: {cbd_dist/1000:.1f} km")
            prisch = am.get("ND-BEST_PRISCH")
            if prisch:
                lines.append(f"  Nearest top primary school: {prisch:.0f}m")
            mall = am.get("ND-SHOPPINGCENTRE")
            if mall:
                lines.append(f"  Nearest shopping mall: {mall:.0f}m")
            park = am.get("ND-PARK")
            if park:
                lines.append(f"  Nearest park: {park:.0f}m")
            lines.append("")

        # ── Live PSF Trend (URA) ───────────────────────────────────────────
        if psf:
            yr_psf = psf["year_psf"]
            lines.append("Private Property PSF (URA transaction data):")
            psf_str = "  ".join(
                f"{yr}: SGD {v:,.0f}"
                for yr, v in sorted(yr_psf.items())
                if yr in PSF_YEARS
            )
            if psf_str:
                lines.append(f"  {psf_str}")
            if psf.get("cagr_pct") is not None:
                lines.append(
                    f"  PSF CAGR {min(yr_psf)}-{max(yr_psf)}: "
                    f"{psf['cagr_pct']:+.1f}%/yr"
                )
            lines.append(
                f"  Based on {psf['n_transactions']:,} transactions (2020-2026)"
            )
            lines.append("")

        # ── Rental Yield ──────────────────────────────────────────────────
        if rnt and psf:
            rental_psf_mo = rnt["rental_psf"]
            purchase_psf = psf.get("latest_psf", 0)
            if purchase_psf > 0:
                gross_yield = (rental_psf_mo * 12) / purchase_psf * 100
                net_yield   = gross_yield - 1.0  # ~1% maintenance/vacancy
                lines.append("Rental Market:")
                lines.append(
                    f"  Median rental PSF/month: SGD {rental_psf_mo:.2f} "
                    f"({rnt['ref_period']})"
                )
                lines.append(f"  Gross rental yield: {gross_yield:.2f}%/yr")
                lines.append(f"  Net rental yield (est.): {net_yield:.2f}%/yr")
                lines.append("")

        # ── Forecast Direction (Reco_Engine) ──────────────────────────────
        if fct:
            lines.append(
                f"Forecast PSF by bedroom type (model vintage {MODEL_VINTAGE}, "
                "directional only):"
            )
            for bhk, label in BHK_LABELS.items():
                yr_data = fct.get(bhk, {})
                vals = {
                    yr: yr_data[yr]
                    for yr in FORECAST_YEARS
                    if yr in yr_data and yr_data[yr] and yr_data[yr] > 0
                }
                if not vals:
                    continue
                val_str = "  ".join(
                    f"{yr}: SGD {v:,.0f}" for yr, v in sorted(vals.items())
                )
                years = sorted(vals)
                cagr_txt = ""
                if len(years) >= 2:
                    v0, vn = vals[years[0]], vals[years[-1]]
                    n = years[-1] - years[0]
                    if n > 0 and v0 > 0:
                        c = ((vn / v0) ** (1 / n) - 1) * 100
                        cagr_txt = f"  (CAGR {c:+.1f}%/yr)"
                lines.append(f"  {label}: {val_str}{cagr_txt}")
            lines.append("")

        lines.append(
            "Note: PSF trend from URA live data (authoritative). "
            "Forecast from proprietary model — treat as directional. "
            f"Model vintage {MODEL_VINTAGE}."
        )

        text = "\n".join(lines)
        chunk_id = f"roi_{area.lower().replace(' ', '_').replace('/', '_')}"

        # Compute rental yield for metadata
        gross_yield_val = None
        if rnt and psf and psf.get("latest_psf", 0) > 0:
            gross_yield_val = round(
                (rnt["rental_psf"] * 12) / psf["latest_psf"] * 100, 2
            )

        chunks.append({
            "chunk_id":      chunk_id,
            "source":        "roi",
            "planning_area": area,
            "region":        region,
            "model_vintage": MODEL_VINTAGE,
            "latest_psf":    psf.get("latest_psf"),
            "psf_cagr_pct":  psf.get("cagr_pct"),
            "gross_yield_pct": gross_yield_val,
            "mrt_dist_m":    round(am.get("ND-MRT", 0)) if am.get("ND-MRT") else None,
            "cbd_dist_km":   round(am.get("ND-CBD", 0) / 1000, 1) if am.get("ND-CBD") else None,
            "text":          text,
        })

    return chunks


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not EXCEL_PATH.exists():
        raise FileNotFoundError(f"Excel not found: {EXCEL_PATH}")

    print(f"Loading Excel: {EXCEL_PATH.name}")
    wb = openpyxl.load_workbook(
        str(EXCEL_PATH), read_only=True, keep_vba=False, data_only=True
    )

    print("  Extracting amenities + PA-to-district map from Master Data...")
    amenities, pa_to_district, region_map = _load_amenities_and_mapping(wb)
    print(f"    {len(amenities)} planning areas  |  {len(pa_to_district)} PA-district mappings")

    print("  Parsing Reco_Engine forecasts...")
    forecast = _load_reco_engine(wb)
    print(f"    {len(forecast)} planning areas with forecast data")

    print("  Loading URA PSF trend data...")
    ura_psf = _load_ura_psf(pa_to_district)
    print(f"    {len(ura_psf)} planning areas with live PSF data")

    print("  Loading URA rental data...")
    ura_rental = _load_ura_rental(pa_to_district)
    print(f"    {len(ura_rental)} planning areas with rental data")

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    print("  Building Area Investment Profile chunks...")
    chunks = build_chunks(amenities, region_map, forecast, ura_psf, ura_rental)
    print(f"    {len(chunks)} chunks built")

    out = PROCESSED_DIR / "roi_chunks.csv"
    pd.DataFrame(chunks).to_csv(out, index=False)
    print(f"  Saved -> {out}")

    # Print two sample chunks
    samples = ["Tampines", "Queenstown"]
    for area in samples:
        match = next((c for c in chunks if c["planning_area"] == area), None)
        if match:
            print(f"\n{'='*60}")
            print(f"Sample: {area}")
            print(match["text"])


if __name__ == "__main__":
    main()
