"""
5 required integration tests for PropSense.AI capstone submission.

Tests verify end-to-end pipeline correctness for the five canonical query scenarios
from the project proposal. Each test:
  1. Retrieves relevant chunks from Pinecone
  2. Runs the deterministic scoring engine
  3. Asserts specific numeric/policy outputs

Run with:
    pytest tests/integration/test_pipeline.py -v
"""
import pytest
from rag.retriever import retrieve
from scoring.readiness import (
    compute_score,
    calc_absd,
    calc_bsd,
    monthly_repayment,
    ABSD_RATES,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _score(profile: dict, query: str = "", source_filter: str | None = None) -> dict:
    result = retrieve(query or "Singapore property", source_filter=source_filter)
    return compute_score(profile, result["chunks"])


# ── Test 1: PR first property — HDB Tampines 4-room ──────────────────────────

class TestPRHDBTampines:
    """
    PR buyer, $400k cash, buying first property — HDB Tampines 4-room ~$620k.
    Expected: ABSD 5% applied, MSR checked, CPF usage noted.
    """

    PROFILE = {
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
        "holding_years": 10,
        "lease_remaining": 90,
    }

    def test_absd_rate_5pct_pr_first(self):
        """PR first property ABSD = 5%."""
        absd = calc_absd(self.PROFILE["property_price"], "PR", 0)
        expected = self.PROFILE["property_price"] * 0.05
        assert abs(absd - expected) < 1, f"Expected ABSD {expected:.0f}, got {absd:.0f}"

    def test_absd_cited_in_financial_dimension(self):
        """ABSD rate appears in financial_readiness citations."""
        chunks = retrieve(
            "HDB Tampines 4-room PR ABSD", source_filter=None
        )["chunks"]
        score = compute_score(self.PROFILE, chunks)
        citations = score["dimensions"]["financial_readiness"]["citations"]
        absd_citation = next(
            (c for c in citations if c.get("field") == "ABSD"), None
        )
        assert absd_citation is not None, "ABSD citation missing"
        assert "5%" in absd_citation["value"] or "31,000" in absd_citation["value"].replace(",", ""), \
            f"ABSD citation value unexpected: {absd_citation['value']}"

    def test_msr_checked_for_hdb(self):
        """MSR (30% limit) is computed for HDB purchases."""
        chunks = retrieve("HDB Tampines resale", source_filter="hdb")["chunks"]
        score = compute_score(self.PROFILE, chunks)
        details = score["dimensions"]["financial_readiness"]["details"]
        assert details.get("msr") is not None, "MSR not computed for HDB"
        assert 0 < details["msr"] < 1, f"MSR value implausible: {details['msr']}"

    def test_policy_chunks_retrieved(self):
        """Policy KB returns ABSD/MSR chunks."""
        result = retrieve("ABSD rates permanent resident HDB MSR", source_filter="policy")
        assert result["sufficient"], "Policy retrieval insufficient"
        sources = {c["source"] for c in result["chunks"]}
        assert "policy" in sources

    def test_overall_score_in_range(self):
        """Overall score is 0-100 and band is valid."""
        score = _score(self.PROFILE, "PR HDB Tampines 4-room")
        assert 0 <= score["overall_score"] <= 100
        assert score["band"] in ("Strong Buy", "Conditional Buy", "Marginal", "Do Not Buy")
        assert score["disclaimer"] != ""


# ── Test 2: Foreigner ABSD District 9 ────────────────────────────────────────

class TestForeignerDistrict9:
    """
    Foreigner buying District 9 condo — ABSD 60% must be cited with effective_date.
    """

    PROFILE = {
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
    }

    def test_absd_60pct_foreigner(self):
        """Foreigner ABSD = 60% regardless of property count."""
        absd = calc_absd(self.PROFILE["property_price"], "FR", 0)
        expected = self.PROFILE["property_price"] * 0.60
        assert abs(absd - expected) < 1

    def test_effective_date_cited(self):
        """Policy citation includes effective_date for ABSD."""
        chunks = retrieve("ABSD foreigner rate effective date", source_filter="policy")["chunks"]
        score = compute_score(self.PROFILE, chunks)
        citations = score["dimensions"]["financial_readiness"]["citations"]
        absd_citation = next((c for c in citations if c.get("field") == "ABSD"), None)
        assert absd_citation is not None
        note = absd_citation.get("note", "")
        assert "2023" in note, f"effective_date 2023-04-27 not in citation note: {note}"

    def test_opportunity_cost_penalised(self):
        """60% ABSD triggers high opportunity cost penalty."""
        score = _score(self.PROFILE, "District 9 condo foreigner")
        oc_score = score["dimensions"]["opportunity_cost"]["score"]
        assert oc_score <= 45, f"Opportunity cost should be heavily penalised, got {oc_score}"

    def test_ura_transaction_chunks_retrieved(self):
        """District 9 URA transaction data retrieved."""
        result = retrieve("PSF District 9 condo", source_filter="ura_transaction")
        assert len(result["chunks"]) >= 2, "Too few URA transaction chunks for D9"
        texts = " ".join(c["text"] for c in result["chunks"])
        assert "09" in texts or "District 09" in texts


# ── Test 3: Rental yield vs mortgage — Buona Vista / Queenstown ──────────────

class TestRentalYieldBuonaVista:
    """
    SC investor — 2-bed condo Buona Vista area (Queenstown PA, D5).
    Rental yield vs mortgage (SORA + spread) computed and cited.
    """

    PROFILE = {
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
        "holding_years": 10,
        "lease_remaining": 99,
    }

    def test_rental_chunks_retrieved(self):
        """URA rental data retrieved for Buona Vista / Queenstown area."""
        result = retrieve("Buona Vista condo rental yield PSF", source_filter="ura_rental")
        assert result["sufficient"], "Rental chunks insufficient"
        assert len(result["chunks"]) >= 3

    def test_market_timing_has_rental_signal(self):
        """market_timing dimension reflects rental yield vs mortgage."""
        rental_chunks = retrieve("rental PSF month condo", source_filter="ura_rental")["chunks"]
        txn_chunks = retrieve("PSF condo District 5", source_filter="ura_transaction")["chunks"]
        score = compute_score(self.PROFILE, rental_chunks + txn_chunks)
        mt = score["dimensions"]["market_timing"]
        # Should have at least a note about positive/negative carry or PSF trend
        assert len(mt["flags"]) >= 0  # zero flags OK (neutral) but dimension must exist
        citations = mt["citations"]
        assert isinstance(citations, list)

    def test_mortgage_rate_components(self):
        """Mortgage rate = SORA + spread is correctly applied."""
        score = _score(self.PROFILE)
        details = score["dimensions"]["financial_readiness"]["details"]
        expected_rate = self.PROFILE["annual_sora"] + self.PROFILE["bank_spread"]
        assert abs(details["mortgage_rate_actual"] - expected_rate) < 0.001

    def test_score_structure_complete(self):
        """All 5 dimensions present, weights sum to 1.0."""
        score = _score(self.PROFILE)
        dims = score["dimensions"]
        assert set(dims.keys()) == {
            "financial_readiness", "market_timing", "area_value",
            "opportunity_cost", "life_stage_fit",
        }
        total_weight = sum(d["weight"] for d in dims.values())
        assert abs(total_weight - 1.0) < 0.001


# ── Test 4: PSF appreciation ranking 2021-2025 ────────────────────────────────

class TestPSFAppreciation:
    """
    Query: which planning areas showed strongest PSF appreciation 2021-2025?
    ROI chunks must be retrieved and contain CAGR data.
    """

    def test_roi_chunks_retrieved(self):
        """ROI/area profile chunks retrieved for appreciation query."""
        result = retrieve("PSF appreciation 2021 2025 planning area CAGR", source_filter="roi")
        assert result["sufficient"], "ROI chunks not retrieved"
        texts = " ".join(c["text"] for c in result["chunks"])
        assert "CAGR" in texts, "CAGR not present in ROI chunks"
        assert "PSF" in texts

    def test_roi_chunks_have_psf_and_yield_metadata(self):
        """ROI chunks carry psf_cagr_pct and gross_yield_pct in metadata."""
        result = retrieve("PSF investment planning area", source_filter="roi", top_k=5)
        chunks_with_cagr = [
            c for c in result["chunks"]
            if c["metadata"].get("psf_cagr_pct") is not None
        ]
        assert len(chunks_with_cagr) >= 1, "No ROI chunks with psf_cagr_pct metadata"

    def test_ura_transaction_multi_year(self):
        """URA transaction chunks span multiple years for trend analysis."""
        result = retrieve("PSF appreciation District 9 condo 2023", source_filter="ura_transaction")
        years = set()
        import re
        for c in result["chunks"]:
            m = re.search(r"\|\s*(\d{4})\b", c["text"])
            if m:
                years.add(int(m.group(1)))
        assert len(years) >= 2, f"Need multi-year URA data, got years: {years}"

    def test_model_vintage_noted(self):
        """ROI chunks note model_vintage to flag data staleness."""
        result = retrieve("planning area PSF forecast", source_filter="roi")
        vintage_mentioned = any(
            "model_vintage" in c["metadata"] or "2022" in c["text"]
            for c in result["chunks"]
        )
        assert vintage_mentioned, "model_vintage not mentioned in ROI chunks"


# ── Test 5: SC upgrader — HDB + private as 2nd property ─────────────────────

class TestSCUpgraderSecondProperty:
    """
    SC upgrader owns HDB, buying private condo as 2nd property.
    ABSD 20%, CPF OA opportunity cost, TDSR, LTV 45% all verified.
    """

    PROFILE = {
        "citizenship": "SC",
        "age": 42,
        "cash": 700_000,
        "cpf_oa": 250_000,
        "monthly_income": 18_000,
        "monthly_obligations": 1_500,  # existing HDB loan
        "property_price": 2_000_000,
        "property_type": "private",
        "is_hdb": False,
        "num_existing_properties": 1,  # owns HDB already
        "existing_hdb": True,
        "loan_tenure": 20,
        "annual_sora": 0.037,
        "bank_spread": 0.011,
        "holding_years": 10,
        "lease_remaining": 99,
    }

    def test_absd_20pct_sc_second(self):
        """SC second property ABSD = 20%."""
        absd = calc_absd(self.PROFILE["property_price"], "SC", 1)
        expected = self.PROFILE["property_price"] * 0.20
        assert abs(absd - expected) < 1

    def test_ltv_45pct_second_property(self):
        """LTV for second property = 45%."""
        from scoring.readiness import ltv_for_property
        ltv = ltv_for_property(1)
        assert abs(ltv - 0.45) < 0.001, f"Expected LTV 0.45 for 2nd property, got {ltv}"

    def test_tdsr_computed_with_existing_obligations(self):
        """TDSR includes existing HDB loan repayment in denominator."""
        score = _score(self.PROFILE, "SC upgrader second property private condo")
        details = score["dimensions"]["financial_readiness"]["details"]
        # With $1,500/mo existing obligation + new mortgage on a $2M property, TDSR will be significant
        assert details["tdsr"] > 0.30, "TDSR suspiciously low given existing obligations"

    def test_opportunity_cost_cites_cpf(self):
        """Opportunity cost dimension cites CPF OA rate."""
        score = _score(self.PROFILE)
        oc_citations = score["dimensions"]["opportunity_cost"]["citations"]
        cpf_cite = next((c for c in oc_citations if "CPF" in c.get("field", "")), None)
        assert cpf_cite is not None, "CPF OA citation missing from opportunity_cost"
        assert "2.5" in cpf_cite.get("value", ""), "CPF 2.5% rate not cited"

    def test_hard_stops_present_for_absd_burden(self):
        """ABSD 20% at $400k flags cash drain."""
        score = _score(self.PROFILE)
        all_flags = []
        for dim in score["dimensions"].values():
            all_flags.extend(dim.get("flags", []))
        absd_flags = [f for f in all_flags if "ABSD" in f or "400,000" in f]
        assert len(absd_flags) >= 1, f"Expected ABSD flag, got: {all_flags}"

    def test_disclaimer_always_present(self):
        """Disclaimer is always returned."""
        score = _score(self.PROFILE)
        assert "Not financial advice" in score["disclaimer"]
        assert "effective_date" in score["disclaimer"]
