# ── Build stage (keeps final image lean) ─────────────────────────────────────
FROM python:3.11-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
  PIP_PROGRESS_BAR=off

WORKDIR /build
COPY requirements-docker.txt .
RUN python -m pip install --no-cache-dir --prefix=/install -r requirements-docker.txt \
 && python -m pip install --no-cache-dir --prefix=/install torch torchvision \
      --index-url https://download.pytorch.org/whl/cpu

# ── Test stage ────────────────────────────────────────────────────────────────
FROM builder AS test

# Expose the builder's installed packages to Python
RUN cp -r /install/. /usr/local/

# Install test-only deps directly (no --prefix needed, not going into production)
COPY requirements-test.txt .
RUN pip install --no-cache-dir -r requirements-test.txt

WORKDIR /app
COPY app.py .
COPY src/ src/
COPY tests/ tests/

CMD ["python", "-m", "pytest", "tests/", "-v"]

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS runtime

# Non-root user for least-privilege execution
RUN adduser --disabled-password --gecos "" appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY app.py .
COPY src/ src/

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request, sys; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

EXPOSE 8501

USER appuser

CMD ["python", "-m", "streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
