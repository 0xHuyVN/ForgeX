from __future__ import annotations

import os
import time
from typing import Any

import requests

from ..config import GEMINI_API_KEY, OPENAI_API_KEY
from ..database import db_cursor


PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "model": "gemini-2.0-flash",
        "api_key_env": "GEMINI_API_KEY",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "google/gemma-4-31b",
        "api_key_env": "OPENROUTER_API_KEY",
    },
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "nvidia/nemotron-3-super",
        "api_key_env": "NVIDIA_API_KEY",
    },
    "ollama": {
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:7b",
        "api_key_env": "",
    },
    "custom": {
        "base_url": "http://127.0.0.1:8000/v1",
        "model": "",
        "api_key_env": "",
    },
}


MODEL_PRESETS: dict[str, list[str]] = {
    "openrouter": [
        "google/gemma-4-31b",
        "google/gemma-4-26b-a4b",
        "qwen/qwen3-next-80b-a3b",
        "qwen/qwen3-coder-480b-a35b",
        "meta-llama/llama-3.3-70b-instruct",
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "nvidia/nemotron-3-super",
        "nvidia/nemotron-3-nano-30b-a3b",
    ],
    "nvidia": [
        "nvidia/nemotron-3-super",
        "nvidia/nemotron-3-nano-30b-a3b",
    ],
    "openai": ["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"],
    "gemini": ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"],
    "ollama": ["qwen2.5:7b", "llama3.1:8b", "gemma2:9b"],
    "custom": [],
}


def _settings() -> dict[str, str]:
    with db_cursor() as cur:
        rows = cur.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


def _env_key(name: str) -> str:
    if name == "OPENAI_API_KEY":
        return OPENAI_API_KEY or os.environ.get(name, "")
    if name == "GEMINI_API_KEY":
        return GEMINI_API_KEY or os.environ.get(name, "")
    return os.environ.get(name, "")


def _clean_base_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    if value.endswith("/chat/completions"):
        value = value[: -len("/chat/completions")]
    return value


def get_ai_provider_config(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = _settings()
    overrides = dict(overrides or {})
    provider = (overrides.get("provider") or settings.get("ai_provider") or "openrouter").strip().lower()
    defaults = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["custom"])
    api_key = (
        overrides.get("api_key")
        or settings.get("ai_api_key")
        or _env_key(defaults.get("api_key_env", ""))
        or ""
    )
    base_url = _clean_base_url(overrides.get("base_url") or settings.get("ai_base_url") or defaults.get("base_url", ""))
    model = (overrides.get("model") or settings.get("ai_model") or defaults.get("model") or "").strip()
    fallback = overrides.get("fallback") or settings.get("ai_fallback") or ""
    try:
        temperature = float(overrides.get("temperature", settings.get("ai_temperature", 0.2)) or 0.2)
    except (TypeError, ValueError):
        temperature = 0.2
    try:
        max_tokens = int(overrides.get("max_tokens", settings.get("ai_max_tokens", 4096)) or 4096)
    except (TypeError, ValueError):
        max_tokens = 4096
    return {
        "provider": provider,
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "fallback": fallback,
    }


def provider_presets() -> dict[str, Any]:
    return {
        "providers": PROVIDER_DEFAULTS,
        "models": MODEL_PRESETS,
    }


def _chat_openai_compatible(config: dict[str, Any], messages: list[dict[str, str]]) -> dict[str, Any]:
    base_url = _clean_base_url(config.get("base_url", ""))
    if not base_url:
        raise RuntimeError("Missing AI provider base_url")
    if not config.get("model"):
        raise RuntimeError("Missing AI provider model")
    headers = {"Content-Type": "application/json"}
    if config.get("api_key"):
        headers["Authorization"] = f"Bearer {config['api_key']}"
    if config.get("provider") == "openrouter":
        headers.setdefault("HTTP-Referer", "http://localhost")
        headers.setdefault("X-Title", "RichReviewTool")
    resp = requests.post(
        f"{base_url}/chat/completions",
        headers=headers,
        json={
            "model": config["model"],
            "messages": messages,
            "temperature": config.get("temperature", 0.2),
            "max_tokens": config.get("max_tokens", 4096),
        },
        timeout=60,
    )
    try:
        data = resp.json()
    except Exception:
        data = {"error": resp.text}
    if resp.status_code >= 400:
        message = data.get("error", {}).get("message") if isinstance(data.get("error"), dict) else data.get("error")
        raise RuntimeError(message or f"HTTP {resp.status_code}")
    choices = data.get("choices") or []
    text = ""
    if choices:
        text = ((choices[0].get("message") or {}).get("content") or choices[0].get("text") or "").strip()
    return {
        "text": text,
        "raw": data,
        "usage": data.get("usage") or {},
        "headers": dict(resp.headers),
    }


def _chat_gemini(config: dict[str, Any], messages: list[dict[str, str]]) -> dict[str, Any]:
    api_key = config.get("api_key") or GEMINI_API_KEY
    if not api_key:
        raise RuntimeError("Missing Gemini API key")
    prompt = "\n\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages)
    base_url = _clean_base_url(config.get("base_url") or PROVIDER_DEFAULTS["gemini"]["base_url"])
    model = config.get("model") or "gemini-2.0-flash"
    resp = requests.post(
        f"{base_url}/models/{model}:generateContent?key={api_key}",
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": config.get("temperature", 0.2),
                "maxOutputTokens": config.get("max_tokens", 4096),
            },
        },
        timeout=60,
    )
    data = resp.json()
    if resp.status_code >= 400:
        raise RuntimeError((data.get("error") or {}).get("message") or f"HTTP {resp.status_code}")
    parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    text = "".join(part.get("text", "") for part in parts).strip()
    return {"text": text, "raw": data, "usage": data.get("usageMetadata") or {}, "headers": dict(resp.headers)}


def chat_completion(messages: list[dict[str, str]], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    config = get_ai_provider_config(overrides)
    if config["provider"] == "gemini":
        return _chat_gemini(config, messages)
    return _chat_openai_compatible(config, messages)


def test_ai_provider(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    config = get_ai_provider_config(overrides)
    started = time.perf_counter()
    try:
        result = chat_completion(
            [{"role": "user", "content": "Reply with exactly: OK"}],
            config,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        headers = result.get("headers") or {}
        quota = {
            key: headers.get(key)
            for key in (
                "x-ratelimit-remaining",
                "x-ratelimit-limit",
                "x-ratelimit-reset",
                "x-openrouter-usage",
            )
            if headers.get(key) is not None
        }
        return {
            "ok": True,
            "provider": config["provider"],
            "model": config["model"],
            "base_url": config["base_url"],
            "latency_ms": latency_ms,
            "response": result.get("text", ""),
            "usage": result.get("usage", {}),
            "quota": quota,
        }
    except Exception as exc:
        return {
            "ok": False,
            "provider": config["provider"],
            "model": config["model"],
            "base_url": config["base_url"],
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "error": str(exc),
        }
