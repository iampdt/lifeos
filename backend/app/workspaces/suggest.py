"""Onboarding suggestions: which database holds each module, which property is each field, and
what each status option means (multi-user §Generic workspace model).

Everything here is a guess the user confirms or adjusts on the setup screen, made from names and
property types only. A workspace duplicated from the LifeOS template matches every guess.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.workspaces.modules import MODULES

# Database titles that suggest a module (lowercase substrings, best first).
SOURCE_WORDS: dict[str, tuple[str, ...]] = {
    "tasks": ("tasks", "task", "to-do", "todo", "to do", "actions"),
    "habits": ("habits", "habit", "daily", "tracker", "scorecard", "log"),
    "weeks": ("weekly", "week"),
    "months": ("monthly", "month"),
    "goals": ("goals", "goal", "objectives", "okr"),
    "books": ("reading", "books", "book", "library"),
}

# Property names that suggest a field (lowercase substrings, best first).
FIELD_WORDS: dict[str, tuple[str, ...]] = {
    "status": ("status", "state", "stage"),
    "due": ("due", "deadline", "do date", "date", "when"),
    "priority": ("priority", "importance", "urgency"),
    "area": ("area", "category", "project", "context"),
    "completed": ("completed", "done on", "finished", "completion"),
    "recurring": ("recurring", "repeat", "routine"),
    "goal": ("goal", "objective"),
    "week": ("week",),
    "month": ("month",),
    "day": ("date", "day"),
    "notes": ("notes", "note", "journal", "reflection", "learned"),
    "start": ("start", "from", "begin"),
    "end": ("end", "until"),
    "review": ("review", "retro", "reflection", "summary", "notes"),
    "theme": ("theme", "focus"),
    "tasks": ("task",),
    "type": ("type", "horizon", "level"),
    "current": ("current", "progress", "actual"),
    "target": ("target",),
    "success_metric": ("metric", "success", "measure"),
    "deadline": ("deadline", "due", "target date", "end"),
    "review_notes": ("review", "notes"),
    "author": ("author", "writer"),
    "pages": ("pages", "page count", "length"),
    "pages_read": ("pages read", "read", "current page", "progress"),
    "started": ("started", "start"),
    "finished": ("finished", "completed", "end"),
    "lane": ("lane",),
    "up_next": ("up next", "queue", "order"),
    "shelf": ("shelf", "shelves", "tags"),
    "week_key": ("week key",),
    "month_key": ("month key",),
}

# Status options that suggest a meaning, per module (lowercase substrings, checked in order).
MEANING_WORDS: dict[str, dict[str, tuple[str, ...]]] = {
    "tasks": {
        "done": ("done", "complete", "finished", "closed", "shipped"),
        "cancelled": ("cancel", "dropped", "abandon", "won't", "wont", "not doing"),
        "doing": ("in progress", "doing", "started", "working", "active", "ongoing"),
        "waiting": ("waiting", "blocked", "on hold", "hold", "paused", "delegated"),
        "inbox": ("inbox", "backlog", "idea", "someday", "triage", "new"),
        "next": ("not started", "to do", "todo", "to-do", "next", "planned", "ready", "open"),
    },
    "weeks": {
        "closed": ("closed", "done", "complete", "past", "finished", "archived"),
        "current": ("current", "active", "in progress", "this"),
        "upcoming": ("upcoming", "planned", "next", "future", "not started"),
    },
    "goals": {
        "done": ("done", "achieved", "complete", "finished"),
        "dropped": ("dropped", "cancel", "abandon"),
        "active": ("active", "in progress", "doing", "current", "ongoing"),
        "planned": ("planned", "not started", "backlog", "next", "future", "someday"),
    },
    "books": {
        "finished": ("finished", "read", "done", "complete"),
        "abandoned": ("abandon", "dropped", "dnf", "gave up"),
        "reading": ("reading", "in progress", "current", "started"),
        "want": ("want", "to read", "wishlist", "queue", "next", "not started"),
    },
}
MEANING_WORDS["months"] = MEANING_WORDS["weeks"]

PRIORITY_RANKS: tuple[tuple[str, ...], ...] = (
    ("urgent", "critical", "p0", "highest", "asap"),
    ("high", "p1", "important"),
    ("medium", "normal", "p2", "mid", "moderate"),
    ("low", "p3", "later", "minor"),
    ("lowest", "p4", "someday", "trivial"),
)

UNIT_WORDS: tuple[tuple[str, str], ...] = (
    ("hour", "hours"), ("sleep", "hours"), ("screen", "hours"), ("min", "minutes"),
    ("step", "steps"), ("page", "pages"), ("kg", "kg"), ("km", "km"), ("glass", "glasses"),
)  # fmt: skip

TITLE_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%B %d, %Y", "%d %B %Y", "%b %d, %Y")

Property = dict[str, Any]  # {"name", "type", "options": [names]}


def describe_schema(data_source: dict[str, Any]) -> list[Property]:
    """A live data source's properties as {name, type, options}, in Notion's order."""
    out: list[Property] = []
    for name, prop in (data_source.get("properties") or {}).items():
        kind = prop.get("type")
        options = [o["name"] for o in ((prop.get(kind) or {}).get("options") or [])]
        out.append({"name": name, "type": kind, "options": options})
    return out


def _score(text: str, words: tuple[str, ...]) -> int:
    text = text.casefold()
    for rank, word in enumerate(words):
        if word in text:
            return len(words) - rank + (5 if text.strip() == word else 0)
    return 0


def suggest_sources(sources: list[dict[str, str]]) -> dict[str, str | None]:
    """Module → the data source whose title fits best; each data source is used once."""
    taken: set[str] = set()
    chosen: dict[str, str | None] = {}
    for module in MODULES:  # tasks first: it is the one that must exist
        free = [s for s in sources if s["id"] not in taken]
        ranked = sorted(((_score(s["title"], SOURCE_WORDS[module]), s["id"]) for s in free),
                        reverse=True)  # fmt: skip
        best = next((sid for score, sid in ranked if score > 0), None)
        chosen[module] = best
        if best:
            taken.add(best)
    return chosen


def suggest_module(module_name: str, properties: list[Property]) -> dict[str, Any]:
    """A proposed mapping for one module on one data source (properties and status meanings)."""
    module = MODULES[module_name]
    used: set[str] = set()
    mapped: dict[str, dict[str, Any]] = {}
    # Required fields first, so an optional one can't take the only property of the right type.
    order = sorted(module.fields, key=lambda f: not module.fields[f].required)
    for field in order:
        rule = module.fields[field]
        candidates = [p for p in properties if p["type"] in rule.types and p["name"] not in used]
        if not candidates:
            continue
        words = FIELD_WORDS.get(field, (field,))
        best = max(candidates, key=lambda p: _score(p["name"], words))
        if _score(best["name"], words) == 0 and not (rule.required or field == "title"):
            continue  # an optional field is only guessed from its name
        if _score(best["name"], words) == 0 and len(candidates) > 1 and field != "title":
            continue  # a required field is guessed by type alone only when there's one choice
        used.add(best["name"])
        spec: dict[str, Any] = {"name": best["name"], "type": best["type"]}
        if field == "priority":
            spec["values"] = rank_priorities(best["options"])
        elif rule.needs or rule.allows:
            spec["values"] = classify_options(module_name, best["options"], rule)
        mapped[field] = spec
    if module.free_fields:
        mapped.update(_habits_and_metrics(properties, used, mapped))
    return {"properties": mapped}


def classify_options(module_name: str, options: list[str], rule) -> dict[str, str]:
    """Each option goes to the meaning whose phrase it matches longest ("Not started" is
    `next`, not `doing`); each meaning takes the first option that lands on it."""
    words = MEANING_WORDS.get(module_name, {})
    meanings = [m for m in (*rule.needs, *rule.allows) if m in words]
    chosen: dict[str, str] = {}
    for option in options:
        text = option.casefold()
        matches = [(len(w), m) for m in meanings for w in words[m] if w in text]
        if matches:
            meaning = max(matches)[1]
            chosen.setdefault(meaning, option)
    return {m: chosen[m] for m in (*rule.needs, *rule.allows) if m in chosen}


def rank_priorities(options: list[str]) -> dict[str, str]:
    """Options from highest to lowest as p0, p1, …; unrecognised names keep Notion's order."""

    def rank(item: tuple[int, str]) -> tuple[int, int]:
        index, name = item
        for level, words in enumerate(PRIORITY_RANKS):
            if _score(name, words) > 0:
                return level, index
        return len(PRIORITY_RANKS), index

    ordered = [name for _, name in sorted(enumerate(options), key=rank)]
    return {f"p{i}": name for i, name in enumerate(ordered[:10])}


def field_key(name: str, taken: set[str]) -> str:
    """A mapping key for a habit or metric, from its property name."""
    key = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_") or "field"
    if key[0].isdigit():
        key = f"f_{key}"
    base, n = key, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    taken.add(key)
    return key


def _habits_and_metrics(
    properties: list[Property], used: set[str], mapped: dict[str, Any]
) -> dict[str, Any]:
    taken = set(MODULES["habits"].fields) | set(mapped)
    out: dict[str, Any] = {}
    for prop in properties:
        if prop["name"] in used or prop["type"] not in ("checkbox", "number"):
            continue
        spec: dict[str, Any] = {"name": prop["name"], "type": prop["type"]}
        if prop["type"] == "number":
            spec["unit"] = next((u for w, u in UNIT_WORDS if w in prop["name"].casefold()), None)
        out[field_key(prop["name"], taken)] = spec
    return out


def detect_date_format(titles: list[str]) -> str | None:
    """The format that parses every sample title; day-first when both orders fit."""
    samples = [t.strip() for t in titles if t and t.strip()]
    if not samples:
        return None
    for fmt in TITLE_DATE_FORMATS:
        try:
            for title in samples:
                datetime.strptime(title, fmt)
        except ValueError:
            continue
        return fmt
    return None
