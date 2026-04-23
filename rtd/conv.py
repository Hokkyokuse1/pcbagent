from skidl import SchLib, KICAD5  # Force KiCad 5 compatibility.

# Load the original KiCad 6/7 library.
sym_lib = SchLib("./kicad-libraries/symbols/Espressif.kicad_sym", tool=KICAD5)  # Note: tool=KICAD5!

# Export to KiCad 5 legacy format.
sym_lib.export("Espressif_legacy.lib", tool=KICAD5)  # Explicitly use KiCad 5.

# Print all parts to verify ESP32-S3-WROOM-2 exists.
for part in sym_lib.parts:
    print(part.name)
