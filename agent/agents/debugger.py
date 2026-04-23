"""
agents/debugger.py
Debugger Agent — analyses failed SKiDL run output and produces
a targeted corrected script.
"""

import json
import re
from pathlib import Path

from core.models import complete
from core.parser import RunResult


def run_debugger(
    current_code: str,
    run_result: RunResult,
    spec: dict,
    model: str,
    config: dict,
    attempt: int,
    progress_cb=None,
) -> tuple[str, str]:
    """
    Analyse errors in `run_result` and produce a fixed version of `current_code`.

    Returns (fixed_code, diagnosis_text).
    """
    system_prompt = _load_prompt("debugger.md")
    user_message = _build_debug_message(current_code, run_result, spec, attempt)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    if progress_cb:
        progress_cb(f"🔧 Debugger (attempt {attempt}): analysing {len(run_result.errors)} error(s)...")

    raw = complete(
        model=model,
        messages=messages,
        temperature=0.1,   # Lower temperature for more deterministic fixes
        max_tokens=8192,
        api_keys=config.get("api_keys", {}),
        ollama_base_url=config.get("api_keys", {}).get("ollama_base_url", "http://localhost:11434"),
    )

    fixed_code, diagnosis = _parse_debugger_response(raw, current_code)

    if progress_cb:
        progress_cb(f"✅ Debugger: {diagnosis[:120]}...")

    return fixed_code, diagnosis


def _build_debug_message(
    code: str,
    result: RunResult,
    spec: dict,
    attempt: int,
) -> str:
    errors_text = "\n".join(str(i) for i in result.issues)

    stdout_snippet = result.stdout[-3000:] if result.stdout else "(none)"
    stderr_snippet = result.stderr[-2000:] if result.stderr else "(none)"

    return f"""## Attempt {attempt} — Fix required

### Current Script
```python
{code}
```

### Error Output
```
{errors_text}
```

### stdout (last 3000 chars)
```
{stdout_snippet}
```

### stderr (last 2000 chars)
```
{stderr_snippet}
```

### Circuit Intent (for context)
Title: {spec.get('title', 'Unknown')}
Description: {spec.get('description', '')}

Fix ALL errors. Return the complete corrected script in the JSON format specified.
"""


def _parse_debugger_response(raw: str, fallback_code: str) -> tuple[str, str]:
    """
    Parse the debugger's JSON response.
    Returns (fixed_code, diagnosis).
    Falls back to the original code if parsing fails.
    """
    raw = raw.strip()

    # Strip markdown fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    raw = raw.strip()

    try:
        data = json.loads(raw)
        code = data.get("fixed_code", "").strip()
        diagnosis = data.get("diagnosis", "No diagnosis provided.")

        if not code:
            return fallback_code, "Debugger returned empty code — keeping previous version."

        # Clean any accidental fences inside the code string
        code = re.sub(r"^```(?:python)?\s*\n?", "", code)
        code = re.sub(r"\n?```\s*$", "", code)

        return code.strip(), diagnosis

    except json.JSONDecodeError:
        # Try to extract python code directly from the response
        match = re.search(r"```(?:python)?\s*\n(.*?)```", raw, re.DOTALL)
        if match:
            code = match.group(1).strip()
            return code, "Debugger returned code without JSON wrapper — extracted directly."

        return fallback_code, f"Could not parse debugger response:\n{raw[:300]}"


def _load_prompt(filename: str) -> str:
    prompt_path = Path(__file__).parent.parent / "prompts" / filename
    if prompt_path.exists():
        return prompt_path.read_text()
    raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
