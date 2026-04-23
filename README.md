# SKiDL Circuit Agent API

An AI-powered agentic system that generates KiCad schematics from natural language descriptions using SKiDL (Python-based KiCad integration). This version implements a robust FastAPI backend with ephemeral container-based isolation for secure circuit script execution.

## 🚀 Key Features

- **Natural Language to PCB**: Generate professional KiCad schematics just by describing them.
- **Agentic Workflow**: Uses an LLM pipeline (Planner -> CodeGen -> Debugger) to iteratively refine circuit code.
- **Instance Isolation**: Support for **Podman/Docker** containers to execute generated scripts in a sandboxed environment.
- **RESTful API**: Structured endpoints for programmatic access and user session isolation.
- **Interactive UI**: Real-time progress streaming and SVG schematic preview.

## 🛠 Tech Stack

- **Backend**: FastAPI, Python 3.11
- **Circuit Generation**: SKiDL, KiCad 5+
- **LLM Integration**: LiteLLM (supporting Gemini, OpenAI, Claude, Groq)
- **Containerization**: Podman / Docker

## 🏗 Architecture

The system consists of a FastAPI coordinator that orchestrates the following:
1. **Planning**: An LLM creates a circuit specification from the user's prompt.
2. **Code Generation**: A separate LLM produces Python SKiDL code.
3. **Execution**: The code is run inside an ephemeral **Podman container** (worker instance).
4. **Validation**: Errors from the execution are fed back into a **Debugger agent** for automatic fixes (max 8 retries).
5. **Output**: Resulting Netlists, SVGs, and Schematics are collected and served via the API.

## 📦 Installation & Usage

### 1. Local Setup (Standard)

1. **Clone the repository:**
   ```bash
   git clone <your-repo-url>
   cd ocs_f
   ```

2. **Install system dependencies (KiCad):**
   ```bash
   # Ubuntu/Debian
   sudo add-apt-repository --yes ppa:kicad/kicad-7.0-releases
   sudo apt update
   sudo apt install --install-recommends kicad
   ```

3. **Install Python requirements:**
   ```bash
   pip install -r agent/requirements.txt
   ```

4. **Set your API Keys:**
   ```bash
   export GEMINI_API_KEY="your_key_here"
   ```

5. **Run the API:**
   ```bash
   python agent/main.py --web
   ```

### 2. Docker / Podman Setup (Isolated)

This is the recommended way for production or tech interviews.

1. **Build the image:**
   ```bash
   podman build -t skidl-agent:latest .
   ```

2. **Configure Podman in `agent/config.yaml`:**
   Set `use_podman: true` and `podman_image: "skidl-agent:latest"`.

3. **Run using Docker Compose:**
   ```bash
   docker-compose up --build
   ```

## 🔌 API Documentation

### Generate Circuit
`POST /api/v1/generate`

**Request Headers:**
- `Content-Type: application/json`

**Request Body:**
```json
{
  "description": "A simple 5V to 3.3V voltage regulator using LM1117",
  "user_id": "engineer_joe",
  "max_retries": 5
}
```

**Response:**
```json
{
  "run_id": "20260423_1130_engineer_joe_a_simple_5v_to_3",
  "success": true,
  "message": "Generation completed",
  "outputs": {
    "netlist": "/output/.../circuit.net",
    "svg": "/output/.../circuit_skin.svg",
    "script": "/output/.../circuit.py"
  },
  "usage": { "total_tokens": 1450 }
}
```

## 🚀 Deployment (GCP & Firebase)

To host this for free while maintaining "instance per user" isolation and queuing:

### 1. Deploy to Google Cloud Run
Cloud Run handles the heavy container (KiCad + Podman) and offers a massive free tier.

```bash
# Build and push the image to Google Artifact Registry
gcloud builds submit --tag gcr.io/pcbagent/api

# Deploy with Free-Tier limits and Queuing
# --max-instances=1 ensures zero cost beyond free tier
# --concurrency=1 forces a queue (one request at a time)
gcloud run deploy pcbagent-api \
  --image gcr.io/pcbagent/api \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --max-instances 1 \
  --concurrency 1 \
  --memory 2Gi \
  --cpu 1
```

### 2. Connect Firebase Hosting
Firebase Hosting provides a professional domain and SSL.

```bash
# Initialize and deploy hosting
firebase deploy --only hosting
```

## 🧪 Interview Highlights

- **User Authentication**: Integrated with **Firebase Auth (Google Sign-In)** to manage user sessions and protect API resources.
- **Isolation Strategy**: Implements per-request container instance spawning using Podman to prevent RCE (Remote Code Execution) vulnerabilities from generated scripts.
- **Cost & Rate Limiting**: Deployed with **Cloud Run concurrency limits** to simulate a processing queue and strictly enforce free-tier usage.
- **Retry Mechanism**: Self-healing loops where the agent acts as its own debugger, parsing compiler errors and fixing syntax/logic issues.
- **Developer Experience**: Comprehensive API documentation, Pydantic validation, and one-command deployment.

## 📄 License
MIT
