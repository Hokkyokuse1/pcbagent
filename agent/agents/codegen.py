"""
agents/codegen.py
Code Generation Agent — converts a circuit spec JSON into a
runnable SKiDL Python script, following the main.py structure.
"""

import json
import re
from pathlib import Path

from core.models import complete
from core.library_scan import get_library_summary, get_native_parts_summary


def run_codegen(
    spec: dict,
    model: str,
    config: dict,
    previous_errors: list[str] | None = None,
    progress_cb=None,
) -> str:
    """
    Generate SKiDL Python code from a circuit spec dict.

    `previous_errors`: list of error strings from previous run attempts.
    On the first call this is None/empty. On retries, errors are appended
    to give the model context on what failed.

    Returns a Python source string ready to run.
    """
    system_prompt = _build_system_prompt(config)
    user_message = _build_user_message(spec, previous_errors)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    if progress_cb:
        attempt = len(previous_errors) + 1 if previous_errors else 1
        progress_cb(f"⚙️  Code Gen (attempt {attempt}): writing SKiDL Python...")

    raw = complete(
        model=model,
        messages=messages,
        temperature=0.15,
        max_tokens=8192,
        api_keys=config.get("api_keys", {}),
        ollama_base_url=config.get("api_keys", {}).get("ollama_base_url", "http://localhost:11434"),
    )

    code = _extract_code(raw)

    if progress_cb:
        lines = code.count("\n")
        progress_cb(f"✅ Code Gen: generated {lines} lines of SKiDL Python.")

    return code


def _build_system_prompt(config: dict) -> str:
    """Load codegen.md and inject live library inventory."""
    prompt_path = Path(__file__).parent.parent / "prompts" / "codegen.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Missing prompt: {prompt_path}")

    prompt = prompt_path.read_text()

    paths = config.get("paths", {})
    symbols_path = paths.get("kicad_symbols", "/usr/share/kicad/symbols")
    native_lib = paths.get("native_parts_lib", "")

    lib_summary = get_library_summary(symbols_path)
    native_summary = get_native_parts_summary(native_lib) if native_lib else ""

    prompt = prompt.replace("{{ LIBRARY_INVENTORY }}", lib_summary)
    prompt = prompt.replace("{{ NATIVE_PARTS }}", native_summary)

    return prompt


def _build_user_message(spec: dict, previous_errors: list[str] | None) -> str:
    spec_json = json.dumps(spec, indent=2)
    message = f"Generate the SKiDL Python script for this circuit specification:\n\n```json\n{spec_json}\n```"

    if previous_errors:
        errors_text = "\n".join(previous_errors)
        message += (
            f"\n\n## ⚠️ Previous attempts failed with these errors — fix them all:\n\n"
            f"```\n{errors_text}\n```\n\n"
            "Produce a corrected complete script that addresses every error above."
        )

    return message


def _extract_code(raw: str) -> str:
    """
    Extract Python code from LLM response.
    Handles responses with or without markdown fences.
    """
    raw = raw.strip()

    # Try to extract from ```python ... ``` block
    match = re.search(r"```(?:python)?\s*\n(.*?)```", raw, re.DOTALL)
    if match:
        return match.group(1).strip()

    # If it looks like raw Python (starts with import/from/# or sys.)
    if re.match(r"^(import|from|#|sys\.)", raw):
        return raw

    # Last resort: return as-is (might still work)
    return raw
