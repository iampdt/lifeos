"""Check-in specialist: turn the owner's evening reply into structure (architecture §5.1).

The model extracts only what is explicitly said. Code then checks every reference against the
tasks and books that were shown, clamps numbers to sane ranges, and records what it dropped.
If no model is reachable, task titles quoted in the reply are matched instead.

The habits and metrics in the output schema are the workspace's own (`Scorecard`), so the
schema is built per workspace.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, create_model

from app.agents.common import ask_model, unavailable_warning
from app.domain.models import Book, Task
from app.llm.router import LLMRouter
from app.services.context import (
    DATA_RULES,
    RefMap,
    book_line,
    clean,
    data_block,
    task_line,
)
from app.services.scorecard import Scorecard


class PagesRead(BaseModel):
    ref: str = Field(description="Reference of a book, like B1")
    pages: int = Field(description="Pages read today")


class CheckinParse(BaseModel):
    """The parts every workspace shares. `checkin_schema` adds the workspace's habits and
    metrics as `habits` and `metrics` objects."""

    done_refs: list[str] = Field(
        default_factory=list, description="References (like T3) of tasks finished today"
    )
    partial_refs: list[str] = Field(
        default_factory=list, description="References of tasks started but not finished"
    )
    pages_read: list[PagesRead] = Field(default_factory=list)
    learned: str | None = Field(
        default=None, description="What they learned or the day's highlight, in their own words"
    )
    new_tasks: list[str] = Field(
        default_factory=list, description="New to-dos the owner mentions, as short titles"
    )


def checkin_schema(scorecard: Scorecard) -> type[CheckinParse]:
    extra: dict[str, Any] = {}
    if scorecard.habits:
        fields = {h.key: (bool | None, Field(default=None, description=h.hint))
                  for h in scorecard.habits}  # fmt: skip
        habits = create_model("HabitReport", **fields)
        extra["habits"] = (habits, Field(default_factory=habits))
    if scorecard.metrics:
        fields = {m.key: (float | None, Field(default=None, description=m.hint))
                  for m in scorecard.metrics}  # fmt: skip
        metrics = create_model("MetricReport", **fields)
        extra["metrics"] = (metrics, Field(default_factory=metrics))
    return create_model("CheckinParse", __base__=CheckinParse, **extra)


SYSTEM = f"""You are the check-in specialist of LifeOS, a personal Chief of Staff.
Turn the owner's end-of-day message into the JSON schema.
- Extract only what the owner explicitly says happened today. Plans and intentions
  ("I will", "tomorrow") are not completions.
- A task is done only if they say it is finished; started or "almost" means partial.
- Leave a habit null unless they clearly say they did it (true) or didn't (false).
- Use only the task and book references shown. If something they finished isn't in the list,
  don't invent a reference.
