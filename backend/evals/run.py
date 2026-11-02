"""Run the evaluation set against real models and write a report.

    python -m evals.run [--models a,b,c] [--suites checkin,planning] [--out PATH]

Each model is called on its own (no fallback chain), through the same agent code the workflows
use, so the numbers describe the production path. Cases are synthetic (`evals/cases/`). Calls
that fail (rate limits, overload) are retried once and then counted as errors, not as wrong
answers.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

from app.agents.checkin import parse_checkin
from app.agents.planner import plan_day
from app.core.config import get_settings
from app.domain.models import Book, Goal, Task
from app.llm.gemini import GeminiProvider
from app.llm.router import LLMRouter
from app.services.context import RefMap
from app.services.scorecard import Habit, Metric, Scorecard
from evals.scoring import CaseScore, Summary, score_checkin, score_plan, summarise

CASES = Path(__file__).parent / "cases"
DEFAULT_OUT = Path(__file__).parents[2] / "docs" / "evals" / "results.md"
EPOCH = datetime(2026, 11, 1, tzinfo=UTC)

Results = dict[str, dict[str, tuple[Summary, list[CaseScore]]]]

CHECKIN_COLUMNS = [
    "Model", "Passed (of answered)", "Injection cases", "Finished-task precision", "Recall",
    "Habit accuracy", "Unsupported values", "Errors", "Latency p50 / max", "Tokens in / out",
]  # fmt: skip
PLANNING_COLUMNS = [
    "Model", "Passed (of answered)", "Injection cases", "Errors", "Latency p50 / max",
    "Tokens in / out",
]  # fmt: skip


def _task(i: int, spec: str | dict[str, Any]) -> Task:
    spec = {"title": spec} if isinstance(spec, str) else spec
    due = spec.get("due")
    return Task(
        id=f"task-{i}", url="", title=spec["title"], last_edited_time=EPOCH, status="Next",
        priority=spec.get("priority"), due=date.fromisoformat(str(due)) if due else None,
    )  # fmt: skip


def _router(provider: GeminiProvider, model: str) -> LLMRouter:
    return LLMRouter(provider, {"fast": [model], "smart": [model]}, daily_token_budget=10**9)


PAUSE_SECONDS = 0.0  # set by --pause: spacing between calls, for per-minute rate limits


async def _with_retry(call):
    """Retry once when the model never answered (rate limit, overload). An answer that failed
    validation is a real result and is not retried."""
    await asyncio.sleep(PAUSE_SECONDS)
    result = await call()
    if result.usage:
        return result
    await asyncio.sleep(max(10.0, PAUSE_SECONDS * 2))
    return await call()


def _no_answer(case: dict[str, Any]) -> CaseScore:
    return CaseScore(case["id"], False, ["no model answer"], bool(case.get("adversarial")), True)


def load_scorecard(data: dict[str, Any]) -> Scorecard:
    return Scorecard(
        habits=tuple(Habit(k, k.replace("_", " ").title(), d) for k, d in data["habits"].items()),
        metrics=tuple(
            Metric(k, k.replace("_", " ").title(), m.get("unit"), m.get("description"))
            for k, m in data["metrics"].items()
        ),
    )


async def run_checkin(
    provider, model, cases, scorecard: Scorecard
) -> tuple[list[CaseScore], list[dict], int]:
    scores, usage, errors = [], [], 0
    for case in cases:
        tasks = [_task(i, t) for i, t in enumerate(case["tasks"])]
        books = [
            Book(id=f"book-{i}", url="", title=b, last_edited_time=EPOCH)
            for i, b in enumerate(case.get("books", []))
        ]
        task_refs, book_refs = RefMap("T"), RefMap("B")
        for t in tasks:
            task_refs.add(t.id)
        for b in books:
            book_refs.add(b.id)

        async def call(tasks=tasks, books=books, task_refs=task_refs, book_refs=book_refs,
                       message=case["message"]):  # fmt: skip
            return await parse_checkin(
                _router(provider, model), {"text": message}, tasks=tasks, books=books,
                task_refs=task_refs, book_refs=book_refs, scorecard=scorecard,
            )  # fmt: skip

        result = await _with_retry(call)
        if result.source != "ai":
            errors += 1
            scores.append(_no_answer(case))
            continue
        usage += result.usage
        titles = {t.id: t.title for t in tasks}
        book_titles = {b.id: b.title for b in books}
        parsed = {
            "done": [titles[i] for i in result.done_ids],
            "partial": [titles[i] for i in result.partial_ids],
            "habits": result.habits,
            "sleep": result.metrics.get("sleep"),
            "screen_time": result.metrics.get("screen_time"),
            "pages": {book_titles[r["book_id"]]: r["pages"] for r in result.reading},
            "learned": result.learned,
            "new_tasks": result.new_tasks,
        }
        scores.append(score_checkin(case, parsed))
    return scores, usage, errors


async def run_planning(provider, model, suite) -> tuple[list[CaseScore], list[dict], int]:
    scores, usage, errors = [], [], 0
    day = date.fromisoformat(str(suite["day"]))
    for case in suite["cases"]:
        candidates = [_task(i, c) for i, c in enumerate(case["candidates"])]
        goals = [
            Goal(id=f"goal-{i}", url="", title=g, last_edited_time=EPOCH)
            for i, g in enumerate(case.get("goals", []))
        ]

        async def call(candidates=candidates, goals=goals, case=case):
            return await plan_day(
                _router(provider, model), day=day, slots=case["slots"], candidates=candidates,
                goals=goals, mode=case.get("mode", "fill"),
            )  # fmt: skip

        result = await _with_retry(call)
        if not result.usage:
            errors += 1
            scores.append(_no_answer(case))
            continue
        usage += result.usage
        titles = {t.id: t.title for t in candidates}
        valid = result.source == "ai"  # "rules" here means the model's plan was rejected
        picks = [titles[i] for i in result.task_ids] if valid else []
        scores.append(score_plan(case, picks, valid=valid))
    return scores, usage, errors


async def evaluate(models: list[str], suites: set[str]) -> Results:
    settings = get_settings()
    assert settings.gemini_api_key is not None
    provider = GeminiProvider(settings.gemini_api_key.get_secret_value())
    checkin_suite = yaml.safe_load((CASES / "checkin.yaml").read_text())
    checkin, scorecard = checkin_suite["cases"], load_scorecard(checkin_suite["scorecard"])
    planning = yaml.safe_load((CASES / "planning.yaml").read_text())
    results: Results = {}
    for model in models:
        results[model] = {}
        if "checkin" in suites:
            scores, usage, errors = await run_checkin(provider, model, checkin, scorecard)
            results[model]["checkin"] = (summarise(scores, usage), scores)
        if "planning" in suites:
            scores, usage, errors = await run_planning(provider, model, planning)
            results[model]["planning"] = (summarise(scores, usage), scores)
        for suite, (summary, _) in results[model].items():
            print(f"{model:28} {suite:9} {summary.passed}/{summary.cases} passed, "
                  f"{summary.errors} errors", flush=True)  # fmt: skip
    return results


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def _num(value: float | None, unit: str = "") -> str:
    return "—" if value is None else f"{value:,.0f}{unit}"


def _header(columns: list[str]) -> list[str]:
    return ["| " + " | ".join(columns) + " |", "|" + " --- |" * len(columns)]


def _row(model: str, suite: str, s: Summary) -> str:
    cells = [
        f"`{model}`",
        f"{s.passed}/{s.answered} ({_pct(s.pass_rate)})",
        f"{s.adversarial_passed}/{s.adversarial_total}",
    ]
    if suite == "checkin":
        cells += [_pct(s.done_precision), _pct(s.done_recall), _pct(s.habit_accuracy),
                  str(s.unexpected_fields)]  # fmt: skip
    cells += [
        str(s.errors),
        f"{_num(s.latency_p50_ms, ' ms')} / {_num(s.latency_max_ms, ' ms')}",
        f"{_num(s.input_tokens)} / {_num(s.output_tokens)}",
    ]
    return "| " + " | ".join(cells) + " |"


def render(results: Results, models: list[str], day: str) -> str:
    lines = [
        f"# Evaluation results ({day})",
        "",
        "Generated by `python -m evals.run`. Method and case design: [README.md](README.md).",
        "",
    ]
    for suite, title in (("checkin", "Check-in parsing"), ("planning", "Day planning")):
        ran = [m for m in models if suite in results.get(m, {})]
        if not ran:
            continue
        lines += [f"## {title}", ""]
        lines += _header(CHECKIN_COLUMNS if suite == "checkin" else PLANNING_COLUMNS)
        lines += [_row(m, suite, results[m][suite][0]) for m in ran]
        lines += ["", "Failed cases:", ""]
        for model in ran:
            failed = [c for c in results[model][suite][1] if not c.passed]
            if not failed:
                lines.append(f"- `{model}`: none")
            for c in failed:
                lines.append(f"- `{model}` · {c.case_id}: {'; '.join(c.errors)}")
        lines.append("")
    lines += [
        "Pass rates and injection results count answered cases only; calls the provider refused",
        "(rate limits, overload) are listed as errors. Latency is per model call and includes",
        "network time. Token counts include the model's internal reasoning tokens where reported.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    settings = get_settings()
    chains = settings.model_chain("fast") + settings.model_chain("smart")
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default=",".join(dict.fromkeys(chains)))
    parser.add_argument("--suites", default="checkin,planning")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--pause", type=float, default=0.0, help="seconds between calls")
    args = parser.parse_args()
    if settings.gemini_api_key is None:
        print("GEMINI_API_KEY is not set", file=sys.stderr)
        return 2
    global PAUSE_SECONDS
    PAUSE_SECONDS = args.pause
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    results = asyncio.run(evaluate(models, set(args.suites.split(","))))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(results, models, datetime.now(UTC).date().isoformat()))
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
