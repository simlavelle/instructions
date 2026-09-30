# syntax=docker/dockerfile:1
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Many native threads + glibc's per-thread arenas inflate memory; two is plenty.
    MALLOC_ARENA_MAX=2

WORKDIR /srv

COPY requirements.txt .
# rapidocr pulls in the desktop build of OpenCV, which needs X11/GL libraries
# that slim images lack. Swap in the headless build of the same version.
RUN pip install -r requirements.txt \
 && OPENCV_VERSION="$(pip show opencv-python | sed -n 's/^Version: //p')" \
 && pip uninstall -y opencv-python \
 && pip install --force-reinstall --no-deps "opencv-python-headless==${OPENCV_VERSION}"

COPY app ./app

# Load the OCR models once at build time, so a broken install fails the build rather than the first request.
RUN python -c "from app.ocr import LabelOCR; LabelOCR().warm_up()"

RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT', '8000'), timeout=4)"

# Most hosts (Render, Azure Container Apps, Cloud Run) pass the port in $PORT.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*'"]
