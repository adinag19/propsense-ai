"""
LLM-as-judge: secondary validation pass for high-risk PropSense outputs.

Triggered when ANY of these conditions are true:
  - ABSD >= 20% (SC/PR upgrader or foreigner — large irreversible sunk cost)
  - remaining_lease < 30 years (CPF proration edge case)
  - Hard stops are present in the score

Checks whether the LLM narrative contradicts the deterministic score numbers.
A contradiction means the narrative recommends buying when the score says Do Not Buy,
or downplays a hard stop that the scorer flagged.

Returns a dict — never raises. Failures are logged and treated as "pass".

Usage:
    from llm.judge import judge
    result = judge(score_dict, profile)
    if result["contradiction"]:
        # add warning to response
"""
from __future__ import annotations

import json
import os
from typing import Any

from dotenv import load_dotenv

load_dotenv()

MODEL = "gpt-4o-mini"
MAX_TOKENS = 256


def _is_high_risk(score: dict[str, Any], profile: dict[str, Any]) -> tuple[bool, list[str]]:
    """Return (triggered, reasons)."""
    reasons = []

    # ABSD >= 20%
    fin = score.get("dimensions", {}).get(
        "financial_readiness",
        score.get("dimensions", {}).get("financial_sustainability", {})
    )
    absd = (fin.get("details") or {}).get("absd", 0)
    price = profile.get("property_price", 1)
    if price > 0 and absd / price >= 0.20:
        reasons.append(f"ABSD {absd/price*100:.0f}% >= 20%")

    # Short lease
    if profile.get("lease_remaining", 99) < 30:
        reasons.append(f"Remaining lease {profile['lease_remaining']}yr < 30yr")

    # Hard stops present
    if score.get("hard_stops"):
        reasons.append(f"{len(score['hard_stops'])} hard stop(s) flagged")

    return bool(reasons), reasons


def _build_prompt(score: dict[str, Any], reasons: list[str]) -> str:
    overall = score["overall_score"]
    band = score["band"]
    hard_stops = score.get("hard_stops", [])
    narrative_summary = (score.get("narrative") or {}).get("summary", "")
    key_risks = (score.get("narrative") or {}).get("key_risks", [])

    hard_stop_text = (
        "\n".join(f"  - [{h['dimension']}] {h['flag']}" for h in hard_stops)
        if hard_stops else "  None"
    )

    return f"""You are a compliance checker for a Singapore property advisory system.

A deterministic scoring engine produced this result:
  Overall score: {overall}/100
  Band: {band}
  Hard stops:
{hard_stop_text}

High-risk triggers that caused this review:
  {', '.join(reasons)}

The AI narrator then wrote this summary:
  "{narrative_summary}"

Key risks cited by narrator:
  {json.dumps(key_risks)}

Your job: check for contradictions. A contradiction exists if:
1. The narrator recommends buying / sounds optimistic when band is "Do Not Buy" or "Marginal"
2. The narrator fails to mention a hard stop that was flagged
3. The narrator downplays or contradicts a high-risk trigger

Respond with ONLY valid JSON, no other text:
{{"ok": true, "issue": null}}
or
{{"ok": false, "issue": "<one sentence describing the contradiction>"}}"""


def judge(score: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """
    Run the LLM-as-judge on a high-risk score output.

    Returns:
        {
            "triggered":     bool  — whether judge was invoked
            "reasons":       list  — why it was triggered
            "contradiction": bool  — whether a contradiction was found
            "issue":         str | None — description of issue if found
            "judge_error":   str | None — if judge itself failed
        }
    """
    triggered, reasons = _is_high_risk(score, profile)

    if not triggered:
        return {"triggered": False, "reasons": [], "contradiction": False, "issue": None}

    # Skip if no narrative to check
    if not (score.get("narrative") or {}).get("summary"):
        return {
            "triggered": True,
            "reasons": reasons,
            "contradiction": False,
            "issue": None,
            "judge_error": "No narrative to validate",
        }

    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        response = client.chat.completions.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            temperature=0,
            messages=[{"role": "user", "content": _build_prompt(score, reasons)}],
        )
        raw = response.choices[0].message.content.strip()

        # Parse JSON response
        import re
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        result = json.loads(m.group()) if m else {"ok": True, "issue": None}

        return {
            "triggered": True,
            "reasons": reasons,
            "contradiction": not result.get("ok", True),
            "issue": result.get("issue"),
            "judge_error": None,
        }

    except Exception as exc:
        # Judge failure is non-fatal — treat as pass
        return {
            "triggered": True,
            "reasons": reasons,
            "contradiction": False,
            "issue": None,
            "judge_error": str(exc),
        }


if __name__ == "__main__":
    # Smoke test with a foreigner buying at 60% ABSD
    test_score = {
        "overall_score": 38,
        "band": "Do Not Buy",
        "hard_stops": [
            {"dimension": "financial_readiness", "flag": "Cash shortfall: need SGD 2,300,000"}
        ],
        "dimensions": {
            "financial_readiness": {
                "details": {"absd": 2_100_000},
                "flags": ["Cash shortfall"],
                "citations": [],
            }
        },
        "narrative": {
            "summary": "This looks like a great opportunity to enter the Singapore market with strong rental upside.",
            "key_risks": [],
        },
        "disclaimer": "Not financial advice.",
    }
    test_profile = {
        "citizenship": "FR",
        "property_price": 3_500_000,
        "lease_remaining": 99,
    }

    print("Running judge smoke test...")
    result = judge(test_score, test_profile)
    print(json.dumps(result, indent=2))
