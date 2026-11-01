"""Pieces every workflow shares: state basics, the approval step and execution.

Approval is a LangGraph `interrupt()`: the run pauses (checkpointed) until the owner answers.
The answer approves "all" or a list of proposal IDs, optionally with edits; every other
proposal of the run is rejected, so nothing lingers half-decided.
"""

from __future__ import annotations

import operator
import uuid
from collections.abc import Sequence
from typing import Annotated, Any, TypedDict

from langgraph.graph import StateGraph
from langgraph.types import interrupt
from sqlalchemy import select

from app.db.models import ActionProposal
from app.domain.models import Task
from app.execution.proposals import ProposalError, ProposalService
from app.workflows.deps import WorkflowDeps


class BaseState(TypedDict, total=False):
    run_id: str
    inputs: dict[str, Any]
    today: str
    titles: dict[str, str]  # page id → title, for approval cards and summaries
    proposal_ids: list[str]
    decisions: dict[str, Any]
    results: list[dict[str, Any]]
    summary: dict[str, Any]
    warnings: Annotated[list[str], operator.add]
    llm: Annotated[list[dict[str, Any]], operator.add]


def dump_tasks(tasks: Sequence[Task]) -> list[dict[str, Any]]:
    return [t.model_dump(mode="json") for t in tasks]


def load_tasks(data: Sequence[dict[str, Any]] | None) -> list[Task]:
    return [Task.model_validate(item) for item in data or []]


def run_uuid(state: BaseState) -> uuid.UUID | None:
    return uuid.UUID(state["run_id"]) if state.get("run_id") else None


def _card_title(row: ActionProposal, titles: dict[str, str]) -> str:
    payload = row.effective_payload
    if row.target_title:
        return row.target_title
    if row.target_page_id and row.target_page_id in titles:
        return titles[row.target_page_id]
    if "title" in payload:
        return str(payload["title"])
    if "day" in payload:
        return f"Scorecard {payload['day']}"
    return row.target_page_id or row.kind


async def proposal_cards(
    deps: WorkflowDeps, ids: Sequence[str], titles: dict[str, str]
) -> list[dict[str, Any]]:
    async with deps.session_factory() as session:
        rows = {
            str(r.id): r
            for r in await session.scalars(
                select(ActionProposal).where(ActionProposal.id.in_([uuid.UUID(i) for i in ids]))
            )
        }
    cards = []
    for proposal_id in ids:
        row = rows.get(proposal_id)
        if row is None:
            continue
        cards.append(
            {
                "id": proposal_id,
                "kind": row.kind,
                "status": row.status,
                "importance": row.importance,
                "target": {
                    "data_source": row.target_data_source,
                    "page_id": row.target_page_id,
                    "title": _card_title(row, titles),
                },
                "before": row.before_snapshot,
                "payload": row.effective_payload,
                "reason": row.reason,
                "rule_ids": list(row.rule_ids or []),
                "depends_on": [str(d) for d in row.depends_on or []],
                "expires_at": row.expires_at.isoformat(),
            }
        )
    return cards


async def apply_decisions(
    deps: WorkflowDeps, ids: Sequence[str], answer: Any, *, actor: str = "owner"
) -> dict[str, Any]:
    answer = answer if isinstance(answer, dict) else {}
    requested = answer.get("approve")
    if requested == "all":
        approve = set(ids)
    elif isinstance(requested, list):
        approve = {str(i) for i in requested} & set(ids)
    else:
        approve = set()
    edits = answer.get("edits") if isinstance(answer.get("edits"), dict) else {}
    note = str(answer["note"])[:500] if answer.get("note") else None

    approved: list[str] = []
    rejected: list[str] = []
    errors: dict[str, str] = {}
    for proposal_id in ids:
        pid = uuid.UUID(proposal_id)
        try:
            async with deps.session_factory() as session, session.begin():
                service = ProposalService(session, clock=deps.clock, tz=deps.tz)
                row = await session.get(ActionProposal, pid)
                if row is not None and row.status == "approved" and proposal_id in approve:
                    approved.append(proposal_id)  # approved earlier today (same proposal)
                    continue
                if row is None or row.status != "pending":
                    errors[proposal_id] = f"already {row.status if row else 'missing'}"
                    continue
                if proposal_id in approve:
                    edit = (
                        edits.get(proposal_id) if isinstance(edits.get(proposal_id), dict) else None
                    )
                    await service.approve(pid, edits=edit, note=note, actor=actor)
                    approved.append(proposal_id)
                else:
                    await service.reject(pid, note=note or "not approved", actor=actor)
                    rejected.append(proposal_id)
        except ProposalError as exc:
            errors[proposal_id] = str(exc)
    return {"approved": approved, "rejected": rejected, "errors": errors}


def approval_request(workflow: str, cards: list[dict[str, Any]], writes_enabled: bool) -> dict:
    return {
        "type": "approval",
        "workflow": workflow,
        "proposals": cards,
        "writes_enabled": writes_enabled,
        "accepts": {
            "approve": '"all", or a list of proposal ids; everything else is rejected',
            "edits": "optional {proposal_id: {field: value}}",
            "note": "optional text",
        },
    }


def add_approval_flow(
    graph: StateGraph, deps: WorkflowDeps, workflow: str, *, after: str, then: str
) -> None:
    """after → (approve → execute →) then. Skips approval when nothing was proposed."""

    async def approve(state: BaseState) -> dict[str, Any]:
        ids = state.get("proposal_ids") or []
        cards = await proposal_cards(deps, ids, state.get("titles") or {})
        answer = interrupt(approval_request(workflow, cards, deps.writes_enabled))
        return {"decisions": await apply_decisions(deps, ids, answer)}

    async def execute(state: BaseState) -> dict[str, Any]:
        approved = (state.get("decisions") or {}).get("approved") or []
        if not approved:
            return {"results": []}
        results = await deps.executor(f"agent:{workflow}").execute_approved(
            [uuid.UUID(i) for i in approved]
        )
        return {
            "results": [
                {
                    "proposal_id": str(r.proposal_id),
                    "outcome": r.outcome,
                    "page_id": r.page_id,
                    "detail": r.detail,
                }
                for r in results
            ]  # fmt: skip
        }

    graph.add_node("approve", approve)
    graph.add_node("execute", execute)
    graph.add_conditional_edges(
        after, lambda s: "approve" if s.get("proposal_ids") else then, ["approve", then]
    )
    graph.add_edge("approve", "execute")
    graph.add_edge("execute", then)


def outcome_counts(results: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        counts[result["outcome"]] = counts.get(result["outcome"], 0) + 1
    return counts


def execution_note(state: BaseState, writes_enabled: bool) -> str | None:
    if not (state.get("decisions") or {}).get("approved"):
        return None
    if not writes_enabled:
        return "Writes are disabled (LIFEOS_WRITES_ENABLED=false): nothing was changed in Notion."
    return None
