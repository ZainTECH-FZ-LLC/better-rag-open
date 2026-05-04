# Dockerfile — API server / query worker / celery beat
# Lightweight image without Node.js or LibreOffice

FROM python:3.12-slim AS base

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv for fast dependency management
RUN pip install --no-cache-dir uv

# Copy dependency files + source for install
COPY pyproject.toml ./
COPY config/ config/
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini ./

# Install dependencies
RUN uv pip install --system --no-cache ".[dev]"

# Download spaCy model
RUN python -m spacy download en_core_web_sm

# Create directories
RUN mkdir -p generated

EXPOSE 8000

# Default: run API server
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--loop", "uvloop", "--http", "httptools"]
