"""A workspace's daily scorecard: its habits and metrics, as the habits mapping defines them.

Every checkbox property of the habits data source is a habit and every number property a metric
(multi-user §Generic workspace model), so the check-in, the weekly review and chat describe
whatever this workspace tracks instead of a fixed list.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.notion.mapping import DataSourceSpec

# Plausible ranges for common units; anything else only has to be non-negative and finite.
UNIT_LIMITS = {"hours": 24.0, "minutes": 1440.0}
DEFAULT_LIMIT = 100_000.0


@dataclass(frozen=True)
class Habit:
    key: str
    name: str
    description: str | None = None

    @property
    def hint(self) -> str:
        return self.description or self.name


@dataclass(frozen=True)
class Metric:
    key: str
    name: str
    unit: str | None = None
    description: str | None = None

    @property
    def hint(self) -> str:
        base = self.description or self.name
        return f"{base}, in {self.unit}" if self.unit else base

    @property
    def limit(self) -> float:
        return UNIT_LIMITS.get((self.unit or "").strip().lower(), DEFAULT_LIMIT)

    def label(self, value: float) -> str:
        shown = int(value) if float(value).is_integer() else round(value, 2)
        return f"{self.name} {shown}{f' {self.unit}' if self.unit else ''}"


@dataclass(frozen=True)
class Scorecard:
    habits: tuple[Habit, ...] = ()
    metrics: tuple[Metric, ...] = ()

    @classmethod
    def from_spec(cls, spec: DataSourceSpec | None) -> Scorecard:
        if spec is None or not spec.configured:
            return cls()
        props = spec.properties
        return cls(
            habits=tuple(Habit(k, props[k].name, props[k].description) for k in spec.habit_fields),
            metrics=tuple(
                Metric(k, props[k].name, props[k].unit, props[k].description)
                for k in spec.metric_fields
            ),
        )

    def __bool__(self) -> bool:
        return bool(self.habits or self.metrics)

    def habit_name(self, key: str) -> str:
        return next((h.name for h in self.habits if h.key == key), key.replace("_", " "))

    def metric(self, key: str) -> Metric:
        return next((m for m in self.metrics if m.key == key), Metric(key, key.replace("_", " ")))

    def describe(self) -> dict[str, list[dict[str, str | None]]]:
        """For API responses and the check-in form."""
        return {
            "fields": [{"key": h.key, "name": h.name, "description": h.description}
                       for h in self.habits],
            "metrics": [{"key": m.key, "name": m.name, "unit": m.unit} for m in self.metrics],
        }  # fmt: skip
