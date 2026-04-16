# ── Build stage (keeps final image lean) ─────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build
COPY requirements-docker.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir --prefix=/install -r requirements-docker.txt \
 && pip install --no-cache-dir --prefix=/install torch torchvision \
      --index-url https://download.pytorch.org/whl/cpu

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.11-slim

# curl is needed for the HEALTHCHECK
RUN apt-get update && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# Non-root user for least-privilege execution
RUN adduser --disabled-password --gecos "" appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY app.py .
COPY src/ src/

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -sf http://localhost:8501/_stcore/health || exit 1

EXPOSE 8501

USER appuser

CMD ["python", "-m", "streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
