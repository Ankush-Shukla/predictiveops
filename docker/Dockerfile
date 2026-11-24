# syntax=docker/dockerfile:1
# Targets: runtime (API + demo target), trainer (adds MLflow; used for training and the MLflow UI)
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv

FROM base AS runtime
COPY requirements-runtime.txt .
RUN pip install -r requirements-runtime.txt
COPY app app
COPY data data
COPY training training
COPY demo_app demo_app
COPY scripts scripts
RUN useradd --system --uid 10001 --create-home appuser \
    && mkdir -p /srv/artifacts && chown -R appuser /srv/artifacts
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=4s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["uvicorn", "app.api.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS trainer
USER root
COPY requirements-train.txt .
RUN pip install -r requirements-train.txt
USER appuser
CMD ["python", "scripts/train.py", "--help"]
