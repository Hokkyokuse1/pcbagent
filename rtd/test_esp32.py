from skidl import *

set_default_tool(KICAD5)
lib_search_paths[KICAD5].append(".")  # Current directory

# Load the part from the converted library.
esp = Part("Espressif_legacy", "ESP32-S3-WROOM-2")
print(esp)
generate_netlist()
