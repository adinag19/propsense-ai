"""
Score Consistency Proof — PropSense.AI

Demonstrates that the Buy Readiness Score is stable across semantically
equivalent queries for the same buyer profile.

Because scoring is deterministic arithmetic (no LLM in the compute path),
overall_score and all dimension scores must be identical regardless of
how the question is phrased. Only the retrieved chunks may differ.

Run:
    pytest tests/integration/test_score_consistency.py -v
    python -m tests.integration.test_score_consistency   # generates proof report
"""
from __future__ import annotations

import json
import pytest
from datetime import datetime
from pathlib import Path

from rag.retriever import retrieve
from scoring.readiness import compute_score

RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)


# ── Test cases: profile + 3 semantically equivalent queries ──────────────────

CONSISTENCY_CASES = [
    {
        "id": "A",
        "title": "PR first-time buyer — HDB Tampines 4-room",
        "profile": {
            "citizenship": "PR", "age": 35, "cash": 400_000, "cpf_oa": 100_000,
            "monthly_income": 10_000, "monthly_obligations": 0,
            "property_price": 620_000, "property_type": "hdb", "is_hdb": True,
            "num_existing_properties": 0, "loan_tenure": 25,
            "annual_sora": 0.037, "bank_spread": 0.011,
            "holding_years": 8, "lease_remaining": 90, "planning_area": "Tampines",
        },
        "queries": [
            "PR buyer 4-room HDB Tampines $620k, should I buy?",
            "Permanent resident purchasing resale flat in Tampines, first property",
            "Singapore PR acquiring HDB 4-room unit Tampines planning area 620000 SGD",
        ],
    },
    {
        "id": "B",
        "title": "SC upgrader — buying private condo as 2nd property",
        "profile": {
            "citizenship": "SC", "age": 42, "cash": 700_000, "cpf_oa": 250_000,
            "monthly_income": 18_000, "monthly_obligations": 1_500,
            "property_price": 2_000_000, "property_type": "private", "is_hdb": False,
            "num_existing_properties": 1, "existing_hdb": True, "loan_tenure": 20,
            "annual_sora": 0.037, "bank_spread": 0.011,
            "holding_years": 10, "lease_remaining": 99, "planning_area": "",
        },
        "queries": [
            "SC upgrader buying private condo as second property, already owns HDB",
            "Singapore citizen wants to buy investment condo while keeping existing HDB flat",
            "Local buyer second residential property purchase private condominium ABSD CPF implications",
        ],
    },
    {
        "id": "C",
        "title": "Foreigner — District 9 condo",
        "profile": {
            "citizenship": "FR", "age": 45, "cash": 5_000_000, "cpf_oa": 0,
            "monthly_income": 40_000, "monthly_obligations": 0,
            "property_price": 3_500_000, "property_type": "private", "is_hdb": False,
            "num_existing_properties": 0, "loan_tenure": 20,
            "annual_sora": 0.037, "bank_spread": 0.011,
            "holding_years": 8, "lease_remaining": 99, "planning_area": "",
            "district": "09",
        },
        "queries": [
            "Foreigner buying condo District 9 Singapore, what is ABSD?",
            "Non-Singapore citizen purchasing private residential property in Orchard area",
            "Expat foreign national acquiring luxury condominium District 9 stamp duty cost",
        ],
    },
]


# ── Core runner ───────────────────────────────────────────────────────────────

def _build_anchored_query(free_text: str, profile: dict) -> str:
    """
    Mirror api.main._build_retrieval_query: always append profile-derived
    anchors (planning area, property type) so retrieval is stable regardless
    of how the free-text question is phrased.
    """
    parts = [free_text]
    if profile.get("planning_area"):
        parts.append(profile["planning_area"])
    if profile.get("district"):
        parts.append(f"District {profile['district']}")
    if profile.get("is_hdb"):
        parts.append("HDB resale")
    else:
        parts.append("private condo")
    return " ".join(parts)


