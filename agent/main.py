#!/usr/bin/env python3
"""
main.py — SKiDL Circuit Agent CLI
====================================
Usage:
  python main.py "RTD temperature readout circuit with ESP32 and SPI ADC"
  python main.py --web                          # Launch web UI
  python main.py --refine ./output/prev_run/    # Refine an existing circuit
  python main.py --config /path/to/config.yaml <description>
"""

import sys
import os
import json
import shutil
import argparse
from datetime import datetime
from pathlib import Path

# Make sure local packages are importable
sys.path.insert(0, str(Path(__file__).parent))

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt, Confirm

from agents import run_planner, run_codegen, run_debugger
from core import run_skidl_script, collect_outputs, get_session_usage

console = Console()

# ── Config loader ──────────────────────────────────────────────────────────────

def load_config(config_path: str | None = None) -> dict:
    """Load config.yaml, resolving env vars in values."""
    if config_path is None:
        config_path = Path(__file__).parent / "config.yaml"
    else:
        config_path = Path(config_path)

    if not config_path.exists():
        console.print(f"[red]Config not found: {config_path}[/red]")
        sys.exit(1)

    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Resolve ${ENV_VAR} references in api_keys
    api_keys = config.get("api_keys", {})
    for k, v in api_keys.items():
        if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
            env_var = v[2:-1]
            api_keys[k] = os.environ.get(env_var, "")

    # Resolve paths relative to the agent directory
    agent_dir = Path(__file__).parent
    paths = config.get("paths", {})
    for k, v in paths.items():
        if isinstance(v, str) and v.startswith("./") or (isinstance(v, str) and v.startswith("../")):
            paths[k] = str((agent_dir / v).resolve())

    return config


# ── Pipeline orchestrator ──────────────────────────────────────────────────────

def run_pipeline(
    description: str,
    config: dict,
    output_dir: Path,
    refine_from: Path | None = None,
    progress_cb=None,
) -> dict:
    """
    Full agent pipeline: Plan → CodeGen → Validate loop → Collect outputs.

    Returns a dict with keys: success, spec, code, outputs, issues, usage, run_log
    """
    models = config.get("models", {})
    runner_cfg = config.get("runner", {})
    max_retries = runner_cfg.get("max_retries", 8)
    escalate_at = runner_cfg.get("escalate_at", 5)

    run_log = []

    def log(msg: str):
        run_log.append(msg)
        if progress_cb:
            progress_cb(msg)

    # ── Step 1: Plan ─────────────────────────────────────────────────────────
    log("Step 1/3 — Planning circuit architecture...")
    spec = run_planner(
        description,
        model=models.get("planner", "gpt-4o"),
        config=config,
        progress_cb=log,
    )

    # Save spec
    output_dir.mkdir(parents=True, exist_ok=True)
    spec_path = output_dir / "circuit_spec.json"
    spec_path.write_text(json.dumps(spec, indent=2))
    log(f"Circuit spec saved: {spec_path}")

    # ── Step 2+3: Code Gen + Validate Loop ───────────────────────────────────
    log(f"Step 2/3 — Generating SKiDL code (max {max_retries} attempts)...")

    code = None
    previous_errors: list[str] = []
    final_result = None
    work_dir = None
    all_outputs = {}

    script_name = _slugify(spec.get("title", "circuit"))

    for attempt in range(1, max_retries + 1):
        log(f"\n── Attempt {attempt}/{max_retries} ──")

        # Escalate to user after escalate_at failed attempts
        if attempt == escalate_at and sys.stdin.isatty():
            log(f"\n⚠️  {escalate_at} attempts failed. Asking user for clarification...")
            clarification = Prompt.ask(
                "[yellow]The agent is struggling. Provide additional hints (or press Enter to continue)[/yellow]"
            )
            if clarification.strip():
                description = description + "\n\nAdditional context: " + clarification
                # Re-plan with updated description
                spec = run_planner(description, model=models.get("planner"), config=config, progress_cb=log)

        # Generate code
        if attempt == 1:
            code = run_codegen(
                spec=spec,
                model=models.get("codegen", "gpt-4o"),
                config=config,
                previous_errors=None,
                progress_cb=log,
            )
        else:
            # Debugger produces the fixed code
            if final_result:
                code, diagnosis = run_debugger(
                    current_code=code,
                    run_result=final_result,
                    spec=spec,
                    model=models.get("debugger", "gpt-4o"),
                    config=config,
                    attempt=attempt,
                    progress_cb=log,
                )
                log(f"Debugger diagnosis: {diagnosis}")

        # Save generated code
        code_path = output_dir / f"{script_name}.py"
        code_path.write_text(code)

        # Run the code
        log(f"Running {script_name}.py...")
        final_result, work_dir = run_skidl_script(
            code=code,
            script_name=script_name,
            config=config,
            progress_cb=log,
        )

        if final_result.success:
            log(f"✅ SUCCESS on attempt {attempt}!")
            break

        # Log the errors for next iteration
        error_summary = "\n".join(str(i) for i in final_result.errors[:20])
        previous_errors.append(f"=== Attempt {attempt} errors ===\n{error_summary}")
        log(f"❌ {len(final_result.errors)} error(s) — will retry...")

    # ── Step 3: Collect outputs ───────────────────────────────────────────────
    if work_dir and work_dir.exists():
        all_outputs = collect_outputs(work_dir, output_dir, script_name)
        # Clean up temp directory
        shutil.rmtree(work_dir, ignore_errors=True)

    # Save run log
    log_path = output_dir / "run.log"
    log_path.write_text("\n".join(run_log))

    usage = get_session_usage()

    return {
        "success": final_result.success if final_result else False,
        "spec": spec,
        "code": code,
        "outputs": all_outputs,
        "issues": final_result.issues if final_result else [],
        "usage": usage,
        "run_log": run_log,
        "output_dir": output_dir,
    }


