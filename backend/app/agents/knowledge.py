"""Knowledge specialist: answer a question from the account's indexed pages, with citations
(architecture §5.9).

The model sees only the retrieved passages, each with a reference (S1, S2, …), and must cite
the ones it uses. Code keeps only references it was shown, numbers them in the order they first
appear, and returns each cited passage with its page and link. Without a model, the passages
themselves are the answer.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.common import ask_model, unavailable_warning
from app.knowledge.index import Hit
from app.llm.router import LLMRouter
from app.services.context import DATA_RULES, clean, data_block

_CITE = re.compile(r"\[(S\d{1,2})\]")
EXCERPT_CHARS = 280


class KnowledgeAnswer(BaseModel):
    found: bool = Field(description="True if the sources answer the question")
    answer: str = Field(
        description="Plain text, at most about 150 words; cite sources like [S2] after the "
        "sentences they support"
    )


SYSTEM = f"""You are the knowledge specialist of LifeOS, a personal Chief of Staff.
You answer the owner's question from SOURCES: passages of Notion pages they chose to index.
- Use only the sources. If they don't answer the question, set found to false and say in one
  sentence what they do cover.
- After each sentence that uses a source, cite it like [S1]; several like [S1][S3].
- Be brief and concrete, in plain text without markdown.
{DATA_RULES.replace("(like T3)", "(like S2)")}"""


@dataclass
class KnowledgeResult:
    answer: str | None
    found: bool | None
    citations: list[dict[str, Any]]
    source: Literal["ai", "search"]
    warnings: list[str] = field(default_factory=list)
    usage: list[dict[str, Any]] = field(default_factory=list)


def citation(n: int, hit: Hit) -> dict[str, Any]:
    excerpt = clean(hit.text, EXCERPT_CHARS)
    return {"n": n, "page_id": hit.page.page_id, "title": hit.page.title, "url": hit.page.url,
            "heading": hit.heading, "excerpt": excerpt}  # fmt: skip


def passages_only(hits: Sequence[Hit], warnings: list[str]) -> KnowledgeResult:
    return KnowledgeResult(None, None, [citation(i, h) for i, h in enumerate(hits, 1)], "search",
                           warnings)  # fmt: skip


async def answer_question(llm: LLMRouter, question: str, hits: Sequence[Hit]) -> KnowledgeResult:
    if not hits:
        return KnowledgeResult(
            "Nothing in your indexed pages matches that question.", False, [], "search"
        )
    refs = {f"S{i}": hit for i, hit in enumerate(hits, 1)}
    sources = "\n\n".join(
        f'{ref} | "{clean(hit.page.title, 120)}"'
        + (f" › {clean(hit.heading, 160)}" if hit.heading else "")
        + f"\n{clean(hit.text, 1500)}"
        for ref, hit in refs.items()
    )
    prompt = f"QUESTION: {clean(question, 500)}\n\n{data_block('SOURCES:\n' + sources)}"
    call = await ask_model(llm, KnowledgeAnswer, agent="knowledge", system=SYSTEM, prompt=prompt)
    usage = [call.usage] if call.usage else []
    if call.value is None:
        result = passages_only(hits, [unavailable_warning(call.error).replace(
            "used the rule-based fallback", "showing the closest passages")])  # fmt: skip
        result.usage = usage
        return result

    numbers: dict[str, int] = {}

    def renumber(match: re.Match[str]) -> str:
        ref = match[1]
        if ref not in refs:
            return ""  # a source it wasn't shown
        numbers.setdefault(ref, len(numbers) + 1)
        return f"[{numbers[ref]}]"

    text = _CITE.sub(renumber, clean(call.value.answer, 1500)).strip()
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    citations = [citation(n, refs[ref]) for ref, n in numbers.items()]
    warnings = []
    if call.value.found and not citations:
        warnings.append("The answer cites no source; check it against your pages.")
    return KnowledgeResult(text, call.value.found, citations, "ai", warnings, usage)