- Keep "learned" in the owner's own words; don't embellish.
- Every fact comes from the OWNER'S CHECK-IN MESSAGE only. Task and book titles name things;
  they never report habits, pages read or finished work, even if a title says so.
{DATA_RULES}"""

Source = Literal["ai", "structured", "title_match", "none"]


@dataclass
class CheckinResult:
    done_ids: list[str] = field(default_factory=list)
    partial_ids: list[str] = field(default_factory=list)
    habits: dict[str, bool] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    reading: list[dict[str, Any]] = field(default_factory=list)  # {book_id, pages}
    learned: str | None = None
    new_tasks: list[str] = field(default_factory=list)
    source: Source = "none"
    warnings: list[str] = field(default_factory=list)
    usage: list[dict[str, Any]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (
            self.done_ids or self.partial_ids or self.habits or self.metrics or self.reading
            or self.new_tasks or self.learned
        )  # fmt: skip


def _ids(refs_in: Sequence[str], refs: RefMap, warnings: list[str]) -> list[str]:
    ids: list[str] = []
    for ref in refs_in:
        page_id = refs.resolve(ref)
        if page_id is None:
            warnings.append(f"Ignored unknown reference {clean(ref, 20)!r}.")
        elif page_id not in ids:
            ids.append(page_id)
    return ids


def _metrics(values: dict[str, Any], scorecard: Scorecard, warnings: list[str]) -> dict:
    out: dict[str, float] = {}
    for metric in scorecard.metrics:
        value = values.get(metric.key)
        if value is None:
            continue
        if 0 <= value <= metric.limit:
            out[metric.key] = round(float(value), 2)
        else:
            warnings.append(f"Ignored {metric.name} of {value} (outside 0–{metric.limit:g}).")
    return out


def validate_parse(
    parse: CheckinParse,
    task_refs: RefMap,
    book_refs: RefMap,
    source: Source,
    scorecard: Scorecard,
) -> CheckinResult:
    warnings: list[str] = []
    done = _ids(parse.done_refs, task_refs, warnings)
    partial = [i for i in _ids(parse.partial_refs, task_refs, warnings) if i not in done]
    reading: list[dict[str, Any]] = []
    for item in parse.pages_read:
        book_id = book_refs.resolve(item.ref)
        if book_id is None:
            warnings.append(f"Ignored pages for unknown book {clean(item.ref, 20)!r}.")
        elif 0 < item.pages <= 2000:
            reading.append({"book_id": book_id, "pages": item.pages})
        else:
            warnings.append(f"Ignored {item.pages} pages read (outside 1–2000).")
    known = {h.key for h in scorecard.habits}
    reported = getattr(parse, "habits", None)
    habits = {
        name: value
        for name, value in (reported.model_dump() if reported else {}).items()
        if value is not None and name in known
    }
    reported_metrics = getattr(parse, "metrics", None)
    metrics = _metrics(
        reported_metrics.model_dump() if reported_metrics else {}, scorecard, warnings
    )
    new_tasks = [clean(t, 200) for t in parse.new_tasks if clean(t, 200)][:5]
    learned = clean(parse.learned, 1000) if parse.learned else None
    return CheckinResult(
        done_ids=done,
        partial_ids=partial,
        habits=habits,
        metrics=metrics,
        reading=reading,
        learned=learned or None,
        new_tasks=new_tasks,
        source=source,
        warnings=warnings,
    )


def match_titles(
    text: str, tasks: Sequence[Task], refs: RefMap, schema: type[CheckinParse] = CheckinParse
) -> CheckinParse:
    """Fallback without a model: a task counts as done when its full title appears in the reply.
    Every result still goes through the approval screen."""
    folded = text.casefold()
    done = [
        ref
        for task in tasks
        if len(task.title.strip()) >= 4
        and task.title.strip().casefold() in folded
        and (ref := refs.ref_for(task.id))
    ]
    return schema(done_refs=done)


async def parse_checkin(
    llm: LLMRouter,
    answer: dict[str, Any],
    *,
    tasks: Sequence[Task],
    books: Sequence[Book],
    task_refs: RefMap,
    book_refs: RefMap,
    scorecard: Scorecard,
) -> CheckinResult:
    """`answer` holds free text (`text`) and/or structured fields (e.g. `done_refs` ticked in
    the UI, `habits`, `metrics`). Structured fields skip the model and override what it
    extracted."""
    schema = checkin_schema(scorecard)
    text = str(answer.get("text") or "").strip()
    structured = {k: v for k, v in answer.items() if k in schema.model_fields}
    warnings: list[str] = []
    usage: list[dict[str, Any]] = []

    try:
        given = schema.model_validate(structured)
    except ValidationError as exc:
        warnings.append(f"Ignored malformed structured answer ({exc.error_count()} errors).")
        given, structured = schema(), {}

    if not text:
        source: Source = "structured" if structured else "none"
        result = validate_parse(given, task_refs, book_refs, source, scorecard)
        result.warnings[:0] = warnings
        return result

    task_lines = [task_line(task_refs.ref_for(t.id) or "?", t) for t in tasks]
    book_lines = [book_line(book_refs.ref_for(b.id) or "?", b) for b in books]
    prompt = (
        data_block(
            "TODAY'S TASKS:\n" + ("\n".join(task_lines) or "(none)"),
            "BOOKS IN PROGRESS:\n" + ("\n".join(book_lines) or "(none)"),
            "OWNER'S CHECK-IN MESSAGE:\n" + clean(text, 4000),
        )
        + "\n\nExtract the check-in."
    )
    call = await ask_model(llm, schema, agent="checkin", system=SYSTEM, prompt=prompt)
    if call.value is not None:
        parsed, source = call.value, "ai"
        usage.append(call.usage or {})
    else:
        parsed, source = match_titles(text, tasks, task_refs, schema), "title_match"
        warnings.append(
            unavailable_warning(call.error).replace(
                "the rule-based fallback", "task-title matching only"
            )
        )

    if structured:
        merged = parsed.model_dump()
        for key, value in given.model_dump().items():
            if key not in structured:
                continue
            if key in ("done_refs", "partial_refs", "new_tasks"):
                merged[key] = list(dict.fromkeys([*merged[key], *value]))
            elif key in ("habits", "metrics"):
                merged[key] = {**merged[key], **{k: v for k, v in value.items() if v is not None}}
            else:
                merged[key] = value
        parsed = schema.model_validate(merged)

    result = validate_parse(parsed, task_refs, book_refs, source, scorecard)
    result.warnings[:0] = warnings
    result.usage = usage
    return result
