# Use a robust Python base with system-level hardware dependencies
FROM python:3.11-slim-bookworm

# Install KiCad and libraries for schematic generation
# We need the symbols and footprints for SKiDL to work correctly
RUN apt-get update && apt-get install -y --no-install-recommends \
    kicad \
    kicad-libraries \
    kicad-symbols \
    kicad-footprints \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Install Python dependencies
COPY agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source code
COPY agent/ ./agent/
COPY rtd/ ./rtd/

# Configure KiCad environment variables for SKiDL
ENV KICAD_SYMBOL_DIR="/usr/share/kicad/symbols"
ENV KICAD_FOOTPRINT_DIR="/usr/share/kicad/footprints"
# Ensure local modules are found
ENV PYTHONPATH="/app/agent:${PYTHONPATH}"

# Expose the API port
EXPOSE 8765

# Set a non-root user for security (optional, but good for tech interviews)
# RUN useradd -m skidl && chown -R skidl:skidl /app
# USER skidl

# Start the application in web mode by default
CMD ["python", "agent/main.py", "--web"]
