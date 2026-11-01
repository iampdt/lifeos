"""Load `config/notion_mapping.yaml`: logical names → data-source IDs and exact property names.

Code refers to logical fields (`tasks.status`), never to Notion's display names, so renaming a
property in Notion is a one-line config change. Data-source IDs come from the environment; the
`sandbox` profile reads `NOTION_SANDBOX_*` instead of `NOTION_*`.
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

    @model_validator(mode="after")
    def _known_type(self) -> PropertySpec:
        if self.type not in PROPERTY_TYPES:
            raise ValueError(f"unsupported property type {self.type!r} for {self.name!r}")
        return self

    def value(self, key: str) -> str:
        return self.values[key]


class DataSourceSpec(BaseModel):
    logical_name: str
    env: str
    database_env: str | None = None
    data_source_id: str | None = None
    database_id: str | None = None
    properties: dict[str, PropertySpec]

    @property
    def configured(self) -> bool:
        return bool(self.data_source_id)

    def prop(self, field: str) -> PropertySpec:
        return self.properties[field]


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
        )
    return specs
