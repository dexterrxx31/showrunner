from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.routers.channel import router as channel_router

app = FastAPI(title="showrunner", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(channel_router)

# Phase 0: segments served by the app for convenience.
# Production: nginx/CDN serves these; app never touches segment bytes.
_segment_dir = Path(config.SEGMENT_DIR)
if _segment_dir.exists():
    app.mount("/segments", StaticFiles(directory=_segment_dir), name="segments")

_demo_dir = Path(__file__).resolve().parent.parent / "demo"
if _demo_dir.exists():
    app.mount("/demo", StaticFiles(directory=_demo_dir, html=True), name="demo")


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/demo/")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
