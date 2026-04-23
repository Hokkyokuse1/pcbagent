"""
core/models.py
LLM provider abstraction using litellm.
Supports OpenAI, Anthropic, Google, Groq, Ollama, OpenRouter, etc.
"""

import os
import time
import litellm
from typing import Optional
from rich.console import Console

console = Console()

# Suppress litellm's verbose logging
litellm.set_verbose = False

# ── Token tracking ─────────────────────────────────────────────────────────────
_session_tokens = {"prompt": 0, "completion": 0, "calls": 0}


def get_session_usage() -> dict:
    return dict(_session_tokens)


def reset_session_usage():
    _session_tokens.update({"prompt": 0, "completion": 0, "calls": 0})


# ── Main completion function ────────────────────────────────────────────────────
def complete(
    model: str,
    messages: list[dict],
    temperature: float = 0.2,
    max_tokens: int = 8192,
    api_keys: Optional[dict] = None,
    ollama_base_url: str = "http://localhost:11434",
    retry_on_rate_limit: int = 3,
) -> str:
    """
    Call any LLM via litellm. Returns the response text.

    model examples:
        "gpt-4o"
        "claude-3-7-sonnet-20250219"
        "gemini/gemini-2.0-flash"
        "groq/llama-3.3-70b-versatile"
        "ollama/deepseek-coder-v2:16b"
        "openrouter/anthropic/claude-3.7-sonnet"
    """
    # Set API keys from config if provided
    if api_keys:
        for key_name, value in api_keys.items():
            if not value or value.startswith("${"):
                # Try reading from environment
                env_var = value.strip("${}") if value and value.startswith("${") else None
                if env_var:
                    value = os.environ.get(env_var, "")
            if value:
                _set_api_key(key_name, value)

    # Configure Ollama base URL
    if model.startswith("ollama/"):
        litellm.api_base = ollama_base_url

    for attempt in range(retry_on_rate_limit):
        try:
            response = litellm.completion(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            # Track usage
            if hasattr(response, "usage") and response.usage:
                _session_tokens["prompt"] += getattr(response.usage, "prompt_tokens", 0)
                _session_tokens["completion"] += getattr(response.usage, "completion_tokens", 0)
            _session_tokens["calls"] += 1

            return response.choices[0].message.content

        except litellm.RateLimitError:
            if attempt < retry_on_rate_limit - 1:
                wait = 2 ** (attempt + 2)  # 4s, 8s, 16s
                console.print(f"[yellow]Rate limited — retrying in {wait}s...[/yellow]")
                time.sleep(wait)
            else:
                raise
        except litellm.AuthenticationError as e:
            raise RuntimeError(
                f"Authentication failed for model '{model}'. "
                f"Check your API key in config.yaml / environment variables.\n{e}"
            ) from e
        except litellm.BadRequestError as e:
            raise RuntimeError(f"Bad request to LLM: {e}") from e

    raise RuntimeError("Max retries exceeded for LLM call.")


def _set_api_key(provider: str, value: str):
    """Map config key names to litellm/env vars."""
    mapping = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GEMINI_API_KEY",
        "groq": "GROQ_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }
    env_var = mapping.get(provider)
    if env_var:
        os.environ.setdefault(env_var, value)
