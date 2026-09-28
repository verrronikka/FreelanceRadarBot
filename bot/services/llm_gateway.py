"""OpenRouter gateway (раздел 4.2 документации).

В LLM передаются только текст профиля, нормализованная карточка заказа и
версия промпта. Ответ строго валидируется Pydantic-схемой. Любая ошибка,
таймаут или отсутствие ключа -> LLMUnavailable, и matching деградирует
к детерминированным правилам.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

import httpx
from pydantic import BaseModel, Field, ValidationError

from core.config import settings

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1"

SYSTEM_PROMPT = (
    "Ты — фильтр заказов для фрилансера. Тебе дают описание того, какие заказы ищет "
    "фрилансер, и карточку заказа. Оцени, подходит ли заказ. Ответь ТОЛЬКО JSON-объектом "
    'вида {"match": true|false, "score": число от 0 до 1, "reasons": ["короткая причина", ...]} '
    "— не больше 3 причин, каждая до 60 символов, на русском. Никакого текста вне JSON."
)


class LLMUnavailable(Exception):
    pass


class LLMVerdict(BaseModel):
    match: bool
    score: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list, max_length=5)
    model: Optional[str] = None


_JSON_RE = re.compile(r"\{.*\}", re.S)


class LLMGateway:
    def __init__(self) -> None:
        proxy = settings.llm_proxy_url
        if proxy and not proxy.startswith(("http://", "https://")):
            logger.warning("LLM_PROXY_URL %s не http(s) — OpenRouter будет вызываться без прокси", proxy.split("://")[0])
            proxy = None
        self._client = httpx.AsyncClient(timeout=settings.llm_timeout_seconds, proxy=proxy)

    @property
    def enabled(self) -> bool:
        return settings.llm_configured

    async def close(self) -> None:
        await self._client.aclose()

    async def evaluate(self, profile_text: str, job: dict) -> LLMVerdict:
        if not self.enabled:
            raise LLMUnavailable("OPENROUTER_API_KEY не задан")
        payload_user = json.dumps(
            {"profile": profile_text, "job": job, "prompt_version": PROMPT_VERSION}, ensure_ascii=False
        )
        body = {
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": payload_user},
            ],
            "temperature": 0,
            "max_tokens": 300,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "X-Title": "FreelanceRadar",
        }
        try:
            resp = await self._client.post(f"{settings.openrouter_base_url}/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"{type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise LLMUnavailable(f"HTTP {resp.status_code}")
        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"] or ""
            m = _JSON_RE.search(content)
            if not m:
                raise ValueError("no JSON in answer")
            verdict = LLMVerdict.model_validate_json(m.group(0))
        except (ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
            raise LLMUnavailable(f"invalid response: {type(exc).__name__}") from exc
        verdict.model = data.get("model", settings.llm_model)
        verdict.reasons = [r.strip()[:80] for r in verdict.reasons if r and r.strip()][:3]
        return verdict
