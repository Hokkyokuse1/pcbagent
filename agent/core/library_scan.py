"""
core/library_scan.py
Scans installed KiCad5 symbol libraries and returns a structured
inventory that the code-gen LLM can use to pick real, available parts.
"""

import os
import re
from pathlib import Path
from functools import lru_cache


# Key library names the agent should know about (KiCad5 .lib format)
# Maps human-readable category → library filename stem
PRIORITY_LIBS = {
    "Passive components": ["Device"],
    "Op-amps": ["Amplifier_Operational"],
    "Instrumentation amps": ["Amplifier_Instrumentation"],
    "Comparators": ["Comparator"],
    "BJT transistors": ["Transistor_BJT"],
    "MOSFETs": ["Transistor_FET"],
    "Connectors": ["Connector_Generic", "Connector_PinHeader_2.54mm"],
    "Power symbols": ["power"],
    "Logic": ["74xx", "74xGxx"],
    "Microcontrollers": ["MCU_Microchip_ATmega", "MCU_ST_STM32F0"],
    "Voltage regulators": ["Regulator_Linear", "Regulator_Switching"],
    "ADC/DAC": ["Analog_ADC", "Analog_DAC"],
    "Displays": ["Display"],
    "Sensors": ["Sensor_Temperature", "Sensor_Pressure"],
    "RF/Wireless": ["RF_Module", "RF_Bluetooth"],
}


# Flat list of all priority library stems to scan (avoids reading 200+ large files)
_PRIORITY_LIB_STEMS = [
    "Device", "power",
    "Amplifier_Operational", "Amplifier_Instrumentation", "Amplifier_Current",
    "Comparator",
    "Transistor_BJT", "Transistor_FET",
    "Connector_Generic",
    "Regulator_Linear", "Regulator_Switching",
    "Analog_ADC", "Analog_DAC", "Analog",
    "Sensor_Temperature", "Sensor_Pressure",
    "74xx", "74xGxx",
    "MCU_Microchip_ATmega", "MCU_ST_STM32F0", "MCU_Espressif",
    "Logic_Gate", "Interface_I2C", "Interface_SPI",
    "RF_Module", "RF_Bluetooth",
    "Display", "LED", "Diode",
]

# Max bytes read per library file (to avoid hanging on huge files)
_MAX_LIB_BYTES = 256 * 1024  # 256 KB


@lru_cache(maxsize=1)
def scan_kicad_libraries(symbols_path: str = "/usr/share/kicad/symbols") -> dict:
    """
    Scan KiCad symbol directory and return an inventory dict:
    {
        "Device": ["R", "C", "L", "LED", ...],
        "Amplifier_Operational": ["LM324", "TL071", ...],
        ...
    }
    Only reads priority libraries to stay fast.
    Supports both .lib (KiCad5) and .kicad_sym (KiCad6+) formats.
    """
    inventory = {}
    symbols_dir = Path(symbols_path)

    if not symbols_dir.exists():
        return {}

    for stem in _PRIORITY_LIB_STEMS:
        # Try .lib first, then .kicad_sym
        for ext in [".lib", ".kicad_sym"]:
            lib_file = symbols_dir / (stem + ext)
            if lib_file.exists():
                parts = _extract_part_names(lib_file)
                if parts:
                    inventory[stem] = parts
                break  # Found this lib, move to next stem

    return inventory


def _extract_part_names(lib_file: Path) -> list[str]:
    """Extract component names from a KiCad library file."""
    parts = []
    try:
        # Only read the first _MAX_LIB_BYTES to avoid stalling on huge files
        with open(lib_file, "r", errors="replace") as f:
            text = f.read(_MAX_LIB_BYTES)

        if lib_file.suffix == ".lib":
            # KiCad5 .lib format: lines starting with "DEF PartName"
            for match in re.finditer(r"^DEF\s+(\S+)", text, re.MULTILINE):
                name = match.group(1)
                if not name.startswith("#"):
                    parts.append(name)

        elif lib_file.suffix == ".kicad_sym":
            # KiCad6+ format: (symbol "PartName" ...)
            for match in re.finditer(r'\(symbol\s+"([^"]+)"', text):
                name = match.group(1)
                # Skip sub-unit symbols (they contain underscores with numbers)
                if not re.search(r'_\d+_\d+$', name):
                    parts.append(name)

    except Exception:
        pass

    return parts


def get_library_summary(symbols_path: str = "/usr/share/kicad/symbols") -> str:
    """
    Return a human-readable summary string of available libraries
    suitable for injection into the code-gen LLM prompt.
    """
    inventory = scan_kicad_libraries(symbols_path)

    if not inventory:
        return "KiCad symbol libraries not found. Use SKiDL-native parts or SKIDL tool."

    lines = ["## Available KiCad5 Symbol Libraries\n"]
    lines.append("Use `Part(\"LibraryName\", \"PartName\", ...)` to instantiate these.\n")

    for lib_name, parts in sorted(inventory.items()):
        # Show first 15 parts to keep the prompt lean
        preview = parts[:15]
        ellipsis = f" ... (+{len(parts)-15} more)" if len(parts) > 15 else ""
        lines.append(f"**{lib_name}**: {', '.join(preview)}{ellipsis}")

    lines.append("\n### Priority Libraries (prefer these)")
    for category, libs in PRIORITY_LIBS.items():
        available = [l for l in libs if l in inventory]
        if available:
            lines.append(f"- {category}: `{', '.join(available)}`")

    return "\n".join(lines)


def get_native_parts_summary(native_lib_path: str) -> str:
    """
    Read a _sklib.py file and return the list of part names defined in it,
    for injection into the code-gen prompt as available custom parts.
    """
    lib_path = Path(native_lib_path)
    if not lib_path.exists():
        return ""

    text = lib_path.read_text()
    names = re.findall(r"'name':'([^']+)'", text)
    if not names:
        return ""

    lines = ["\n## Custom Native Parts (from project _sklib.py)"]
    lines.append("These parts are available via the project's native SKiDL library.")
    lines.append("Import with: `from main_lib_sklib import main_lib`")
    lines.append(f"Parts: {', '.join(names)}")
    return "\n".join(lines)