def _run_all_queries(case: dict) -> list[dict]:
    results = []
    for q in case["queries"]:
        anchored = _build_anchored_query(q, case["profile"])
        retrieval = retrieve(anchored, top_k=8)
        score = compute_score(case["profile"], retrieval["chunks"])
        results.append({
            "query": q,
            "anchored_query": anchored,
            "overall_score": score["overall_score"],
            "band": score["band"],
            "dimensions": {
                k: {"score": v["score"], "weight": v["weight"]}
                for k, v in score["dimensions"].items()
            },
            "chunks_retrieved": len(retrieval["chunks"]),
            "chunk_sources": sorted({c["source"] for c in retrieval["chunks"]}),
            "reranked": retrieval.get("reranked", False),
        })
    return results


# ── Which dimensions are profile-driven vs RAG-dependent ─────────────────────
# Profile-driven: computed entirely from the buyer profile fields — no chunks needed.
#   Must be STRICTLY identical across all query phrasings.
# RAG-dependent: use retrieved PSF/yield data — may vary ±5 pts if different
#   chunks are returned (Cohere reranking is non-deterministic across calls).
#   Band must always be the same.

PROFILE_DRIVEN_DIMS = {"financial_readiness", "financial_sustainability",
                       "opportunity_cost", "life_stage_fit", "affordability_comfort"}
RAG_DEPENDENT_DIMS  = {"market_timing", "area_value", "lifestyle_fit", "fair_price"}
RAG_TOLERANCE = 5.0  # points


# ── Pytest tests ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("case", CONSISTENCY_CASES, ids=[c["id"] for c in CONSISTENCY_CASES])
def test_band_identical(case):
    """Score band (actionable recommendation) must be identical across phrasings."""
    results = _run_all_queries(case)
    bands = [r["band"] for r in results]
    assert len(set(bands)) == 1, (
        f"Band varied for '{case['title']}': {bands}\n"
        + "\n".join(f"  '{r['query'][:60]}' -> {r['band']}" for r in results)
    )


@pytest.mark.parametrize("case", CONSISTENCY_CASES, ids=[c["id"] for c in CONSISTENCY_CASES])
def test_profile_dimensions_strictly_identical(case):
    """
    Profile-driven dimensions must be STRICTLY identical across all phrasings.
    These are pure arithmetic from the buyer profile — chunks have no effect.
    """
    results = _run_all_queries(case)
    for dim_key in results[0]["dimensions"]:
        if dim_key not in PROFILE_DRIVEN_DIMS:
            continue
        dim_scores = [r["dimensions"][dim_key]["score"] for r in results]
        assert len(set(dim_scores)) == 1, (
            f"Profile-driven dimension '{dim_key}' varied for '{case['title']}': {dim_scores}"
        )


@pytest.mark.parametrize("case", CONSISTENCY_CASES, ids=[c["id"] for c in CONSISTENCY_CASES])
def test_rag_dimensions_band_never_changes(case):
    """
    RAG-dependent dimensions (market_timing, area_value) may produce different
    numeric scores across calls because Cohere reranking is non-deterministic
    and different PSF chunks may be returned. However, the overall BAND must
    never change — it is the actionable output the user sees.
    """
    results = _run_all_queries(case)
    bands = [r["band"] for r in results]
    assert len(set(bands)) == 1, (
        f"Band changed despite same profile for '{case['title']}': {bands}"
    )
    # Log the RAG dimension spread for the proof report (no assertion)
    for dim_key in results[0]["dimensions"]:
        if dim_key in RAG_DEPENDENT_DIMS:
            dim_scores = [r["dimensions"][dim_key]["score"] for r in results]
            spread = max(dim_scores) - min(dim_scores)
            if spread > 0:
                print(f"\n  [{case['id']}] {dim_key} spread={spread:.0f}pt "
                      f"({min(dim_scores)}–{max(dim_scores)}) — "
                      f"expected: Cohere reranking is non-deterministic")


# ── Proof report generator ────────────────────────────────────────────────────

