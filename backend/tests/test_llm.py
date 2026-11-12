import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from app.core.config import Settings
from app.llm.base import (
    Completion,
    LLMBadOutput,
    LLMBudgetExceeded,
    LLMNotConfigured,
    LLMUnavailable,
)
from app.llm.mock import MockLLM
from app.llm.router import LLMRouter


class Answer(BaseModel):
    value: int


def make_router(mock, budget=10_000):
    return LLMRouter(
        mock, {"fast": ["a", "b"], "smart": ["c"]}, daily_token_budget=budget,
        clock=lambda: datetime(2026, 11, 1, tzinfo=UTC),
    )  # fmt: skip


async def test_falls_back_to_next_model_and_records_usage():
    mock = MockLLM()
    mock.queue(Answer, LLMUnavailable("503"), {"value": 4})
    result = await make_router(mock).generate(Answer, system="s", prompt="p" * 40)
    assert result.value.value == 4
    assert result.usage.model == "b" and result.usage.fallbacks == 1
    assert [c["model"] for c in mock.calls] == ["a", "b"]


async def test_bad_output_also_falls_back():
    mock = MockLLM()
    mock.queue(Answer, LLMBadOutput("junk"), {"value": 1})
    assert (await make_router(mock).generate(Answer, system="s", prompt="p")).value.value == 1


async def test_all_models_failing_raises_unavailable():
    mock = MockLLM()
    mock.queue(Answer, LLMUnavailable("x"), LLMUnavailable("y"))
    with pytest.raises(LLMUnavailable, match="all models failed"):
        await make_router(mock).generate(Answer, system="s", prompt="p")


async def test_non_fallback_errors_stop_the_chain():
    mock = MockLLM()
    mock.queue(Answer, LLMBudgetExceeded("stop"), {"value": 1})
    with pytest.raises(LLMBudgetExceeded):
        await make_router(mock).generate(Answer, system="s", prompt="p")
    assert len(mock.calls) == 1


async def test_daily_budget_and_missing_provider():
    mock = MockLLM()
    mock.queue(Answer, {"value": 1}, {"value": 2})
    router = make_router(mock, budget=30)
    await router.generate(Answer, system="s", prompt="p" * 100)  # 25 + 20 tokens
    assert router.tokens_spent_today() == 45
    with pytest.raises(LLMBudgetExceeded):
        await router.generate(Answer, system="s", prompt="p")
    with pytest.raises(LLMNotConfigured):
        await LLMRouter(None, {"fast": ["a"]}).generate(Answer, system="s", prompt="p")


async def test_tier_selects_chain():
    mock = MockLLM()
    mock.queue(Answer, {"value": 1})
    result = await make_router(mock).generate(Answer, system="s", prompt="p", tier="smart")
    assert result.usage.model == "c"


class TimedProvider:
    """Answers with the model's position in the chain after a delay per model, or fails."""

    name = "timed"

    def __init__(self, delays: dict[str, float], failing: tuple[str, ...] = ()):
        self.delays = delays
        self.failing = failing
        self.started: list[str] = []
        self.cancelled: list[str] = []

    async def generate_structured(self, *, model, system, prompt, schema, **_):
        self.started.append(model)
        try:
            await asyncio.sleep(self.delays[model])
        except asyncio.CancelledError:
            self.cancelled.append(model)
            raise
        if model in self.failing:
            raise LLMUnavailable(f"{model}: 503")
        return Completion(schema(value="abc".index(model)), input_tokens=10, output_tokens=5)


def hedged(provider, hedge=0.05):
    return LLMRouter(provider, {"fast": ["a", "b", "c"]}, hedge_seconds={"fast": hedge})


async def test_a_slow_model_gets_the_next_one_alongside_and_the_first_answer_wins():
    provider = TimedProvider({"a": 1.0, "b": 0.01, "c": 1.0})
    result = await hedged(provider).generate(Answer, system="s", prompt="p")
    assert result.value.value == 1 and result.usage.model == "b"
    assert result.usage.fallbacks == 1  # "a" was outpaced
    assert provider.started == ["a", "b"]
    await asyncio.sleep(0)
    assert provider.cancelled == ["a"]  # the slower call is dropped


async def test_a_prompt_model_answers_alone():
    provider = TimedProvider({"a": 0.01, "b": 0.01, "c": 0.01})
    result = await hedged(provider).generate(Answer, system="s", prompt="p")
    assert result.usage.model == "a" and provider.started == ["a"]


async def test_the_first_model_can_still_win_after_the_hedge():
    provider = TimedProvider({"a": 0.08, "b": 1.0, "c": 1.0})
    result = await hedged(provider).generate(Answer, system="s", prompt="p")
    assert result.usage.model == "a" and result.usage.fallbacks == 0
    assert provider.started == ["a", "b"]


async def test_a_failure_moves_on_at_once_and_all_failing_raises():
    provider = TimedProvider({"a": 0.0, "b": 0.0, "c": 0.0}, failing=("a", "b"))
    result = await hedged(provider, hedge=10).generate(Answer, system="s", prompt="p")
    assert result.usage.model == "c" and result.usage.fallbacks == 2
    provider = TimedProvider({"a": 0.0, "b": 0.08, "c": 0.0}, failing=("a", "b", "c"))
    with pytest.raises(LLMUnavailable, match="all models failed"):
        await hedged(provider).generate(Answer, system="s", prompt="p")
    assert provider.started == ["a", "b", "c"]


def test_settings_model_chains():
    settings = Settings(_env_file=None, llm_fast_models=" a, b ,", llm_smart_models="c")
    assert settings.model_chain("fast") == ["a", "b"]
    assert settings.model_chain("smart") == ["c"]
    url = Settings(_env_file=None, database_url="postgresql+asyncpg://u:p@h:1/d").checkpoint_url
    assert url == "postgresql://u:p@h:1/d"
