# ─────────────────────────────────────────────────────────────────────────────
# Stage 1: builder — install Python dependencies into a clean prefix
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps needed to compile some wheels (e.g. curl_cffi, uvloop)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies into an isolated prefix so we can copy just that layer.
# Strip the SSH-based editable install of the local package — we will install
# it from source in the runtime stage instead.
COPY requirements.txt .
RUN pip install --upgrade pip \
    && grep -v '^-e git' requirements.txt > /tmp/reqs_clean.txt \
    && pip install --prefix=/install --no-cache-dir -r /tmp/reqs_clean.txt


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: runtime — lean image, only what's needed to run
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL maintainer="minervini_ai"
LABEL description="Minervini AI — FastAPI backend / Streamlit dashboard / scheduler"

WORKDIR /app

# Minimal runtime system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Copy pre-built packages from builder
COPY --from=builder /install /usr/local

# Copy application source
# (frontend/ and data/ are excluded via .dockerignore)
COPY . .


# Install the local package itself (no SSH needed — source is already copied)
RUN pip install --no-deps -e .

# Create the data directory mount-point so the volume always has a home
RUN mkdir -p /app/data

# .env is NOT baked in — it is injected at runtime via docker-compose env_file
# or docker run --env-file .env

# Expose FastAPI port (override CMD for other services)
EXPOSE 8000

# Default: run the FastAPI server
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
