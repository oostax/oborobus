"""
Ouroboros — LLM client.

The only module that communicates with LLM APIs (Cloud.ru Foundation Models + optional local).
Contract: chat(), default_model(), available_models(), add_usage().
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import copy
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger(__name__)

DEFAULT_LIGHT_MODEL = "ai-sage/GigaChat3-10B-A1.8B"


class LocalContextTooLargeError(RuntimeError):
    """Raised when a local model cannot fit context without silent truncation."""


def _estimate_message_chars(messages: List[Dict[str, Any]]) -> int:
    total = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            total += sum(len(str(block.get("text", ""))) for block in content if isinstance(block, dict))
        else:
            total += len(str(content or ""))
    return total


def normalize_reasoning_effort(value: str, default: str = "medium") -> str:
    allowed = {"none", "minimal", "low", "medium", "high", "xhigh"}
    v = str(value or "").strip().lower()
    return v if v in allowed else default


def reasoning_rank(value: str) -> int:
    order = {"none": 0, "minimal": 1, "low": 2, "medium": 3, "high": 4, "xhigh": 5}
    return int(order.get(str(value or "").strip().lower(), 3))


def add_usage(total: Dict[str, Any], usage: Dict[str, Any]) -> None:
    """Accumulate usage from one LLM call into a running total."""
    for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "cache_write_tokens"):
        total[k] = int(total.get(k) or 0) + int(usage.get(k) or 0)
    if usage.get("cost"):
        total["cost"] = float(total.get("cost") or 0) + float(usage["cost"])


def fetch_openrouter_pricing() -> Dict[str, Tuple[float, float, float]]:
    """Stub: Cloud.ru does not expose a public pricing API."""
    return {}


class LLMClient:
    """LLM API wrapper. Routes calls to Cloud.ru Foundation Models or a local llama-cpp-python server."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://foundation-models.api.cloud.ru/v1",
    ):
        self._api_key = api_key or os.environ.get("API_KEY", "") or os.environ.get("OPENROUTER_API_KEY", "")
        self._base_url = base_url
        self._client = None
        self._local_client = None
        self._local_port: Optional[int] = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            import httpx

            log.info("Initializing Cloud.ru LLM client (base_url=%s)", self._base_url)
            self._client = OpenAI(
                base_url=self._base_url,
                api_key=self._api_key,
                max_retries=0,
                default_headers={"Authorization": f"Bearer {self._api_key}"},
                http_client=httpx.Client(verify=False),
            )
        return self._client

    def _get_local_client(self):
        port = int(os.environ.get("LOCAL_MODEL_PORT", "8766"))
        if self._local_client is None or self._local_port != port:
            from openai import OpenAI
            self._local_client = OpenAI(
                base_url=f"http://127.0.0.1:{port}/v1",
                api_key="local",
                max_retries=0,
            )
            self._local_port = port
        return self._local_client

    @staticmethod
    def _strip_cache_control(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Strip cache_control from message content blocks (not supported by Cloud.ru)."""
        cleaned = copy.deepcopy(messages)
        for msg in cleaned:
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        block.pop("cache_control", None)
        return cleaned

    @staticmethod
    def _normalize_content(content: Any) -> str:
        """Convert list content to plain string for Cloud.ru compatibility."""
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    text = block.get("text") or block.get("output_text") or ""
                    if text:
                        parts.append(str(text))
            return "\n\n".join(parts)
        return str(content)

    @staticmethod
    def _fix_schema_required(schema: Dict[str, Any]) -> None:
        """Recursively fix `required` fields in JSON Schema for Cloud.ru."""
        if not isinstance(schema, dict):
            return
        req = schema.get("required")
        if req is not None:
            if isinstance(req, list) and all(isinstance(r, str) for r in req):
                if not req:
                    del schema["required"]
            else:
                del schema["required"]
        for prop in (schema.get("properties") or {}).values():
            if isinstance(prop, dict):
                LLMClient._fix_schema_required(prop)
        for kw in ("items", "additionalProperties"):
            sub = schema.get(kw)
            if isinstance(sub, dict):
                LLMClient._fix_schema_required(sub)

    @staticmethod
    def _sanitize_tools(tools: List[Any]) -> List[Dict[str, Any]]:
        """Sanitize tools schema for Cloud.ru compatibility."""
        fixed: List[Dict[str, Any]] = []
        for tool in tools:
            t = tool if isinstance(tool, dict) else (tool.model_dump() if hasattr(tool, 'model_dump') else dict(tool))
            t.pop("cache_control", None)
            func = t.get("function")
            if isinstance(func, dict):
                params = func.get("parameters") or {}
                if not isinstance(params, dict):
                    params = {}
                params.setdefault("type", "object")
                params.setdefault("properties", {})
                if not isinstance(params.get("properties"), dict):
                    params["properties"] = {}
                LLMClient._fix_schema_required(params)
                func["parameters"] = params
            fixed.append(t)
        return json.loads(json.dumps(fixed))

    def _sanitize_payload(self, messages: List[Dict[str, Any]], kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize payload for Cloud.ru: normalize content, fix tools, cap max_tokens."""
        clean_messages = copy.deepcopy(messages)
        for msg in clean_messages:
            msg["content"] = self._normalize_content(msg.get("content"))

        enable_tools = os.environ.get("CLOUDRU_ENABLE_TOOLS", "1").strip() not in ("0", "false", "no")
        if "tools" in kwargs and enable_tools:
            kwargs["tools"] = self._sanitize_tools(kwargs["tools"])
        else:
            kwargs.pop("tools", None)
            kwargs.pop("tool_choice", None)

        for key in ("parallel_tool_calls", "response_format", "stream", "stream_options"):
            kwargs.pop(key, None)

        mt = kwargs.get("max_tokens")
        if mt is None or (isinstance(mt, (int, float)) and int(mt) > 4096):
            kwargs["max_tokens"] = 4096

        kwargs["messages"] = clean_messages
        return kwargs

    def _fetch_generation_cost(self, generation_id: str) -> Optional[float]:
        return None

    def chat(
        self,
        messages: List[Dict[str, Any]],
        model: str = "",
        tools: Optional[List[Dict[str, Any]]] = None,
        reasoning_effort: str = "medium",
        max_tokens: int = 16384,
        tool_choice: str = "auto",
        use_local: bool = False,
        use_gigachat: bool = False,
        temperature: Optional[float] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Single LLM call. Returns: (response_message_dict, usage_dict)."""
        if use_local:
            return self._chat_local(messages, tools, max_tokens, tool_choice)
        return self._chat_cloud(messages, model or self.default_model(), tools, max_tokens, tool_choice)

    async def chat_async(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        reasoning_effort: str = "medium",
        max_tokens: int = 16384,
        tool_choice: str = "auto",
        temperature: Optional[float] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Async chat — delegates to sync for Cloud.ru (no async client needed)."""
        return self.chat(messages, model, tools, reasoning_effort, max_tokens, tool_choice)

    def _chat_cloud(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        tools: Optional[List[Dict[str, Any]]],
        max_tokens: int,
        tool_choice: str,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Send a chat request to Cloud.ru Foundation Models."""
        client = self._get_client()
        clean_messages = self._strip_cache_control(messages)

        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": clean_messages,
            "max_tokens": max_tokens,
        }
        if tools:
            clean_tools = [{k: v for k, v in t.items() if k != "cache_control"} for t in tools]
            kwargs["tools"] = clean_tools
            # Force tool use — GigaChat tends to respond with text instead of calling tools
            kwargs["tool_choice"] = "required" if tool_choice == "auto" else tool_choice

        kwargs = self._sanitize_payload(clean_messages, kwargs)

        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as e:
            body = getattr(e, 'body', None) or getattr(e, 'response', None)
            log.error("Cloud.ru API error: %s | body=%s | model=%s", e, body, model)
            raise

        resp_dict = resp.model_dump()
        usage = resp_dict.get("usage") or {}
        usage["cost"] = 0.0
        choices = resp_dict.get("choices") or [{}]
        msg = (choices[0] if choices else {}).get("message") or {}

        # GLM-4.7 puts answer in reasoning_content instead of content
        content = msg.get("content")
        reasoning = msg.get("reasoning_content")
        if (not content or not str(content).strip()) and reasoning and str(reasoning).strip():
            log.info("Cloud.ru: content empty, using reasoning_content (%d chars)", len(str(reasoning)))
            msg["content"] = str(reasoning)

        return msg, usage

    def _chat_local(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        max_tokens: int,
        tool_choice: str,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Send a chat request to the local llama-cpp-python server."""
        client = self._get_local_client()
        clean_messages = self._strip_cache_control(messages)
        for msg in clean_messages:
            content = msg.get("content")
            if isinstance(content, list):
                msg["content"] = "\n\n".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )

        local_max = min(max_tokens, 2048)
        try:
            from ouroboros.local_model import get_manager
            ctx_len = get_manager().get_context_length()
            if ctx_len > 0:
                local_max = min(max_tokens, max(256, ctx_len // 4))
        except Exception:
            pass

        clean_tools = None
        if tools:
            clean_tools = [{k: v for k, v in t.items() if k != "cache_control"} for t in tools]

        kwargs: Dict[str, Any] = {
            "model": "local-model",
            "messages": clean_messages,
            "max_tokens": local_max,
        }
        if clean_tools:
            kwargs["tools"] = clean_tools
            kwargs["tool_choice"] = tool_choice

        resp = client.chat.completions.create(**kwargs)
        resp_dict = resp.model_dump()
        usage = resp_dict.get("usage") or {}
        usage["cost"] = 0.0
        choices = resp_dict.get("choices") or [{}]
        msg = (choices[0] if choices else {}).get("message") or {}
        return msg, usage

    def vision_query(
        self,
        prompt: str,
        images: List[Dict[str, Any]],
        model: str = "ai-sage/GigaChat3-10B-A1.8B",
        max_tokens: int = 2048,
        reasoning_effort: str = "none",
    ) -> Tuple[str, Dict[str, Any]]:
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img in images:
            if "url" in img:
                content.append({"type": "image_url", "image_url": {"url": img["url"]}})
            elif "base64" in img:
                mime = img.get("mime", "image/png")
                content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img['base64']}"}})
        messages = [{"role": "user", "content": content}]
        response_msg, usage = self.chat(messages=messages, model=model, tools=None, max_tokens=max_tokens)
        return response_msg.get("content") or "", usage

    def default_model(self) -> str:
        return os.environ.get("OUROBOROS_MODEL", "ai-sage/GigaChat3-10B-A1.8B")

    def available_models(self) -> List[str]:
        main = os.environ.get("OUROBOROS_MODEL", "ai-sage/GigaChat3-10B-A1.8B")
        code = os.environ.get("OUROBOROS_MODEL_CODE", "")
        light = os.environ.get("OUROBOROS_MODEL_LIGHT", "")
        models = [main]
        if code and code != main:
            models.append(code)
        if light and light != main and light != code:
            models.append(light)
        return models
