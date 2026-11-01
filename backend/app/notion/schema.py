"""Schema drift detection: compare a live data source with what the mapping expects.

If a required property is missing, has changed type, or lost an option the code depends on,
the data source is reported as drifted (architecture §3.2), in `GET /notion/status` and
`scripts/notion_check.py`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from app.notion.client import NotionClient
from app.notion.codecs import plain_text
from app.notion.mapping import DataSourceSpec

OPTION_TYPES = ("select", "status", "multi_select")


@dataclass(frozen=True)
class DriftIssue:
    field: str
    property_name: str
    problem: str  # missing | optional_missing | wrong_type | missing_option
    detail: str = ""

    @property
    def blocking(self) -> bool:
        return self.problem != "optional_missing"


@dataclass
class SchemaReport:
    logical_name: str
    data_source_id: str
    title: str
    schema_hash: str
    issues: list[DriftIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(issue.blocking for issue in self.issues)


def _option_names(prop: dict[str, Any]) -> set[str]:
    kind = prop.get("type")
    if kind not in OPTION_TYPES:
        return set()
    return {option["name"] for option in (prop.get(kind) or {}).get("options", [])}


def schema_hash(properties: dict[str, Any]) -> str:
    canonical = sorted(
        [name, prop.get("type"), sorted(_option_names(prop))] for name, prop in properties.items()
    )
    return hashlib.sha256(json.dumps(canonical).encode()).hexdigest()[:16]


def compare_schema(spec: DataSourceSpec, properties: dict[str, Any]) -> list[DriftIssue]:
    issues: list[DriftIssue] = []
    for field_name, expected in spec.properties.items():
        actual = properties.get(expected.name)
        if actual is None:
            problem = "missing" if expected.required else "optional_missing"
            issues.append(DriftIssue(field_name, expected.name, problem))
            continue
        if actual.get("type") != expected.type:
            issues.append(
                DriftIssue(
                    field_name,
                    expected.name,
                    "wrong_type",
                    f"expected {expected.type}, found {actual.get('type')}",
                )
            )
            continue
        options = _option_names(actual)
        for option in expected.values.values():
            if option not in options:
                issues.append(DriftIssue(field_name, expected.name, "missing_option", option))
    return issues


async def check_data_source(client: NotionClient, spec: DataSourceSpec) -> SchemaReport:
    if not spec.data_source_id:
        raise ValueError(f"{spec.logical_name}: no data-source ID configured ({spec.env})")
    data_source = await client.get_data_source(spec.data_source_id)
    properties = data_source.get("properties", {})
    return SchemaReport(
        logical_name=spec.logical_name,
        data_source_id=spec.data_source_id,
        title=plain_text(data_source.get("title")),
        schema_hash=schema_hash(properties),
        issues=compare_schema(spec, properties),
    )