# ── CLI display ────────────────────────────────────────────────────────────────

def display_results(result: dict):
    console.print()

    if result["success"]:
        console.print(Panel(
            "[bold green]✅ Circuit generated successfully![/bold green]",
            border_style="green"
        ))
    else:
        console.print(Panel(
            "[bold red]❌ Circuit generation failed after all retries.[/bold red]",
            border_style="red"
        ))

    # Outputs table
    table = Table(title="Generated Files", show_header=True, header_style="bold blue")
    table.add_column("Type", style="cyan")
    table.add_column("Path")

    for ftype, fpath in result.get("outputs", {}).items():
        table.add_row(ftype.upper(), str(fpath))

    console.print(table)

    # Issues summary
    issues = result.get("issues", [])
    if issues:
        console.print(f"\n[yellow]⚠️  {len(issues)} issue(s) in final output:[/yellow]")
        for issue in issues[:10]:
            colour = "red" if issue.severity == "ERROR" else "yellow"
            console.print(f"  [{colour}]{issue}[/{colour}]")

    # Token usage
    usage = result.get("usage", {})
    if usage.get("calls"):
        console.print(
            f"\n[dim]Token usage: {usage['prompt']} prompt + "
            f"{usage['completion']} completion = "
            f"{usage['prompt'] + usage['completion']} total "
            f"({usage['calls']} LLM calls)[/dim]"
        )

    output_dir = result.get("output_dir")
    if output_dir:
        console.print(f"\n[bold]Output directory:[/bold] {output_dir}")

    # Show generated code snippet
    code = result.get("code", "")
    if code:
        console.print("\n[bold]Generated SKiDL script (preview):[/bold]")
        console.print(Syntax(code[:3000] + ("\n..." if len(code) > 3000 else ""),
                             "python", theme="monokai", line_numbers=True))


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="SKiDL Circuit Agent — Generate KiCad schematics from natural language",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("description", nargs="?", help="Natural language circuit description")
    parser.add_argument("--web", action="store_true", help="Launch the web UI")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument(
        "--refine", metavar="DIR", default=None,
        help="Path to a previous output directory to refine"
    )
    parser.add_argument(
        "--output", metavar="DIR", default=None,
        help="Output directory (default: auto-timestamped under ./output/)"
    )

    args = parser.parse_args()

    # ── Web UI mode ────────────────────────────────────────────────────────
    if args.web:
        import uvicorn
        import os
        sys.path.insert(0, str(Path(__file__).parent))
        port = int(os.environ.get("PORT", 8765))
        uvicorn.run(
            "web.app:app",
            host="0.0.0.0",
            port=port,
            reload=False,
        )
        return

    # ── CLI mode ───────────────────────────────────────────────────────────
    if not args.description:
        # Interactive prompt if no description given
        console.print(Panel(
            "[bold]SKiDL Circuit Agent[/bold]\n"
            "Describe your circuit in natural language and the agent will\n"
            "generate a KiCad 5 schematic using SKiDL Python.",
            title="Welcome",
            border_style="blue",
        ))
        description = Prompt.ask("[bold cyan]Circuit description[/bold cyan]")
    else:
        description = args.description

    config = load_config(args.config)

    # Determine output dir
    if args.output:
        output_dir = Path(args.output)
    else:
        agent_dir = Path(__file__).parent
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        slug = _slugify(description[:40])
        output_dir = agent_dir / "output" / f"{timestamp}_{slug}"

    console.print(f"\n[bold]Output:[/bold] {output_dir}")
    console.print(f"[bold]Models:[/bold] planner={config['models']['planner']} | "
                  f"codegen={config['models']['codegen']}\n")

    # Run pipeline with rich progress indicator
    result = {}
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Running agent pipeline...", total=None)

        def update_progress(msg: str):
            progress.update(task, description=msg)

        result = run_pipeline(
            description=description,
            config=config,
            output_dir=output_dir,
            refine_from=Path(args.refine) if args.refine else None,
            progress_cb=update_progress,
        )

    display_results(result)
    return 0 if result.get("success") else 1


def _slugify(text: str) -> str:
    import re
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text)
    return text[:40]


if __name__ == "__main__":
    sys.exit(main())
