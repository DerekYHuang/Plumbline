"""Minimal client for a local Ollama server (https://ollama.com).

We call Ollama's REST endpoint `POST /api/generate` directly with `requests`
instead of using an SDK or agent framework. Fewer moving parts, and the
blueprint wants the check (and the benchmark) independent of any framework.

Ollama must be running (the desktop app starts it) and the model pulled:

    ollama pull qwen2.5-coder:7b
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

from bench import config


@dataclass
class Generation:
    text: str              # the model's full answer
    model: str             # exact model tag that answered
    prompt_tokens: int     # tokens read (useful to spot truncated prompts)
    output_tokens: int     # tokens written
    seconds: float         # total wall-clock time reported by Ollama
    truncated: bool        # True if the answer hit num_predict and was cut off


class OllamaError(RuntimeError):
    pass


def generate(
    prompt: str,
    model: str = config.DEFAULT_MODEL,
    options: dict | None = None,
    host: str = config.OLLAMA_HOST,
    timeout_s: float = 600.0,
) -> Generation:
    """Send one prompt, wait for the whole answer (no streaming), return it."""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {**config.DEFAULT_OPTIONS, **(options or {})},
    }
    try:
        resp = requests.post(f"{host}/api/generate", json=payload, timeout=timeout_s)
    except requests.ConnectionError as e:
        raise OllamaError(f"Can't reach Ollama at {host}. Is the Ollama app running?") from e
    if resp.status_code != 200:
        raise OllamaError(f"Ollama returned {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    return Generation(
        text=data.get("response", ""),
        model=data.get("model", model),
        prompt_tokens=data.get("prompt_eval_count", 0),
        output_tokens=data.get("eval_count", 0),
        # Ollama reports durations in nanoseconds.
        seconds=data.get("total_duration", 0) / 1e9,
        # done_reason is "length" when generation stopped at num_predict.
        truncated=data.get("done_reason") == "length",
    )


def model_digest(model: str = config.DEFAULT_MODEL, host: str = config.OLLAMA_HOST) -> str:
    """Return the model's content digest so results record exactly which weights ran."""
    resp = requests.get(f"{host}/api/tags", timeout=10)
    resp.raise_for_status()
    for m in resp.json().get("models", []):
        if m.get("name") == model or m.get("model") == model:
            return m.get("digest", "unknown")
    raise OllamaError(f"Model {model!r} not pulled. Run: ollama pull {model}")
