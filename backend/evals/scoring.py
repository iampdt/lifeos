"""Score agent outputs against eval cases. Pure functions, no I/O, unit-tested.

Results use task and book titles, not IDs, so a report reads as plain English.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from statistics import median
from typing import Any

NUMBER_TOLERANCE = 0.25  # hours


@dataclass
class CaseScore:
    case_id: str
    passed: bool
    errors: list[str] = field(default_factory=list)
    adversarial: bool = False
    no_answer: bool = False  # the call failed (rate limit, overload): not scored
    # Counts for precision/recall of finished tasks (check-in only).
    done_tp: int = 0
    done_fp: int = 0
    done_fn: int = 0
    habits_right: int = 0
    habits_total: int = 0
    unexpected_fields: int = 0  # values the message didn't support (hallucinations)


def _set_errors(label: str, expected: Iterable[str], got: Iterable[str]) -> list[str]:
    expected, got = set(expected), set(got)
    errors = [f"{label}: missed {t!r}" for t in sorted(expected - got)]
    errors += [f"{label}: unexpected {t!r}" for t in sorted(got - expected)]
    return errors


def score_checkin(case: dict[str, Any], parsed: dict[str, Any]) -> CaseScore:
    """`parsed` holds titles: done, partial, habits, sleep, screen_time, pages ({book: n}),
    learned, new_tasks."""
    expect = case.get("expect", {})
    score = CaseScore(case["id"], True, adversarial=bool(case.get("adversarial")))

    done_expected, done_got = set(expect.get("done", [])), set(parsed["done"])
    score.done_tp = len(done_expected & done_got)
    score.done_fp = len(done_got - done_expected)
    score.done_fn = len(done_expected - done_got)
    score.errors += _set_errors("done", done_expected, done_got)
    score.errors += _set_errors("partial", expect.get("partial", []), parsed["partial"])

    habits_expected: dict[str, bool] = expect.get("habits", {})
    optional = set(case.get("optional_habits", []))
    for name, value in habits_expected.items():
        score.habits_total += 1
        if parsed["habits"].get(name) == value:
            score.habits_right += 1
        else:
            score.errors.append(f"habit {name}: expected {value}, got {parsed['habits'].get(name)}")
    for name, value in parsed["habits"].items():
        if name not in habits_expected and name not in optional:
            score.unexpected_fields += 1
            score.errors.append(f"habit {name}: unexpected {value}")

    for name in ("sleep", "screen_time"):
        expected, got = expect.get(name), parsed[name]
        if expected is None and got is not None:
            score.unexpected_fields += 1
            score.errors.append(f"{name}: unexpected {got}")
        elif expected is not None and (got is None or abs(got - expected) > NUMBER_TOLERANCE):
            score.errors.append(f"{name}: expected {expected}, got {got}")

    pages_expected: dict[str, int] = expect.get("pages", {})
    if parsed["pages"] != pages_expected:
        if set(parsed["pages"]) - set(pages_expected):
            score.unexpected_fields += 1
        score.errors.append(f"pages: expected {pages_expected}, got {parsed['pages']}")

    new_expected = expect.get("new_tasks", 0)
    if len(parsed["new_tasks"]) != new_expected:
        if len(parsed["new_tasks"]) > new_expected:
            score.unexpected_fields += 1
        score.errors.append(f"new tasks: expected {new_expected}, got {parsed['new_tasks']}")
    joined = " ".join(parsed["new_tasks"]).casefold()
    for keyword in expect.get("new_task_keywords", []):
        if keyword.casefold() not in joined:
            score.errors.append(f"new tasks: no mention of {keyword!r}")

    if "learned" in expect and bool(parsed["learned"]) != expect["learned"]:
        score.errors.append(f"learned: expected {'text' if expect['learned'] else 'nothing'}")

    score.passed = not score.errors
    return score


def score_plan(case: dict[str, Any], picks: Sequence[str], valid: bool) -> CaseScore:
    score = CaseScore(case["id"], True, adversarial=bool(case.get("adversarial")))
    if not valid:
        score.errors.append("plan rejected by validation (unknown, duplicate or too many tasks)")
    if len(picks) > case["slots"]:
        score.errors.append(f"picked {len(picks)} tasks for {case['slots']} slots")
    for title in case.get("must_pick", []):
        if title not in picks:
            score.errors.append(f"missed {title!r}")
    for title in case.get("must_not_pick", []):
        if title in picks:
            score.errors.append(f"picked {title!r}")
    score.passed = not score.errors
    return score


def score_knowledge(
    case: dict[str, Any], answer: str, found: bool, cited: Sequence[str]
) -> CaseScore:
    """`cited` names each cited passage as "Page › Heading"."""
    score = CaseScore(case["id"], True, adversarial=bool(case.get("adversarial")))
    if found != case["found"]:
        score.errors.append(f"found: expected {case['found']}, got {found}")
    for source in case.get("must_cite", []):
        if source not in cited:
            score.errors.append(f"didn't cite {source!r}")
    for source in case.get("must_not_cite", []):
        if source in cited:
            score.errors.append(f"cited {source!r}")
    text = answer.casefold()
    for word in case.get("must_mention", []):
        if word.casefold() not in text:
            score.errors.append(f"answer doesn't mention {word!r}")
    for word in case.get("must_not_mention", []):
        if word.casefold() in text:
            score.errors.append(f"answer mentions {word!r}")
    score.passed = not score.errors
    return score


def score_goals(case: dict[str, Any], notes: dict[str, str], steps: dict[str, str]) -> CaseScore:
    """`notes` and `steps` are keyed by goal title."""
    score = CaseScore(case["id"], True, adversarial=bool(case.get("adversarial")))
    for goal in case["goals"]:
        if not notes.get(goal["title"]):
            score.errors.append(f"no note for {goal['title']!r}")
    score.errors += _set_errors("next step", case.get("needs_step", []), steps)
    for title, step in steps.items():
        keywords = case.get("step_keywords", {}).get(title)
        if keywords and not any(k.casefold() in step.casefold() for k in keywords):
            score.errors.append(f"step for {title!r} is off-topic: {step!r}")
    written = " ".join([*notes.values(), *steps.values()]).casefold()
    for word in case.get("must_not_mention", []):
        if word.casefold() in written:
            score.errors.append(f"mentions {word!r}")
    score.passed = not score.errors
    return score


@dataclass
class Summary:
    cases: int
    answered: int
    passed: int  # of the answered cases
    errors: int  # calls that never produced a model answer
    adversarial_passed: int
    adversarial_total: int
    done_precision: float | None
    done_recall: float | None
    habit_accuracy: float | None
    unexpected_fields: int
    latency_p50_ms: float | None
    latency_max_ms: float | None
    input_tokens: float | None
    output_tokens: float | None

    @property
    def pass_rate(self) -> float | None:
        return self.passed / self.answered if self.answered else None


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def summarise(scores: Sequence[CaseScore], usage: Sequence[dict[str, Any]]) -> Summary:
    latencies = [u["latency_ms"] for u in usage if "latency_ms" in u]
    answered = [s for s in scores if not s.no_answer]
    tp = sum(s.done_tp for s in answered)
    adversarial = [s for s in answered if s.adversarial]
    return Summary(
        cases=len(scores),
        answered=len(answered),
        passed=sum(s.passed for s in answered),
        errors=len(scores) - len(answered),
        adversarial_passed=sum(s.passed for s in adversarial),
        adversarial_total=len(adversarial),
        done_precision=_ratio(tp, tp + sum(s.done_fp for s in answered)),
        done_recall=_ratio(tp, tp + sum(s.done_fn for s in answered)),
        habit_accuracy=_ratio(
            sum(s.habits_right for s in answered), sum(s.habits_total for s in answered)
        ),
        unexpected_fields=sum(s.unexpected_fields for s in answered),
        latency_p50_ms=median(latencies) if latencies else None,
        latency_max_ms=max(latencies) if latencies else None,
        input_tokens=(sum(u["input_tokens"] for u in usage) / len(usage)) if usage else None,
        output_tokens=(sum(u["output_tokens"] for u in usage) / len(usage)) if usage else None,
    )
