"""Read-only Phase 3 check against the real workspace (or the sandbox).

    python -m scripts.notion_check [--profile live|sandbox] [--today YYYY-MM-DD]

1. Confirms the token and which workspace it belongs to.
2. Fetches every configured data source and reports schema drift.
3. If a data source isn't reachable, tries its database ID to list the API's data-source IDs.
4. Counts tasks in each mirrored view.
5. Checks that LifeOS's own week/month keys group tasks exactly like the Notion formulas.

It never writes to Notion. Output is counts and IDs only, no page contents.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import date, datetime

from app.core.config import env_values, get_settings
from app.notion.client import NotionClient
from app.notion.errors import NotionError, NotionNotShared
from app.notion.mapping import load_mapping
from app.notion.rate_limiter import AsyncTokenBucket
from app.notion.schema import check_data_source
from app.notion.workspace import build_workspace
from app.services.calendar import DEFAULT_TZ, month_key, week_key


def same_grouping(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Pairs (notion_key, lifeos_key) must form a one-to-one mapping; return the conflicts."""
    forward: dict[str, str] = {}
    backward: dict[str, str] = {}
    conflicts = []
    for notion_value, lifeos_value in pairs:
        if forward.setdefault(notion_value, lifeos_value) != lifeos_value or (
            backward.setdefault(lifeos_value, notion_value) != notion_value
        ):
            conflicts.append((notion_value, lifeos_value))
    return conflicts


async def main(profile: str, today: date) -> int:
    settings = get_settings()
    if settings.notion_token is None:
        print("NOTION_TOKEN is not set in .env")
        return 2
    specs = load_mapping(settings.notion_mapping_path, env_values(), profile)  # type: ignore[arg-type]
    limiter = AsyncTokenBucket(rate=settings.notion_requests_per_second)
    failures = 0

    async with NotionClient(
        settings.notion_token.get_secret_value(),
        base_url=settings.notion_base_url,
        notion_version=settings.notion_version,
        rate_limiter=limiter,
    ) as client:
        me = await client.get_self()
        print(
            f"Integration: {me.get('name')} | workspace: {me.get('bot', {}).get('workspace_name')}"
        )
        print(f"Profile: {profile} | today: {today.isoformat()}\n")

        print("Schema checks")
        reachable = {}
        for logical, spec in specs.items():
            if not spec.configured:
                print(f"  - {logical:7} not configured ({spec.env} unset)")
                continue
            try:
                report = await check_data_source(client, spec)
            except NotionNotShared:
                failures += 1
                print(
                    f"  ✗ {logical:7} not reachable: not found or not connected to the integration"
                )
                if spec.database_id:
                    try:
                        database = await client.get_database(spec.database_id)
                        ids = [
                            (s.get("id"), s.get("name")) for s in database.get("data_sources", [])
                        ]
                        print(f"      database is reachable; its data sources: {ids}")
                    except NotionError:
                        print("      database also not reachable → connect the page to LifeOS")
                continue
            reachable[logical] = report
            status = "✓" if report.ok else "✗"
            failures += 0 if report.ok else 1
            print(f"  {status} {logical:7} '{report.title}' schema {report.schema_hash}")
            for issue in report.issues:
                print(
                    f"      {issue.problem}: {issue.field} → '{issue.property_name}' {issue.detail}"
                )

        workspace = build_workspace(client, {k: specs[k] for k in reachable}, DEFAULT_TZ)

        if workspace.tasks and reachable["tasks"].ok:
            tasks = workspace.tasks
            print("\nTask views (mirrored in code)")
            for label, view in (
                ("All Active", tasks.active),
                ("Today", lambda: tasks.today(today)),
                ("Overdue / Carry Forward", lambda: tasks.overdue(today)),
                ("Inbox", tasks.inbox),
                ("Next 7 Days", lambda: tasks.next_7_days(today)),
            ):
                rows = await view()
                print(f"  {label:24} {len(rows):3}  ids: {[t.id[-6:] for t in rows][:12]}")

            everything = await tasks.all()
            by_status = dict(Counter(t.status for t in everything))
            print(f"\nAll tasks: {len(everything)}  by status: {by_status}")
            dated = [t for t in everything if t.due]
            week_pairs = [(t.week_key, week_key(t.due)) for t in dated if t.week_key]
            month_pairs = [(t.month_key, month_key(t.due)) for t in dated if t.month_key]
            print("\nWeek/Month key check (Notion formula vs LifeOS calendar)")
            if week_pairs:
                print(
                    f"  sample Notion Week Key: {week_pairs[0][0]!r} ↔ LifeOS {week_pairs[0][1]!r}"
                )
            for name, pairs in (("Week Key", week_pairs), ("Month Key", month_pairs)):
                if not pairs:
                    # Not a LifeOS failure: LifeOS derives keys from Due (rule R8).
                    print(f"  ! {name}: the Notion formula returned no text for any dated task")
                    continue
                conflicts = same_grouping(pairs)
                mark = "✓" if not conflicts else "✗"
                failures += 1 if conflicts else 0
                print(f"  {mark} {name}: {len(pairs)} compared, {len(conflicts)} conflicts")
                for notion_value, lifeos_value in conflicts[:5]:
                    print(f"      Notion {notion_value!r} vs LifeOS {lifeos_value!r}")

        for name in ("goals", "habits", "weeks", "months", "books"):
            repo = getattr(workspace, name)
            if repo is None or not reachable[name].ok:
                continue
            rows = await repo.all()
            extra = ""
            if name == "habits":
                unparsed = [r.title for r in rows if r.day is None]
                extra = f" unparsed titles: {len(unparsed)}"
            if name == "books":
                extra = f" in queue: {sum(1 for b in rows if b.status)}"
            print(f"  {name:7} rows: {len(rows)}{extra}")

        print(f"\nNotion requests: {client.request_count} (retries: {client.retry_count})")
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--profile", choices=["live", "sandbox"], default="live")
    parser.add_argument("--today", type=date.fromisoformat, default=datetime.now(DEFAULT_TZ).date())
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.profile, args.today)))
