import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from app.core.config import Settings
from app.llm.base import LLMBadOutput, LLMError, LLMUnavailable
from app.llm.factory import accounts_router, build_router, own_key_router
from app.llm.gateway import GatewayProvider, RoutedProvider, extract_json
from app.llm.mock import MockLLM
from app.llm.router import LLMRouter


class Answer(BaseModel):
    value: int


def gateway(handler) -> GatewayProvider:
    return GatewayProvider(
        "https://gate.test/", "tg_secret", transport=httpx.MockTransport(handler)
    )


def reply(content: str, status: int = 200) -> httpx.Response:
    body = {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3},
    }
    return httpx.Response(status, json=body)


async def call(provider: GatewayProvider, model: str = "tollgate:smart"):
    return await provider.generate_structured(
        model=model, system="sys", prompt="hello", schema=Answer, temperature=0.2,
        timeout_seconds=5,
    )  # fmt: skip


async def test_sends_route_schema_and_no_cache_then_validates_reply():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return reply('```json\n{"value": 7}\n```')

    completion = await call(gateway(handler))
    assert completion.value.value == 7
    assert (completion.input_tokens, completion.output_tokens) == (12, 3)
    assert seen["url"] == "https://gate.test/v1/chat/completions"
    assert seen["headers"]["authorization"] == "Bearer tg_secret"
    assert seen["headers"]["x-tollgate-cache"] == "off"
    body = seen["body"]
    assert body["model"] == "smart"
    system, user = body["messages"]
    assert system["role"] == "system" and system["content"].startswith("sys\n\n")
    assert '"value"' in system["content"]  # the schema travels in the prompt
    assert user == {"role": "user", "content": "hello"}


@pytest.mark.parametrize("status", [401, 403, 429, 502, 503, 504])
async def test_refusals_and_outages_let_the_chain_move_on(status):
    def handler(request):
        return httpx.Response(status, json={"error": {"code": "x", "message": "m"}})

    with pytest.raises(LLMUnavailable, match=str(status)):
        await call(gateway(handler))


async def test_invalid_request_is_a_plain_error():
    with pytest.raises(LLMError) as caught:
        await call(gateway(lambda r: httpx.Response(400, json={"error": {"code": "bad"}})))
    assert type(caught.value) is LLMError


async def test_network_failure_is_unavailable():
    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(LLMUnavailable, match="ConnectError"):
        await call(gateway(handler))


@pytest.mark.parametrize("content", ["no json here", '{"value": "seven"}', ""])
async def test_replies_that_dont_match_are_bad_output(content):
    with pytest.raises(LLMBadOutput):
        await call(gateway(lambda r: reply(content)))


def test_extract_json_tolerates_fences_and_prose():
    assert extract_json('Sure! {"a": 1} Hope that helps.') == '{"a": 1}'
    assert extract_json('```\n{"a": {"b": 2}}\n```') == '{"a": {"b": 2}}'


async def test_router_falls_back_from_gemini_to_the_gateway_and_names_it():
    default = MockLLM()
    default.queue(Answer, LLMUnavailable("503"))
    routed = RoutedProvider(default, gateway(lambda r: reply('{"value": 5}')))
    router = LLMRouter(
        routed, {"fast": ["gemini-x", "tollgate:smart"]},
        clock=lambda: datetime(2026, 11, 17, tzinfo=UTC),
    )  # fmt: skip
    result = await router.generate(Answer, system="s", prompt="p")
    assert result.value.value == 5
    assert (result.usage.provider, result.usage.model) == ("tollgate", "tollgate:smart")
    assert result.usage.fallbacks == 1
    assert router.tokens_spent_today() == 15


def configured(**extra) -> Settings:
    return Settings(
        _env_file=None, gemini_api_key=SecretStr("g"), tollgate_url="https://gate.test",
        tollgate_api_key=SecretStr("t"), **extra,
    )  # fmt: skip


def test_gateway_entries_are_skipped_until_configured():
    plain = Settings(_env_file=None, llm_fast_models="a,tollgate:smart,b")
    assert plain.model_chain("fast") == ["a", "b"]
    on = configured(llm_fast_models="a,tollgate:smart,b")
    assert on.model_chain("fast") == ["a", "tollgate:smart", "b"]
    assert on.model_chain("fast", gateway=False) == ["a", "b"]


def test_owner_router_uses_the_gateway_and_accounts_do_not():
    settings = configured()
    service = build_router(settings)
    assert isinstance(service.provider, RoutedProvider)
    assert "tollgate:smart" in service.chains["fast"]

    accounts = accounts_router(settings, service)
    assert accounts.provider is service.provider.default
    assert all(not m.startswith("tollgate:") for chain in accounts.chains.values() for m in chain)
    assert accounts._spent is service._spent  # one daily budget for both

    own = own_key_router(settings, "account-key")
    assert all(not m.startswith("tollgate:") for chain in own.chains.values() for m in chain)


def test_without_gemini_the_gateway_is_not_used():
    settings = Settings(
        _env_file=None, tollgate_url="https://gate.test", tollgate_api_key=SecretStr("t")
    )
    router = build_router(settings)
    assert router.provider is None
    assert accounts_router(settings, router) is router
