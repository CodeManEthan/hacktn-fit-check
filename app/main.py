"""Entry point. Run with: uvicorn app.main:app --reload"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.data import repository
from app.web.csv_routes import router as csv_router
from app.web.marketplace_routes import router as marketplace_router
from app.web.routes import router

STATIC_DIR = Path(__file__).parent / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    repository.setup()
    yield


app = FastAPI(title="Fit-Check", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
# Before the marketplace routes: their /offer/{offer_id} would take /offer/csv.
app.include_router(csv_router)
app.include_router(marketplace_router)
app.include_router(router)
