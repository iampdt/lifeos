"""Drive a workflow from the terminal against Notion and the configured LLM.

    python -m scripts.run_workflow evening_checkin [--profile live|sandbox] [--writes]
        [--answer JSON_OR_TEXT ...] [--resume RUN_ID]

Each time the run waits, the request is printed and the next `--answer` is sent (a JSON object,
or plain text for a check-in). With no answers left it stops, and the run stays waiting in
Postgres: continue later with `--resume RUN_ID --answer ...`, or leave it and its proposals
expire.

Writes: `--writes` enables Notion writes for the sandbox profile only. The real workspace is
written only when LIFEOS_WRITES_ENABLED=true is set in the environment, never by a flag.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from contextlib import AsyncExitStack
from typing import Any

from app.api.main import build_services
from app.core.config import env_values, get_settings
from app.notion.mapping import load_mapping


def parse_answer(raw: str) -> Any:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"text": raw}
    return value if isinstance(value, dict) else {"text": raw}


def show(view) -> None:
    print(f"\nrun {view.id} · {view.workflow} · {view.status}")
    request = view.waiting_for
    if request:
        kind = request["type"]
        if kind == "question":
            print(request["prompt"])
            for t in request["tasks"]:
                print(
                    f"  {t['ref']:>4}  {t['title']}  [{t['status']}, {t['priority']}, {t['due']}]"
                )
            for b in request["books"]:
                print(f"  {b['ref']:>4}  {b['title']}")
        elif kind == "decisions":
            review = request["review"]
            print(f"Week {request['week']['label']} · metrics {json.dumps(request['metrics'])}")
            print(f"Review ({review.get('source')}): {review.get('summary')}")
            labels = {"wins": "win", "misses": "missed", "bottlenecks": "bottleneck",
                      "priorities": "priority"}  # fmt: skip
            for key, label in labels.items():
                for item in review.get(key) or []:
                    print(f"  {label}: {item}")
            for row in request["overdue"]:
                plan = row["recommended"]
                if plan == "carry_forward" and row["suggested_due"]:
                    plan += f" to {row['suggested_due']}"
                why = row["reason"]
                print(f"  {row['ref']:>4}  {row['title']}  due {row['due']} → {plan} ({why})")
            for row in request["inbox"]:
                print(f"  {row['ref']:>4}  [Inbox] {row['title']}")
        elif kind == "approval":
            print(f"Proposals (writes enabled: {request['writes_enabled']}):")
            for card in request["proposals"]:
                print(f"  {card['kind']:<22} {card['target']['title']!s:<40} "
                      f"{json.dumps(card['payload'], ensure_ascii=False)[:120]}")  # fmt: skip
                print(f"  {'':<22} why: {card['reason']}")
    if view.summary:
        print(json.dumps(view.summary, indent=2, ensure_ascii=False))
    if view.error:
        print("error:", view.error)
    usage = view.usage
    if usage.get("llm_calls"):
        print(f"LLM: {usage['llm_calls']} calls, {usage['model']}, "
              f"{usage['input_tokens']}+{usage['output_tokens']} tokens, "
              f"{usage['llm_latency_ms']} ms")  # fmt: skip


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workflow", nargs="?")
    parser.add_argument("--profile", choices=["live", "sandbox"], default="live")
    parser.add_argument("--writes", action="store_true", help="sandbox only")
    parser.add_argument("--answer", action="append", default=[])
    parser.add_argument("--resume", help="continue a waiting run")
    parser.add_argument("--inputs", default="{}", help="JSON inputs for a new run")
    args = parser.parse_args()

    settings = get_settings()
    if args.writes and args.profile != "sandbox":
        print("--writes only applies to the sandbox; the live workspace needs "
              "LIFEOS_WRITES_ENABLED=true", file=sys.stderr)  # fmt: skip
        return 2
    if args.profile == "sandbox":
        sandbox = load_mapping(settings.notion_mapping_path, env_values(), "sandbox")
        live = load_mapping(settings.notion_mapping_path, env_values(), "live")
        for name, spec in sandbox.items():  # never let a sandbox run touch the real Life OS
            if spec.configured:
                assert spec.data_source_id != live[name].data_source_id, name
    settings = settings.model_copy(
        update={
            "notion_profile": args.profile,
            "lifeos_writes_enabled": settings.lifeos_writes_enabled or args.writes,
        }
    )

    async with AsyncExitStack() as stack:
        services = await build_services(settings, stack)
        runner = services.runner
        if args.resume:
            view = await runner.get(uuid.UUID(args.resume))
        else:
            view = await runner.start(
                args.workflow, trigger="manual", inputs=json.loads(args.inputs)
            )
        show(view)
        answers = list(args.answer)
        while view.status.startswith("waiting") and answers:
            answer = parse_answer(answers.pop(0))
            print("\n→ answering:", json.dumps(answer, ensure_ascii=False)[:200])
            view = await runner.resume(uuid.UUID(view.id), answer)
            show(view)
    return 0 if view.status != "failed" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
