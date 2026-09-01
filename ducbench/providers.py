from __future__ import annotations

import json
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Any


def _extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        obj = json.loads(text[start:end+1])
        if isinstance(obj, dict):
            return obj
    raise ValueError("Model output did not contain a JSON object")


@dataclass
class ProviderConfig:
    provider: str
    model: str
    max_output_tokens: int = 6000
    temperature: float = 0.0
    retries: int = 4


class LLMProvider:
    def __init__(self, cfg: ProviderConfig):
        self.cfg = cfg

    def generate_json(self, system: str, user: str) -> dict[str, Any]:
        raise NotImplementedError

    def _with_retries(self, fn):
        last = None
        for i in range(self.cfg.retries):
            try:
                return fn()
            except Exception as e:
                last = e
                if i + 1 < self.cfg.retries:
                    time.sleep((2 ** i) + random.random())
        raise last


class OpenAIProvider(LLMProvider):
    def __init__(self, cfg: ProviderConfig):
        super().__init__(cfg)
        from openai import OpenAI
        self.client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

    def generate_json(self, system: str, user: str) -> dict[str, Any]:
        def call():
            response = self.client.responses.create(
                model=self.cfg.model,
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_output_tokens=self.cfg.max_output_tokens,
            )
            return _extract_json(response.output_text)
        return self._with_retries(call)


class AnthropicProvider(LLMProvider):
    def __init__(self, cfg: ProviderConfig):
        super().__init__(cfg)
        from anthropic import Anthropic
        self.client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    def generate_json(self, system: str, user: str) -> dict[str, Any]:
        def call():
            message = self.client.messages.create(
                model=self.cfg.model,
                max_tokens=self.cfg.max_output_tokens,
                temperature=self.cfg.temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            text = "".join(getattr(block, "text", "") for block in message.content)
            return _extract_json(text)
        return self._with_retries(call)


def make_provider(provider: str, model: str, max_output_tokens: int = 6000) -> LLMProvider:
    cfg = ProviderConfig(provider=provider, model=model, max_output_tokens=max_output_tokens)
    if provider == "openai":
        return OpenAIProvider(cfg)
    if provider == "anthropic":
        return AnthropicProvider(cfg)
    raise ValueError("provider must be 'openai' or 'anthropic'")
