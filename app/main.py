import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse

from .db import init_db
from .routers.api import router
from .scheduler import start_scheduler

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    sched = start_scheduler()
    yield
    sched.shutdown(wait=False)


app = FastAPI(
    title="BizOS - digital front desk for small businesses",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(router, prefix="/api")

PANEL = Path(__file__).resolve().parent / "static" / "panel.html"


@app.get("/panel", include_in_schema=False)
def panel():
    return FileResponse(PANEL)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/panel")


@app.get("/health")
def health():
    return {"ok": True, "panel": "/panel", "docs": "/docs"}
