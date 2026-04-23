"""
core/parser.py
Parses all output sources from a SKiDL script run into a unified
list of structured error/warning objects.

Sources:
  1. Python traceback (from stderr)
  2. SKiDL stdout messages (errors/warnings printed to stdout)
  3. .erc file content
  4. .log file content
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum


class Severity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class Issue:
    severity: Severity
    source: str       # "traceback" | "stdout" | "erc" | "log"
    message: str
    line: int | None = None
    context: str = ""

    def __str__(self):
        loc = f" (line {self.line})" if self.line else ""
        return f"[{self.severity}][{self.source}]{loc}: {self.message}"


@dataclass
class RunResult:
    success: bool
    issues: list[Issue] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    has_netlist: bool = False
    has_schematic: bool = False
    has_svg: bool = False
    erc_clean: bool = False

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    def summary(self) -> str:
        lines = []
        lines.append(f"Success: {self.success}")
        lines.append(f"ERC clean: {self.erc_clean}")
        lines.append(f"Netlist: {self.has_netlist} | Schematic: {self.has_schematic} | SVG: {self.has_svg}")
        if self.issues:
            lines.append(f"\nIssues ({len(self.errors)} errors, {len(self.warnings)} warnings):")
            for issue in self.issues:
                lines.append(f"  {issue}")
        return "\n".join(lines)


# ── Regex patterns ─────────────────────────────────────────────────────────────

_TRACEBACK_START = re.compile(r"^Traceback \(most recent call last\):", re.MULTILINE)
_TRACEBACK_ERROR = re.compile(r"^(\w+(?:\.\w+)*Error|Exception): (.+)$", re.MULTILINE)
_FILE_LINE = re.compile(r'File "([^"]+)", line (\d+)')

_SKIDL_ERROR = re.compile(
    r"(ERROR|WARNING|INFO):\s*(.+?)(?:\s*@\s*\[([^\]]+)\])?$",
    re.MULTILINE
)
_ERC_CLEAN = re.compile(r"No errors or warnings found", re.IGNORECASE)
_ERC_ERROR = re.compile(r"ERC (ERROR|WARNING):\s*(.+)", re.MULTILINE)

_NET_GEN_OK = re.compile(r"No errors.*generating netlist", re.IGNORECASE)
_SCH_GEN_OK = re.compile(r"No errors.*generating schematic", re.IGNORECASE)
_SVG_GEN_OK = re.compile(r"No errors.*generating SVG", re.IGNORECASE)


# ── Main parse function ────────────────────────────────────────────────────────

def parse_run(
    stdout: str,
    stderr: str,
    work_dir: Path,
    script_name: str = "circuit",
) -> RunResult:
    """Parse all output from a SKiDL script run into a RunResult."""

    issues: list[Issue] = []
    combined = stdout + "\n" + stderr

    # 1. Python traceback
    if _TRACEBACK_START.search(stderr):
        issues.extend(_parse_traceback(stderr))

    # 2. SKiDL stdout/stderr messages
    issues.extend(_parse_skidl_messages(combined))

    # 3. ERC file
    erc_file = _find_file(work_dir, ".erc")
    erc_clean = False
    if erc_file:
        erc_text = erc_file.read_text()
        erc_clean = bool(_ERC_CLEAN.search(erc_text))
        if not erc_clean:
            issues.extend(_parse_erc_file(erc_text))

    # 4. .log files
    for log_file in work_dir.glob("*.log"):
        log_text = log_file.read_text()
        issues.extend(_parse_log_file(log_text, log_file.name))

    # 5. Check for output artifacts
    has_netlist = bool(_find_file(work_dir, ".net"))
    has_svg = bool(_find_file(work_dir, "_skin.svg") or _find_file(work_dir, ".svg"))
    has_schematic = bool(list(work_dir.glob("*_top*.sch")) or list(work_dir.glob("*.sch")))

    # Overall success: no errors, ERC clean, and all outputs present
    error_count = sum(1 for i in issues if i.severity == Severity.ERROR)
    success = (
        error_count == 0
        and erc_clean
        and has_netlist
        and has_schematic
        and has_svg
    )

    return RunResult(
        success=success,
        issues=issues,
        stdout=stdout,
        stderr=stderr,
        has_netlist=has_netlist,
        has_schematic=has_schematic,
        has_svg=has_svg,
        erc_clean=erc_clean,
    )


# ── Parsers ────────────────────────────────────────────────────────────────────

def _parse_traceback(stderr: str) -> list[Issue]:
    issues = []
    # Find the exception type + message
    for match in _TRACEBACK_ERROR.finditer(stderr):
        exc_type = match.group(1)
        exc_msg = match.group(2)

        # Find the last file/line reference (most relevant location)
        file_lines = _FILE_LINE.findall(stderr)
        last_loc = file_lines[-1] if file_lines else None
        line_no = int(last_loc[1]) if last_loc else None

        issues.append(Issue(
            severity=Severity.ERROR,
            source="traceback",
            message=f"{exc_type}: {exc_msg}",
            line=line_no,
            context=stderr[-2000:],  # Last 2k chars of traceback
        ))

    if not issues and "Error" in stderr:
        # Catch-all for non-standard error output
        issues.append(Issue(
            severity=Severity.ERROR,
            source="traceback",
            message=stderr.strip()[-500:],
        ))

    return issues


def _parse_skidl_messages(text: str) -> list[Issue]:
    issues = []
    for match in _SKIDL_ERROR.finditer(text):
        sev_str = match.group(1)
        msg = match.group(2).strip()
        location = match.group(3) or ""

        # Skip the "clean" info messages
        if "No errors or warnings" in msg:
            continue

        severity = Severity[sev_str] if sev_str in Severity.__members__ else Severity.INFO

        # Extract line number from location string like "/path/file.py:42"
        line_match = re.search(r":(\d+)\]?$", location)
        line_no = int(line_match.group(1)) if line_match else None

        issues.append(Issue(
            severity=severity,
            source="stdout",
            message=msg,
            line=line_no,
            context=location,
        ))
    return issues


def _parse_erc_file(erc_text: str) -> list[Issue]:
    issues = []
    for match in _ERC_ERROR.finditer(erc_text):
        sev_str = match.group(1)
        msg = match.group(2).strip()
        issues.append(Issue(
            severity=Severity[sev_str],
            source="erc",
            message=msg,
        ))
    return issues


def _parse_log_file(log_text: str, filename: str) -> list[Issue]:
    issues = []
    for match in _SKIDL_ERROR.finditer(log_text):
        sev_str = match.group(1)
        msg = match.group(2).strip()
        if "No errors or warnings" in msg:
            continue
        severity = Severity[sev_str] if sev_str in Severity.__members__ else Severity.INFO
        issues.append(Issue(
            severity=severity,
            source=f"log:{filename}",
            message=msg,
        ))
    return issues


def _find_file(directory: Path, suffix: str) -> Path | None:
    matches = list(directory.glob(f"*{suffix}"))
    return matches[0] if matches else None
