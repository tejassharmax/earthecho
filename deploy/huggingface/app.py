"""EarthEcho on a Hugging Face Gradio Space (ZeroGPU hardware).

ZeroGPU Spaces must start through Gradio and declare a GPU function. EarthEcho needs no GPU: its NISAR
analysis runs on the Space's CPUs. So Gradio starts the web server, and EarthEcho's own FastAPI routes and
built React app are added to that server. Gradio keeps its own paths (/gradio_api, /config, /assets, ...);
everything else is EarthEcho.
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "backend"))
os.environ.setdefault("FRONTEND_DIST", str(HERE / "frontend" / "dist"))
os.environ.setdefault("CACHE_DIR", "/tmp/earthecho-cache")
os.environ.setdefault("EST_FILE_S", "2.5")  # files arrive fast here: first time-left estimate
if os.getenv("SPACE_HOST"):
    os.environ.setdefault("KEEP_AWAKE_URL", f"https://{os.environ['SPACE_HOST']}")
Path(os.environ["CACHE_DIR"]).mkdir(parents=True, exist_ok=True)

import gradio as gr  # noqa: E402
import spaces  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402

from app import main as earthecho  # noqa: E402


@spaces.GPU(duration=5)
def _gpu_check():
    """ZeroGPU requires one GPU function; EarthEcho itself runs on CPU."""
    return "ok"


with gr.Blocks(title="EarthEcho") as demo:
    gr.Markdown("EarthEcho is loading…")
    gr.Button("check", visible=False).click(_gpu_check, None, gr.Textbox(visible=False))

demo.queue()
demo.launch(prevent_thread_lock=True, ssr_mode=False)  # no Node proxy: Python serves every path

server = getattr(demo, "server_app", None) or demo.app
routes = server.router.routes
ours = list(earthecho.app.router.routes)
spa = [r for r in ours if getattr(r, "path", "") == "/{path:path}"]
api = [r for r in ours if r not in spa and not getattr(r, "path", "").startswith(("/docs", "/openapi", "/redoc"))]

# EarthEcho's API, its asset folder and its home page go first; its catch-all (other built files and
# client-side routes) goes last so Gradio's own paths still win.
for r in reversed(api):
    routes.insert(0, r)
if spa:
    routes.insert(0, APIRoute("/", lambda: earthecho.spa(""), methods=["GET"], include_in_schema=False))
    routes.extend(spa)

earthecho._start_background()
demo.block_thread()
