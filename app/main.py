"""HTTP API and static web UI.

Everything runs on this machine and nothing is stored: each image is read
into memory, checked, and discarded.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app.imaging import MAX_BYTES, ImageError, assess_quality, load_image
from app.models import ApplicationData, VerificationResult
from app.ocr import LabelOCR
from app.verification.engine import verify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("label-verifier")

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # CPU threads for OCR. Set OCR_THREADS lower on a small machine, or in a container
    # (containers often report the host's cores rather than their own share).
    ocr = LabelOCR(threads=max(1, int(os.environ.get("OCR_THREADS", "4"))))
    await asyncio.to_thread(ocr.warm_up)
    app.state.ocr = ocr
    log.info("Ready.")
    yield


app = FastAPI(title="TTB Label Verifier", version="1.0.0", lifespan=lifespan)


# FastAPI's interactive API docs load Swagger UI from a CDN, so they're exempt from the strict policy.
_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith(_DOCS_PATHS):
        # Everything the UI needs is served from this origin, so nothing else is allowed
        # (this also means the tool works on networks that block CDNs).
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = response.headers.get("Cache-Control", "no-store")
    return response


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/api/verify", response_model=VerificationResult)
async def verify_label(
    request: Request,
    image: UploadFile = File(..., description="Label image (JPG, PNG, WebP)"),
    application: str = Form(..., description="Application data as JSON"),
) -> VerificationResult:
    started = time.perf_counter()
    try:
        app_data = ApplicationData.model_validate(json.loads(application))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=_friendly_validation(exc)) from exc

    data = await image.read(MAX_BYTES + 1)
    try:
        img = await asyncio.to_thread(load_image, data)
    except ImageError as exc:
        status = 413 if len(data) > MAX_BYTES else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc

    quality = await asyncio.to_thread(assess_quality, img)
    reading = await asyncio.to_thread(request.app.state.ocr.read, img, hard_to_read=bool(quality.issues))
    total_ms = round((time.perf_counter() - started) * 1000)
    result = verify(app_data, reading, quality, total_ms)
    log.info("verified verdict=%s read_ms=%d total_ms=%d", result.verdict.value, reading.elapsed_ms, total_ms)
    return result


def _friendly_validation(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        for err in exc.errors():
            if err["loc"] and err["loc"][0] == "brand_name":
                return "Brand name is required."
        return "Some application details are invalid: " + "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
    return "Application details must be sent as JSON."


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong while checking this label. Please try again."},
    )


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