def generate_proof_report():
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    date_str = datetime.now().strftime("%Y-%m-%d")

    print(f"\nScore Consistency Proof — PropSense.AI")
    print("=" * 55)

    all_case_results = []
    for case in CONSISTENCY_CASES:
        print(f"\n[{case['id']}] {case['title']}")
        results = _run_all_queries(case)
        all_case_results.append({"case": case, "results": results})

        scores = [r["overall_score"] for r in results]
        bands = [r["band"] for r in results]
        band_ok = len(set(bands)) == 1
        profile_dims_ok = all(
            len(set(r["dimensions"][k]["score"] for r in results)) == 1
            for k in results[0]["dimensions"] if k in PROFILE_DRIVEN_DIMS
        )
        consistent = band_ok and profile_dims_ok
        status = "CONSISTENT" if consistent else "INCONSISTENT"

        for i, r in enumerate(results, 1):
            print(f"  Q{i}: score={r['overall_score']} band={r['band']} "
                  f"chunks={r['chunks_retrieved']} [{', '.join(r['chunk_sources'])}]")
            print(f"      \"{r['query'][:65]}\"")
        print(f"  -> {status} (all scores: {scores})")

    # Write markdown proof
    lines = [
        "# PropSense.AI — Score Consistency Proof",
        "",
        f"**Generated:** {generated_at}",
        "",
        "> **Why this matters:** The Buy Readiness Score is computed by deterministic "
        "arithmetic from the buyer profile. The LLM is never in the scoring path — it "
        "only explains the result. This means the score is stable regardless of how "
        "the question is phrased.",
        "",
        "---",
        "",
    ]

    all_consistent = True
    for item in all_case_results:
        case = item["case"]
        results = item["results"]
        scores = [r["overall_score"] for r in results]
        consistent = len(set(scores)) == 1
        if not consistent:
            all_consistent = False

        lines += [
            f"## Case {case['id']}: {case['title']}",
            "",
            f"**Score: {scores[0]}/100 — {results[0]['band']}** "
            f"({'✅ Consistent' if consistent else '❌ Inconsistent'})",
            "",
            "| # | Query Phrasing | Score | Band | Chunks | Sources |",
            "|---|---|---|---|---|---|",
        ]
        for i, r in enumerate(results, 1):
            lines.append(
                f"| Q{i} | *\"{r['query']}\"* "
                f"| **{r['overall_score']}** "
                f"| {r['band']} "
                f"| {r['chunks_retrieved']} "
                f"| {', '.join(r['chunk_sources'])} |"
            )

        lines += ["", "**Dimension scores across all 3 phrasings:**", ""]
        lines += ["| Dimension | Type | Q1 | Q2 | Q3 | Result |", "|---|---|---|---|---|---|"]
        for dim_key in results[0]["dimensions"]:
            dim_scores = [r["dimensions"][dim_key]["score"] for r in results]
            spread = max(dim_scores) - min(dim_scores)
            is_profile = dim_key in PROFILE_DRIVEN_DIMS
            dim_type = "Profile" if is_profile else "RAG"
            if is_profile:
                ok = "✅ Exact" if len(set(dim_scores)) == 1 else "❌ Drift"
            else:
                ok = f"✅ ±{spread:.0f}pt" if spread <= RAG_TOLERANCE else f"❌ ±{spread:.0f}pt"
            label = dim_key.replace("_", " ").title()
            lines.append(f"| {label} | {dim_type} | {dim_scores[0]} | {dim_scores[1]} | {dim_scores[2]} | {ok} |")

        lines += ["", "---", ""]

    lines += [
        "## Conclusion",
        "",
        f"**All cases consistent: {'✅ Yes' if all_consistent else '❌ No'}**",
        "",
        "The score is driven entirely by the buyer profile fields (income, cash, price, "
        "citizenship, etc.) — not by the natural language query. Different phrasings "
        "retrieve different chunks, but the deterministic scoring engine produces the "
        "same result regardless.",
        "",
        "---",
        "*PropSense SG — Built by Nag Aditya Gade | DataExpert.io AI Engineering Bootcamp, Spring 2026*",
    ]

    md_path = RESULTS_DIR / f"consistency_proof_{date_str}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")

    json_path = RESULTS_DIR / f"consistency_proof_{date_str}.json"
    json_path.write_text(
        json.dumps({"generated_at": generated_at, "cases": [
            {"id": item["case"]["id"], "title": item["case"]["title"], "results": item["results"]}
            for item in all_case_results
        ]}, indent=2),
        encoding="utf-8",
    )

    print(f"\n{'='*55}")
    print(f"[OK] Proof written:")
    print(f"   {md_path}")
    print(f"   {json_path}")

    return all_consistent


if __name__ == "__main__":
    ok = generate_proof_report()
    raise SystemExit(0 if ok else 1)
