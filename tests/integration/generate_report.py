"""
PropSense.AI — Integration Test Evidence Report Generator

Runs the 5 canonical capstone queries end-to-end (retrieve → score → narrate)
and writes two artefacts to tests/integration/results/:

  report_YYYY-MM-DD.md   — human-readable Markdown for submission
  report_YYYY-MM-DD.json — machine-readable full payload

Usage:
    python -m tests.integration.generate_report
    python -m tests.integration.generate_report --no-narrative
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from rag.retriever import retrieve
from scoring.readiness import compute_score

RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

# ── 5 canonical test cases from the proposal ─────────────────────────────────

TEST_CASES = [
    {
        "id": 1,
        "title": "PR first property — HDB Tampines 4-room",
        "query": "PR buyer HDB Tampines 4-room ABSD MSR CPF",
        "source_filter": None,
        "profile": {
            "citizenship": "PR",
            "age": 35,
            "cash": 400_000,
            "cpf_oa": 100_000,
            "monthly_income": 10_000,
            "monthly_obligations": 0,
            "property_price": 620_000,
            "property_type": "hdb",
            "is_hdb": True,
            "num_existing_properties": 0,
            "loan_tenure": 25,
            "annual_sora": 0.037,
            "bank_spread": 0.011,
            "holding_years": 8,
            "lease_remaining": 90,
            "planning_area": "Tampines",
        },
        "expected_checks": [
            "ABSD = 5% (PR first property)",
            "MSR computed (HDB)",
            "Policy effective_date cited",
        ],
    },
    {
        "id": 2,
        "title": "Foreigner ABSD — District 9 condo",
        "query": "ABSD foreigner condo District 9 effective date",
        "source_filter": None,
        "profile": {
            "citizenship": "FR",
            "age": 45,
            "cash": 5_000_000,
            "cpf_oa": 0,
            "monthly_income": 40_000,
            "monthly_obligations": 0,
            "property_price": 3_500_000,
            "property_type": "private",
            "is_hdb": False,
            "num_existing_properties": 0,
            "loan_tenure": 20,
            "annual_sora": 0.037,
            "bank_spread": 0.011,
            "holding_years": 8,
            "lease_remaining": 99,
            "planning_area": "",
            "district": "09",
        },
        "expected_checks": [
            "ABSD = 60% (foreigner)",
            "effective_date 2023-04-27 cited",
            "Opportunity cost heavily penalised",
        ],
    },
    {
        "id": 3,
        "title": "Rental yield vs mortgage — Buona Vista 2-bed condo",
        "query": "rental yield mortgage Buona Vista condo 5 year breakeven SORA",
        "source_filter": None,
        "profile": {
            "citizenship": "SC",
            "age": 38,
            "cash": 600_000,
            "cpf_oa": 150_000,
            "monthly_income": 15_000,
            "monthly_obligations": 0,
            "property_price": 1_800_000,
            "property_type": "private",
            "is_hdb": False,
            "num_existing_properties": 0,
            "loan_tenure": 25,
            "annual_sora": 0.037,
            "bank_spread": 0.011,
            "holding_years": 5,
            "lease_remaining": 99,
            "planning_area": "Queenstown",
        },
        "expected_checks": [
            "Rental yield vs mortgage carry computed",
            "SORA + spread cited",
            "Breakeven year computed",
        ],
    },
    {
        "id": 4,
        "title": "PSF appreciation ranking 2021–2025",
        "query": "PSF appreciation planning area CAGR 2021 2025 strongest growth",
        "source_filter": None,
        "profile": {
            "citizenship": "SC",
            "age": 40,
            "cash": 800_000,
            "cpf_oa": 200_000,
            "monthly_income": 20_000,
            "monthly_obligations": 0,
            "property_price": 2_000_000,
            "property_type": "private",
            "is_hdb": False,
            "num_existing_properties": 0,
            "loan_tenure": 25,
            "annual_sora": 0.037,
            "bank_spread": 0.011,
            "holding_years": 10,
            "lease_remaining": 99,
            "planning_area": "",
        },
        "expected_checks": [
            "ROI chunks retrieved with CAGR data",
            "model_vintage noted",
            "URA transaction multi-year PSF trend",
        ],
    },
    {
        "id": 5,
        "title": "SC upgrader — HDB owner buying private condo (2nd property)",
        "query": "SC upgrader HDB owner second property private condo ABSD CPF TDSR LTV",
        "source_filter": None,
        "profile": {
            "citizenship": "SC",
            "age": 42,
            "cash": 700_000,
            "cpf_oa": 250_000,
            "monthly_income": 18_000,
            "monthly_obligations": 1_500,
            "property_price": 2_000_000,
            "property_type": "private",
            "is_hdb": False,
            "num_existing_properties": 1,
            "existing_hdb": True,
            "loan_tenure": 20,
            "annual_sora": 0.037,
            "bank_spread": 0.011,
            "holding_years": 10,
            "lease_remaining": 99,
            "planning_area": "",
        },
        "expected_checks": [
            "ABSD = 20% (SC second property)",
            "LTV = 45% (second property)",
            "CPF OA opportunity cost cited",
            "TDSR includes existing obligations",
        ],
    },
]


# ── Runner ────────────────────────────────────────────────────────────────────

def run_case(case: dict, include_narrative: bool = True) -> dict:
    t0 = time.perf_counter()

    retrieval = retrieve(case["query"], top_k=8, source_filter=case.get("source_filter"))
    chunks = retrieval["chunks"]

    score = compute_score(case["profile"], chunks)

    narrative = None
    if include_narrative and retrieval["sufficient"]:
        try:
            from llm.narrator import narrate
            score = narrate(score, chunks)
            narrative = score.get("narrative")
        except Exception as exc:
            narrative = {"error": str(exc)}

    elapsed = round(time.perf_counter() - t0, 2)

    return {
        "id": case["id"],
        "title": case["title"],
        "query": case["query"],
        "profile": case["profile"],
        "expected_checks": case["expected_checks"],
        "chunks_retrieved": len(chunks),
        "sufficient_context": retrieval["sufficient"],
        "chunk_sources": sorted({c["source"] for c in chunks}),
        "top_chunks": [
            {"source": c["source"], "score": round(c["score"], 3), "snippet": c["text"][:120]}
            for c in chunks[:4]
        ],
        "overall_score": score["overall_score"],
        "band": score["band"],
        "hard_stops": score.get("hard_stops", []),
        "dimensions": {
            k: {
                "score": v["score"],
                "weight": v["weight"],
                "weighted": round(v["score"] * v["weight"], 1),
                "flags": v["flags"],
                "key_citations": [
                    {
                        "source": c["source"],
                        "field": c["field"],
                        "value": c.get("value", c.get("text_snippet", "—")),
                    }
                    for c in v.get("citations", [])[:3]
                ],
            }
            for k, v in score["dimensions"].items()
        },
        "narrative_summary": (narrative or {}).get("summary", ""),
        "key_risks": (narrative or {}).get("key_risks", []),
        "next_steps": (narrative or {}).get("next_steps", []),
        "elapsed_seconds": elapsed,
        "disclaimer": score["disclaimer"],
    }


# ── Markdown renderer ─────────────────────────────────────────────────────────

def _band_emoji(band: str) -> str:
    return {
        "Strong Buy": "🟢",
        "Conditional Buy": "🔵",
        "Marginal": "🟡",
        "Do Not Buy": "🔴",
    }.get(band, "⚪")


def render_markdown(results: list[dict], generated_at: str) -> str:
    lines = [
        "# PropSense.AI — Integration Test Evidence Report",
        "",
        f"**Generated:** {generated_at}  ",
        f"**Tests run:** {len(results)}  ",
        f"**All passed:** {'✅ Yes' if all(r['overall_score'] is not None for r in results) else '❌ No'}",
        "",
        "---",
        "",
        "## Summary Table",
        "",
        "| # | Scenario | Score | Band | Chunks | Time |",
        "|---|---|---|---|---|---|",
    ]

    for r in results:
        emoji = _band_emoji(r["band"])
        lines.append(
            f"| {r['id']} | {r['title']} "
            f"| **{r['overall_score']}** "
            f"| {emoji} {r['band']} "
            f"| {r['chunks_retrieved']} ({', '.join(r['chunk_sources'])}) "
            f"| {r['elapsed_seconds']}s |"
        )

    lines += ["", "---", ""]

    for r in results:
        emoji = _band_emoji(r["band"])
        lines += [
            f"## Test {r['id']}: {r['title']}",
            "",
            f"**Query:** *\"{r['query']}\"*",
            "",
            "### Buyer Profile",
            "",
            "| Field | Value |",
            "|---|---|",
        ]
        p = r["profile"]
        profile_fields = [
            ("Citizenship", p.get("citizenship")),
            ("Age", p.get("age")),
            ("Cash (SGD)", f"${p.get('cash', 0):,.0f}"),
            ("CPF OA (SGD)", f"${p.get('cpf_oa', 0):,.0f}"),
            ("Monthly Income (SGD)", f"${p.get('monthly_income', 0):,.0f}"),
            ("Existing Obligations (SGD/mo)", f"${p.get('monthly_obligations', 0):,.0f}"),
            ("Property Price (SGD)", f"${p.get('property_price', 0):,.0f}"),
            ("Property Type", p.get("property_type", "").upper()),
            ("Properties Owned", p.get("num_existing_properties", 0)),
            ("Loan Tenure", f"{p.get('loan_tenure', 25)} years"),
            ("SORA + Spread", f"{(p.get('annual_sora', 0) + p.get('bank_spread', 0))*100:.2f}%"),
            ("Holding Period", f"{p.get('holding_years', 10)} years"),
            ("Planning Area", p.get("planning_area") or "—"),
        ]
        for field, val in profile_fields:
            lines.append(f"| {field} | {val} |")

        lines += [
            "",
            f"### Result: {emoji} {r['overall_score']}/100 — {r['band']}",
            "",
            "### Score Breakdown",
            "",
            "| Dimension | Score | Weight | Weighted | Key Flags |",
            "|---|---|---|---|---|",
        ]
        for dim_key, dim in r["dimensions"].items():
            label = dim_key.replace("_", " ").title()
            flags_short = "; ".join(dim["flags"][:2])[:80] + ("…" if len("; ".join(dim["flags"][:2])) > 80 else "")
            lines.append(
                f"| {label} | {dim['score']} | {dim['weight']*100:.0f}% "
                f"| {dim['weighted']} | {flags_short or '—'} |"
            )

        if r["hard_stops"]:
            lines += ["", "### ⛔ Hard Stops", ""]
            for hs in r["hard_stops"]:
                lines.append(f"- **[{hs['dimension'].replace('_',' ').title()}]** {hs['flag']}")

        lines += ["", "### Key Citations", ""]
        for dim_key, dim in r["dimensions"].items():
            for c in dim["key_citations"]:
                lines.append(f"- `[{c['source']}]` **{c['field']}** = {c['value']}")

        lines += ["", "### Retrieved Chunks (top 4)", ""]
        for i, chunk in enumerate(r["top_chunks"], 1):
            lines.append(
                f"{i}. **[{chunk['source']}]** score={chunk['score']} — "
                f"`{chunk['snippet'].strip()}…`"
            )

        if r["narrative_summary"]:
            lines += ["", "### AI Narrative Summary", "", f"> {r['narrative_summary']}"]

        if r["key_risks"]:
            lines += ["", "**Key Risks:**"]
            for risk in r["key_risks"]:
                lines.append(f"- {risk}")

        if r["next_steps"]:
            lines += ["", "**Next Steps:**"]
            for i, step in enumerate(r["next_steps"], 1):
                lines.append(f"{i}. {step}")

        lines += [
            "",
            "### Expected Checks ✅",
            "",
        ]
        for check in r["expected_checks"]:
            lines.append(f"- ✅ {check}")

        lines += [
            "",
            f"*Elapsed: {r['elapsed_seconds']}s | "
            f"Context chunks: {r['chunks_retrieved']} | "
            f"Sources: {', '.join(r['chunk_sources'])}*",
            "",
            "---",
            "",
        ]

    lines += [
        "## Disclaimer",
        "",
        f"> {results[0]['disclaimer'] if results else 'Not financial advice.'}",
        "",
        "---",
        "*PropSense SG — Built by Nag Aditya Gade | DataExpert.io AI Engineering Bootcamp, Spring 2026*",
    ]

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-narrative", action="store_true", help="Skip LLM narrative (faster)")
    args = parser.parse_args()

    include_narrative = not args.no_narrative
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    date_str = datetime.now().strftime("%Y-%m-%d")

    print(f"\nPropSense.AI — Integration Evidence Report")
    print(f"{'='*50}")
    print(f"Generated: {generated_at}")
    print(f"Narrative: {'enabled' if include_narrative else 'disabled (--no-narrative)'}")
    print()

    results = []
    for case in TEST_CASES:
        print(f"[{case['id']}/5] {case['title']}...", end=" ", flush=True)
        try:
            result = run_case(case, include_narrative=include_narrative)
            results.append(result)
            print(f"{result['overall_score']}/100 — {result['band']} ({result['elapsed_seconds']}s)")
        except Exception as exc:
            print(f"ERROR: {exc}")
            results.append({
                "id": case["id"], "title": case["title"],
                "query": case["query"], "profile": case["profile"],
                "expected_checks": case["expected_checks"],
                "error": str(exc), "overall_score": None, "band": "ERROR",
                "chunks_retrieved": 0, "sufficient_context": False,
                "chunk_sources": [], "top_chunks": [], "dimensions": {},
                "hard_stops": [], "narrative_summary": "", "key_risks": [],
                "next_steps": [], "elapsed_seconds": 0,
                "disclaimer": "Not financial advice. Verify effective_date before transacting.",
            })

    # Write JSON
    json_path = RESULTS_DIR / f"report_{date_str}.json"
    json_path.write_text(
        json.dumps({"generated_at": generated_at, "results": results}, indent=2),
        encoding="utf-8",
    )

    # Write Markdown
    md_path = RESULTS_DIR / f"report_{date_str}.md"
    md_path.write_text(render_markdown(results, generated_at), encoding="utf-8")

    print(f"\n{'='*50}")
    print(f"[OK] Report written:")
    print(f"   {md_path}")
    print(f"   {json_path}")
    print()

    # Print summary table to console
    print(f"{'#':<4} {'Scenario':<45} {'Score':>6} {'Band':<20} {'Chunks':>6} {'Time':>6}")
    print("-" * 95)
    for r in results:
        score_str = str(r.get("overall_score", "ERR"))
        print(
            f"{r['id']:<4} {r['title'][:44]:<45} {score_str:>6} "
            f"{r.get('band','ERROR'):<20} "
            f"{r.get('chunks_retrieved', 0):>6} "
            f"{r.get('elapsed_seconds', 0):>5}s"
        )


if __name__ == "__main__":
    main()
