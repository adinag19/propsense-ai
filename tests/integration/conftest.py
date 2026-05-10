"""
Pytest plugin — saves a JSON pass/fail summary to results/ after every run.
The rich evidence report is generated separately via generate_report.py.
"""
import json
from datetime import datetime
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

_results: list[dict] = []


def pytest_runtest_logreport(report):
    if report.when == "call":
        _results.append({
            "test": report.nodeid,
            "passed": report.passed,
            "failed": report.failed,
            "duration_s": round(report.duration, 3),
            "failure_message": str(report.longrepr) if report.failed else None,
        })


def pytest_sessionfinish(session, exitstatus):
    if not _results:
        return

    passed = sum(1 for r in _results if r["passed"])
    failed = sum(1 for r in _results if r["failed"])
    date_str = datetime.now().strftime("%Y-%m-%d_%H-%M")

    summary = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "exit_status": exitstatus,
        "total": len(_results),
        "passed": passed,
        "failed": failed,
        "tests": _results,
    }

    out = RESULTS_DIR / f"pytest_summary_{date_str}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n[saved] Test summary -> {out}")
