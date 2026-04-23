"""
web/app.py — FastAPI Web UI for SKiDL Circuit Agent
"""

import sys
import json
import asyncio
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from main import load_config, run_pipeline, _slugify
from core import get_session_usage, reset_session_usage
from pydantic import BaseModel

app = FastAPI(title="SKiDL Circuit Agent")

# ── API Models ────────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    description: str
    planner_model: str = "gemini/gemini-2.5-pro"
    codegen_model: str = "gemini/gemini-2.5-flash"
    max_retries: int = 8
    user_id: str = "default_user"

class GenerateResponse(BaseModel):
    run_id: str
    success: bool
    message: str
    outputs: dict[str, str]
    usage: dict

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# In-memory run store (keyed by run_id)
_runs: dict[str, dict] = {}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={"request": request})


@app.post("/generate")
async def generate(
    request: Request,
    description: str = Form(...),
    planner_model: str = Form("gemini/gemini-2.5-pro"),
    codegen_model: str = Form("gemini/gemini-2.5-flash"),
    max_retries: int = Form(8),
):
    """Start a circuit generation run and stream progress via SSE."""
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + _slugify(description[:30])

    config = load_config()
    config["models"]["planner"] = planner_model
    config["models"]["codegen"] = codegen_model
    config["models"]["debugger"] = codegen_model
    config["runner"]["max_retries"] = max_retries

    agent_dir = Path(__file__).parent.parent
    output_dir = agent_dir / "output" / run_id

    reset_session_usage()

    async def event_stream():
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def progress_cb(msg: str):
            asyncio.run_coroutine_threadsafe(queue.put(msg), loop)

        async def run_in_thread():
            try:
                result = await loop.run_in_executor(
                    None,
                    lambda: run_pipeline(
                        description=description,
                        config=config,
                        output_dir=output_dir,
                        progress_cb=progress_cb,
                    ),
                )
                _runs[run_id] = result
                await queue.put(f"__DONE__{run_id}")
            except Exception as e:
                await queue.put(f"__ERROR__{e}")

        asyncio.create_task(run_in_thread())

        while True:
            msg = await queue.get()
            if msg is None:
                break
            if msg.startswith("__DONE__"):
                rid = msg[8:]
                yield f"data: {{\"type\":\"done\",\"run_id\":\"{rid}\"}}\n\n"
                break
            elif msg.startswith("__ERROR__"):
                err = msg[9:]
                yield f"data: {{\"type\":\"error\",\"message\":{json.dumps(str(err))}}}\n\n"
                break
            else:
                yield f"data: {{\"type\":\"progress\",\"message\":{json.dumps(msg)}}}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/v1/generate", response_model=GenerateResponse)
async def api_generate(req: GenerateRequest):
    """
    Programmatic API endpoint to generate a circuit.
    This is a blocking call that returns the final result as JSON.
    """
    # Create a unique run ID based on user and timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_uid = _slugify(req.user_id)
    run_id = f"{timestamp}_{safe_uid}_{_slugify(req.description[:20])}"

    config = load_config()
    config["models"]["planner"] = req.planner_model
    config["models"]["codegen"] = req.codegen_model
    config["models"]["debugger"] = req.codegen_model
    config["runner"]["max_retries"] = req.max_retries

    agent_dir = Path(__file__).parent.parent
    output_dir = agent_dir / "output" / run_id

    reset_session_usage()

    loop = asyncio.get_event_loop()
    try:
        # Run the pipeline in a thread pool
        result = await loop.run_in_executor(
            None,
            lambda: run_pipeline(
                description=req.description,
                config=config,
                output_dir=output_dir,
                progress_cb=None,
            ),
        )
        _runs[run_id] = result

        outputs = {k: f"/output/{run_id}/{v.name}" for k, v in result.get("outputs", {}).items()}
        
        return GenerateResponse(
            run_id=run_id,
            success=result.get("success", False),
            message="Generation completed" if result.get("success") else "Generation failed",
            outputs=outputs,
            usage=result.get("usage", {})
        )
    except Exception as e:
        return GenerateResponse(
            run_id=run_id,
            success=False,
            message=f"Internal Error: {str(e)}",
            outputs={},
            usage={}
        )


@app.get("/result/{run_id}", response_class=HTMLResponse)
async def result(request: Request, run_id: str):
    run = _runs.get(run_id)
    if not run:
        # Try loading from disk
        agent_dir = Path(__file__).parent.parent
        output_dir = agent_dir / "output" / run_id
        if not output_dir.exists():
            return HTMLResponse("<h1>Run not found</h1>", status_code=404)
        run = {"output_dir": output_dir, "success": None}

    output_dir = Path(run["output_dir"])
    svg_content = _load_svg(output_dir)
    code = run.get("code", _load_file(output_dir, "*.py"))
    run_log = "\n".join(run.get("run_log", [])) or _load_file(output_dir, "run.log")
    spec = run.get("spec", _load_json(output_dir, "circuit_spec.json"))
    issues = [str(i) for i in run.get("issues", [])]
    usage = run.get("usage", {})
    outputs = {k: str(v) for k, v in run.get("outputs", {}).items()}

    return templates.TemplateResponse(request=request, name="result.html", context={
        "request": request,
        "run_id": run_id,
        "success": run.get("success"),
        "svg_content": svg_content,
        "code": code,
        "run_log": run_log,
        "spec": json.dumps(spec, indent=2) if spec else "{}",
        "issues": issues,
        "usage": usage,
        "outputs": outputs,
        "title": spec.get("title", run_id) if spec else run_id,
    })


@app.get("/output/{run_id}/{filename}")
async def download_output(run_id: str, filename: str):
    agent_dir = Path(__file__).parent.parent
    file_path = agent_dir / "output" / run_id / filename
    if not file_path.exists():
        return HTMLResponse("File not found", status_code=404)
    return FileResponse(str(file_path), filename=filename)


@app.get("/runs")
async def list_runs():
    agent_dir = Path(__file__).parent.parent
    output_dir = agent_dir / "output"
    runs = []
    if output_dir.exists():
        for d in sorted(output_dir.iterdir(), reverse=True):
            if d.is_dir():
                spec = _load_json(d, "circuit_spec.json")
                runs.append({
                    "id": d.name,
                    "title": spec.get("title", d.name) if spec else d.name,
                    "success": (d / "circuit_spec.json").exists(),
                })
    return {"runs": runs}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_svg(output_dir: Path) -> str:
    for pattern in ["*_skin.svg", "*.svg"]:
        matches = list(output_dir.glob(pattern))
        if matches:
            return matches[0].read_text()
    return ""


def _load_file(output_dir: Path, pattern: str) -> str:
    matches = list(output_dir.glob(pattern))
    if matches:
        return matches[0].read_text()
    return ""


def _load_json(output_dir: Path, name: str) -> dict | None:
    p = output_dir / name
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return None
