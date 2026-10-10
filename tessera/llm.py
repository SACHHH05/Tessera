"""Token Factory client wrapper.

Every agent calls the model through here so we get, for free:
  * the right Nemotron size per role,
  * token / latency / cost-ish accounting per role (the dashboard and the demo use these),
  * a hard per-project token budget so agents can never loop forever.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from openai import APIStatusError, OpenAI

from .config import Settings


class BudgetExceeded(RuntimeError):
    pass


class LLMRequestError(RuntimeError):
    """The server rejected the request itself (HTTP 4xx other than 429). Retrying won't help."""


@dataclass
class LLMResult:
    role: str
    model: str
    text: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def json(self) -> Any:
        return parse_json(self.text)


@dataclass
class UsageTracker:
    """Per-project accounting, broken down by role."""

    budget: int
    by_role: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return int(sum(r["tokens"] for r in self.by_role.values()))

    def check(self) -> None:
        if self.total_tokens >= self.budget:
            raise BudgetExceeded(f"Token budget of {self.budget} reached ({self.total_tokens} used).")

    def add(self, res: LLMResult) -> None:
        r = self.by_role.setdefault(res.role, {"calls": 0, "tokens": 0, "latency_s": 0.0})
        r["calls"] += 1
        r["tokens"] += res.total_tokens
        r["latency_s"] += res.latency_s


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_json(text: str) -> Any:
    """Parse model output as JSON, tolerating markdown fences and leading prose."""
    cleaned = _FENCE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = min((i for i in (cleaned.find("{"), cleaned.find("[")) if i != -1), default=-1)
        end = max(cleaned.rfind("}"), cleaned.rfind("]"))
        if start == -1 or end <= start:
            raise
        return json.loads(cleaned[start : end + 1])


class LLMClient:
    def __init__(
        self,
        settings: Settings,
        tracker: UsageTracker | None = None,
        client: OpenAI | None = None,
        on_call: Callable[[LLMResult], None] | None = None,
    ) -> None:
        self.settings = settings
        self.tracker = tracker or UsageTracker(budget=settings.max_tokens_per_project)
        self.on_call = on_call
        self._client = client or OpenAI(base_url=settings.base_url, api_key=settings.nebius_api_key)

    def chat(
        self,
        role: str,
        messages: list[dict[str, Any]],
        *,
        json_schema: dict[str, Any] | None = None,
        schema_name: str = "output",
        temperature: float = 0.3,
        max_tokens: int | None = None,
        retries: int = 2,
    ) -> LLMResult:
        self.tracker.check()
        model = self.settings.model_for(role)
        kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if json_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": json_schema, "strict": True},
            }

        last_err: Exception | None = None
        for attempt in range(retries + 1):
            t0 = time.perf_counter()
            try:
                resp = self._client.chat.completions.create(**kwargs)
            except APIStatusError as e:
                if 400 <= e.status_code < 500 and e.status_code != 429:
                    raise LLMRequestError(f"{e.status_code}: {e}") from e
                last_err = e
                time.sleep(min(2**attempt, 8))
                continue
            except Exception as e:  # network errors
                last_err = e
                time.sleep(min(2**attempt, 8))
                continue
            latency = time.perf_counter() - t0
            usage = getattr(resp, "usage", None)
            res = LLMResult(
                role=role,
                model=model,
                text=resp.choices[0].message.content or "",
                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                latency_s=latency,
            )
            self.tracker.add(res)
            if self.on_call:
                self.on_call(res)
            return res
        raise RuntimeError(f"Token Factory call failed after {retries + 1} attempts: {last_err}")