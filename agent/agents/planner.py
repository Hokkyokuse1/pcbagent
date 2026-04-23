"""
agents/planner.py
Planner Agent — converts natural-language circuit description
into a structured circuit_spec JSON.
"""

import json
from pathlib import Path

from core.models import complete


def run_planner(
    user_description: str,
    model: str,
    config: dict,
    progress_cb=None,
) -> dict:
    """
    Call the planner LLM to convert a natural-language circuit description
    into a structured JSON circuit specification dict.

    Returns the parsed spec dict.
    Raises ValueError if the LLM returns invalid JSON.
    """
    system_prompt = _load_prompt("planner.md")

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                "Produce the circuit specification JSON for the following description.\n\n"
                f"Description:\n{user_description}"
            ),
        },
    ]

    if progress_cb:
        progress_cb("🧠 Planner: analysing circuit description...")

    raw = complete(
        model=model,
        messages=messages,
        temperature=0.2,
        max_tokens=4096,
        api_keys=config.get("api_keys", {}),
        ollama_base_url=config.get("api_keys", {}).get("ollama_base_url", "http://localhost:11434"),
    )

    # Strip any accidental markdown fences
    raw = _strip_fences(raw)

    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as e:
        # Try to extract JSON from the response
        import re
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            spec = json.loads(match.group())
        else:
            raise ValueError(
                f"Planner returned invalid JSON:\n{raw[:500]}\n\nError: {e}"
            ) from e

    if progress_cb:
        progress_cb(
            f"✅ Planner done: '{spec.get('title', 'Untitled')}' "
            f"— {len(spec.get('subcircuits', []))} subcircuits planned."
        )

    return spec


def _load_prompt(filename: str) -> str:
    prompt_path = Path(__file__).parent.parent / "prompts" / filename
    if prompt_path.exists():
        return prompt_path.read_text()
    raise FileNotFoundError(f"Prompt file not found: {prompt_path}")


def _strip_fences(text: str) -> str:
    """Remove markdown code fences if the LLM wrapped its JSON in them."""
    import re
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()
