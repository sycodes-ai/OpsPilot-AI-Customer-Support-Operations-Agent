"""Run the OpsPilot evaluation set and write results.json and results.md.

Usage (from the repository root):
    python eval/run_eval.py                    # rules mode, no API key needed
    LLM_API_KEY=... python eval/run_eval.py --mode llm
    python eval/run_eval.py --min-pass 0.9     # exit 1 if the pass rate is lower (used in CI)
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.agent import Agent  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import init_db, make_engine, make_session_factory  # noqa: E402
from app.llm import LLMClient  # noqa: E402
from app.rag.retriever import Retriever  # noqa: E402
from app.seed import seed  # noqa: E402


def build_agent(mode: str) -> Agent:
    os.environ["AGENT_MODE"] = mode
    settings = Settings()
    engine = make_engine("sqlite://")
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        seed(s)
    llm = None
    if mode == "llm":
        if not settings.llm_api_key:
            sys.exit("LLM mode needs LLM_API_KEY (or OPENAI_API_KEY).")
        llm = LLMClient(settings.llm_api_key, settings.llm_base_url, settings.llm_model, settings.llm_timeout_s)
    return Agent(factory, Retriever.from_dir(ROOT / "backend" / "data" / "docs"), settings, llm=llm)


def check(case: dict, reply) -> dict[str, bool]:
    exp = case["expect"]
    text = reply.reply.lower()
    called = [t.name for t in reply.tool_calls]
    checks: dict[str, bool] = {}
    if "intent" in exp:
        checks["intent"] = reply.intent == exp["intent"]
    if "tools" in exp:
        checks["tool_selection"] = set(exp["tools"]) <= set(called)
    if exp.get("no_tools"):
        checks["tool_selection"] = not called
    if "tool_ok" in exp:
        relevant = [t for t in reply.tool_calls if t.name in set(exp.get("tools", []))] or reply.tool_calls
        checks["tool_result"] = bool(relevant) and relevant[-1].ok == exp["tool_ok"]
    if "refused" in exp or "escalated" in exp:
        checks["guardrail"] = reply.refused == exp.get("refused", False) and reply.escalated == exp.get("escalated", False)
    if "must_include" in exp:
        checks["content"] = all(s.lower() in text for s in exp["must_include"])
    if "must_not_include" in exp:
        checks["no_leak"] = not any(s.lower() in text for s in exp["must_not_include"])
    if "source" in exp:
        checks["grounding"] = exp["source"] in reply.sources
    return checks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["rules", "llm"], default="rules")
    ap.add_argument("--cases", default=str(ROOT / "eval" / "cases.yaml"))
    ap.add_argument("--out", default=str(ROOT / "eval" / "results"))
    ap.add_argument("--min-pass", type=float, default=0.0)
    args = ap.parse_args()

    cases = yaml.safe_load(Path(args.cases).read_text(encoding="utf-8"))
    agent = build_agent(args.mode)
    rows, latencies = [], []
    per_check: dict[str, list[bool]] = defaultdict(list)
    per_cat: dict[str, list[bool]] = defaultdict(list)

    for case in cases:
        history: list[dict] = []
        reply = None
        for turn in case["turns"]:
            reply = agent.handle(turn, history, session_id=f"eval-{case['id']}")
            history += [{"role": "user", "content": turn}, {"role": "assistant", "content": reply.reply}]
            latencies.append(reply.latency_ms)
        checks = check(case, reply)
        passed = all(checks.values())
        for k, v in checks.items():
            per_check[k].append(v)
        per_cat[case["category"]].append(passed)
        rows.append({"id": case["id"], "category": case["category"], "passed": passed, "checks": checks,
                     "intent": reply.intent, "tools": [t.name for t in reply.tool_calls], "mode": reply.mode,
                     "latency_ms": reply.latency_ms, "reply": reply.reply})

    n = len(rows)
    passed = sum(r["passed"] for r in rows)
    summary = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "mode": args.mode,
        "model": agent.settings.llm_model if args.mode == "llm" else None,
        "cases": n,
        "passed": passed,
        "pass_rate": round(passed / n, 3),
        "checks": {k: round(sum(v) / len(v), 3) for k, v in sorted(per_check.items())},
        "categories": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(per_cat.items())},
        "latency_ms": {"p50": round(statistics.median(latencies), 1),
                       "p95": round(sorted(latencies)[int(0.95 * (len(latencies) - 1))], 1)},
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"results-{args.mode}.json").write_text(json.dumps({"summary": summary, "cases": rows}, indent=2, ensure_ascii=False))

    lines = [f"# Evaluation results ({args.mode} mode)", "",
             f"Date: {summary['date']} · Cases: {n} · **Passed: {passed}/{n} ({summary['pass_rate']:.0%})**"
             + (f" · Model: {summary['model']}" if summary["model"] else ""), "",
             "| Check | Accuracy |", "|---|---|"]
    names = {"intent": "Intent detection", "tool_selection": "Tool selection", "tool_result": "Tool outcome",
             "guardrail": "Guardrail decision", "content": "Answer content", "no_leak": "No data leak", "grounding": "Cited correct source"}
    lines += [f"| {names.get(k, k)} | {v:.0%} |" for k, v in summary["checks"].items()]
    lines += ["", "| Category | Passed |", "|---|---|"] + [f"| {k} | {v} |" for k, v in summary["categories"].items()]
    lines += ["", f"Latency per turn: p50 {summary['latency_ms']['p50']} ms, p95 {summary['latency_ms']['p95']} ms.", ""]
    failed = [r for r in rows if not r["passed"]]
    lines += ["## Failures", ""] + ([f"- **{r['id']}** ({r['category']}): failed "
                                     f"{', '.join(k for k, v in r['checks'].items() if not v)}. Reply: \"{r['reply'][:160]}\""
                                     for r in failed] or ["None."])
    (out / f"results-{args.mode}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(summary, indent=2))
    return 0 if summary["pass_rate"] >= args.min_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
