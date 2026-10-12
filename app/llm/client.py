"""Groq client wrapper: retries, rate-limit handling, usage tracking.

Structured outputs (json_schema) are used for the intent step. Per Groq's docs, streaming and
tool use are not supported together with structured outputs, so we don't combine them.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional


class LLMUnavailable(RuntimeError):
    """Raised when Groq cannot be used right now (rate limit, outage). Message is user-safe."""


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    latency_s: float = 0.0
    calls: int = 1

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.total_tokens + other.total_tokens,
            self.cached_tokens + other.cached_tokens,
            self.latency_s + other.latency_s,
            self.calls + other.calls,
        )


@dataclass
class JsonResult:
    data: dict
    usage: Usage
    strict_used: bool


def _usage_from(resp: Any, latency: float) -> Usage:
    u = getattr(resp, "usage", None)
    details = getattr(u, "prompt_tokens_details", None)
    return Usage(
        prompt_tokens=getattr(u, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(u, "completion_tokens", 0) or 0,
        total_tokens=getattr(u, "total_tokens", 0) or 0,
        cached_tokens=getattr(details, "cached_tokens", 0) or 0,
        latency_s=latency,
    )


def _retry_after(exc: Exception) -> Optional[float]:
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers:
        raw = headers.get("retry-after")
        try:
            return float(raw) if raw is not None else None
        except ValueError:
            return None
    return None


class GroqClient:
    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        *,
        reasoning_effort: str = "low",
        max_attempts: int = 4,
        max_wait_s: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        client: Any = None,
    ) -> None:
        if not model:
            raise ValueError("model is required (set GROQ_MODEL)")
        if client is None:
            from groq import Groq  # imported lazily so tests run without the package

            client = Groq(api_key=api_key)
        self._client, self.model = client, model
        self.reasoning_effort, self.max_attempts = reasoning_effort, max_attempts
        self.max_wait_s, self._sleep = max_wait_s, sleep

    def complete_json(
        self,
        messages: list[dict],
        name: str,
        schema: dict,
        *,
        strict: bool = True,
        max_tokens: int = 1200,
    ) -> JsonResult:
        attempt = 0
        while True:
            attempt += 1
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": name, "schema": schema, "strict": strict},
            }
            started = time.perf_counter()
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0,
                    max_tokens=max_tokens,
                    response_format=response_format,
                    extra_body={"reasoning_effort": self.reasoning_effort},
                )
            except Exception as exc:  # noqa: BLE001 - classified by status code below
                status = getattr(exc, "status_code", None)
                if status == 400 and strict:
                    strict = False
                    continue
                if attempt >= self.max_attempts:
                    raise LLMUnavailable(
                        "The AI service is busy or unavailable. Please try again shortly."
                    ) from exc
                if status == 429:
                    wait = _retry_after(exc)
                    if wait is not None and wait > self.max_wait_s:
                        raise LLMUnavailable(
                            f"The AI service rate limit was reached; it resets in about {int(wait)} seconds."
                        ) from exc
                    self._sleep(
                        wait if wait is not None else min(2**attempt, self.max_wait_s)
                    )
                    continue
                if status in (500, 502, 503, 504) or type(exc).__name__ == "APIConnectionError":
                    self._sleep(min(2**attempt, self.max_wait_s))
                    continue
                raise
            usage = _usage_from(resp, time.perf_counter() - started)
            content = (resp.choices[0].message.content or "").strip()
            if not content:
                raise LLMUnavailable(
                    "The AI service returned an empty response. Please try again."
                )
            try:
                return JsonResult(json.loads(content), usage, strict)
            except json.JSONDecodeError as exc:
                raise LLMUnavailable(
                    "The AI service returned malformed output. Please try again."
                ) from exc

    def complete_text(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 500,
    ) -> tuple[str, Usage]:
        """Plain-text completion for narration.

        Uses the same retry, rate-limit, outage and usage handling as the
        structured JSON path.
        """
        attempt = 0

        while True:
            attempt += 1
            started = time.perf_counter()

            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0,
                    max_tokens=max_tokens,
                    extra_body={"reasoning_effort": self.reasoning_effort},
                )
            except Exception as exc:  # noqa: BLE001
                status = getattr(exc, "status_code", None)

                if attempt >= self.max_attempts:
                    raise LLMUnavailable(
                        "The AI service is busy or unavailable. Please try again shortly."
                    ) from exc

                if status == 429:
                    wait = _retry_after(exc)

                    if wait is not None and wait > self.max_wait_s:
                        raise LLMUnavailable(
                            "The AI service rate limit was reached; "
                            f"it resets in about {int(wait)} seconds."
                        ) from exc

                    self._sleep(
                        wait if wait is not None else min(2**attempt, self.max_wait_s)
                    )
                    continue

                if status in (500, 502, 503, 504) or (
                    type(exc).__name__ == "APIConnectionError"
                ):
                    self._sleep(min(2**attempt, self.max_wait_s))
                    continue

                raise

            usage = _usage_from(
                resp,
                time.perf_counter() - started,
            )

            content = (resp.choices[0].message.content or "").strip()

            if not content:
                raise LLMUnavailable(
                    "The AI service returned an empty response. Please try again."
                )

            return content, usage
