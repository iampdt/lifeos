"""Mappings: logical names → data-source IDs and exact property names.

Code refers to logical fields (`tasks.status`), never to Notion's display names, so renaming a
property in Notion is a configuration change. A mapping comes from one of two places:
- `config/notion_mapping.yaml` for the workspace configured in the environment (the owner's),
  with data-source IDs from `NOTION_*` (or `NOTION_SANDBOX_*` for the `sandbox` profile);
- an account's saved workspace (`workspaces.mapping`), built during onboarding.

Habits are generic: in the habits data source every checkbox property is a habit and every
number property a metric, whatever they are called.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

Profile = Literal["live", "sandbox"]

PROPERTY_TYPES = {
    "title",
    "rich_text",
    "select",
    "status",
    "multi_select",
    "date",
    "checkbox",
    "number",
    "url",
    "relation",
    "formula",
    "rollup",
    "created_time",
    "last_edited_time",
}


class PropertySpec(BaseModel):
    name: str
    type: str
    # Semantic value → Notion option name, e.g. {"done": "Done"}. Each must exist as an option.
    values: dict[str, str] = Field(default_factory=dict)
    required: bool = True
    # Habits and metrics: what counts as doing the habit, and the metric's unit ("hours").
    description: str | None = None
    unit: str | None = None

    @model_validator(mode="after")
    def _known_type(self) -> PropertySpec:
        if self.type not in PROPERTY_TYPES:
            raise ValueError(f"unsupported property type {self.type!r} for {self.name!r}")
        return self

    def value(self, key: str) -> str:
        return self.values[key]


class DataSourceSpec(BaseModel):
    logical_name: str
    env: str | None = None  # environment variable holding the ID (environment mappings only)
    database_env: str | None = None
    data_source_id: str | None = None
    database_id: str | None = None
    properties: dict[str, PropertySpec]
    # Habits without a date property: how the title holds the day (strftime), e.g. "%d/%m/%Y".
    date_format: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.data_source_id)

    def prop(self, field: str) -> PropertySpec:
        return self.properties[field]

    def has(self, field: str) -> bool:
        return field in self.properties

    def has_value(self, field: str, key: str) -> bool:
        return field in self.properties and key in self.properties[field].values

    def fields_of_type(self, *types: str) -> list[str]:
        return [name for name, prop in self.properties.items() if prop.type in types]

    @property
    def habit_fields(self) -> list[str]:
        """Habits data source: every checkbox property is a habit."""
        return self.fields_of_type("checkbox")

    @property
    def metric_fields(self) -> list[str]:
        """Habits data source: every number property is a metric (sleep, screen time, …)."""
        return self.fields_of_type("number")


def env_name(name: str, profile: Profile) -> str:
    if profile == "live":
        return name
    return name.replace("NOTION_", "NOTION_SANDBOX_", 1)


def load_mapping(
    path: Path, env: Mapping[str, str], profile: Profile = "live"
) -> dict[str, DataSourceSpec]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    specs: dict[str, DataSourceSpec] = {}
    for logical, body in raw["data_sources"].items():
        ds_env = env_name(body["env"], profile)
        db_env = env_name(body["database_env"], profile) if body.get("database_env") else None
        specs[logical] = DataSourceSpec(
            logical_name=logical,
            env=ds_env,
            database_env=db_env,
            data_source_id=env.get(ds_env) or None,
            database_id=(env.get(db_env) or None) if db_env else None,
            properties={k: PropertySpec(**v) for k, v in body["properties"].items()},
            date_format=body.get("date_format"),
        )
    return specs
