"""
core/runner.py
Executes a generated SKiDL Python script in an isolated temp directory,
collects all outputs, and returns a RunResult via the parser.
"""

import os
import sys
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from .parser import parse_run, RunResult


def run_skidl_script(
    code: str,
    script_name: str = "circuit",
    config: dict = None,
    progress_cb: Callable[[str], None] = None,
) -> tuple[RunResult, Path]:
    """
    Write `code` to a temp directory, run it with Python, parse all outputs.

    Returns (RunResult, work_dir_path).
    The caller is responsible for copying outputs from work_dir if needed.
    """
    cfg = config or {}
    runner_cfg = cfg.get("runner", {})
    paths_cfg = cfg.get("paths", {})

    timeout = runner_cfg.get("timeout_seconds", 90)
    work_base = Path(runner_cfg.get("work_dir", "/tmp/skidl_agent_runs"))
    kicad_footprints = paths_cfg.get("kicad_footprints", "/usr/share/kicad/footprints")
    kicad_symbols = paths_cfg.get("kicad_symbols", "/usr/share/kicad/symbols")
    native_parts = paths_cfg.get("native_parts_lib", "")

    # ── Create a fresh temp working directory ───────────────────────────────
    work_base.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(dir=work_base, prefix=f"{script_name}_"))

    script_path = work_dir / f"{script_name}.py"
    script_path.write_text(code)

    # Copy native parts lib if available
    if native_parts:
        native_path = Path(native_parts)
        if native_path.exists():
            shutil.copy2(native_path, work_dir / native_path.name)

    # Symlink / copy fp-lib-table and sym-lib-table from rtd reference dir
    _setup_lib_tables(work_dir, paths_cfg)

    if progress_cb:
        progress_cb(f"Running {script_name}.py in {work_dir}...")

    # ── Run the script ──────────────────────────────────────────────────────
    use_podman = runner_cfg.get("use_podman", os.environ.get("USE_PODMAN", "false").lower() == "true")
    podman_image = runner_cfg.get("podman_image", os.environ.get("PODMAN_IMAGE", "skidl-agent:latest"))

    if use_podman:
        if progress_cb:
            progress_cb(f"Spawning Podman container for instance isolation (image: {podman_image})...")
        
        # We mount the work directory and run the script inside
        # Using :Z for SELinux relabeling if needed on Linux hosts
        cmd = [
            "podman", "run", "--rm",
            "-v", f"{work_dir.absolute()}:/work:Z",
            "-w", "/work",
            "-e", f"KICAD_SYMBOL_DIR={kicad_symbols}",
            "-e", f"KICAD_FOOTPRINT_DIR={kicad_footprints}",
            podman_image,
            "python", f"{script_name}.py"
        ]
    else:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(work_dir) + os.pathsep + env.get("PYTHONPATH", "")
        # Set ALL KiCad symbol dir env vars — SKiDL 2.x checks these on import
        for var in ["KICAD_SYMBOL_DIR", "KICAD5_SYMBOL_DIR", "KICAD6_SYMBOL_DIR",
                    "KICAD7_SYMBOL_DIR", "KICAD8_SYMBOL_DIR"]:
            env[var] = kicad_symbols
        # Footprint dir
        env["KICAD_FOOTPRINT_DIR"] = kicad_footprints
        env.setdefault("KICAD5_FOOTPRINT_DIR", kicad_footprints)
        cmd = [sys.executable, str(script_path)]

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(work_dir) if not use_podman else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env if not use_podman else None,
        )
        stdout = proc.stdout
        stderr = proc.stderr
    except subprocess.TimeoutExpired:
        stdout = ""
        stderr = f"ERROR: Script execution timed out after {timeout}s."
    except Exception as e:
        stdout = ""
        stderr = f"ERROR: Failed to launch execution: {str(e)}"

    result = parse_run(stdout, stderr, work_dir, script_name)

    if progress_cb:
        progress_cb(result.summary())

    return result, work_dir


def collect_outputs(work_dir: Path, dest_dir: Path, script_name: str = "circuit") -> dict[str, Path]:
    """
    Copy generated output files from the work dir to a permanent destination.
    Returns a dict of {type: path}.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    outputs = {}

    patterns = {
        "netlist": "*.net",
        "schematic": "*_top*.sch",
        "svg": "*_skin.svg",
        "erc": "*.erc",
        "json": "*.json",
        "script": f"{script_name}.py",
    }

    for output_type, pattern in patterns.items():
        matches = list(work_dir.glob(pattern))
        if matches:
            src = matches[0]
            dst = dest_dir / src.name
            shutil.copy2(src, dst)
            outputs[output_type] = dst

    # Also copy any sub-sheet .sch files
    for sch in work_dir.glob("*.sch"):
        dst = dest_dir / sch.name
        if not dst.exists():
            shutil.copy2(sch, dst)

    return outputs


def _setup_lib_tables(work_dir: Path, paths_cfg: dict):
    """Copy or create minimal fp-lib-table and sym-lib-table in the work dir."""
    ref_impl = paths_cfg.get("reference_impl", "")
    if ref_impl:
        ref_dir = Path(ref_impl).parent
        for table_file in ["fp-lib-table", "sym-lib-table"]:
            src = ref_dir / table_file
            if src.exists():
                shutil.copy2(src, work_dir / table_file)
                return

    # Create minimal tables if reference not found
    (work_dir / "fp-lib-table").write_text("(fp_lib_table)\n")
    (work_dir / "sym-lib-table").write_text("(sym_lib_table)\n")
