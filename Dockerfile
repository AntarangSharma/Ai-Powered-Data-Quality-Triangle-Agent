# Use a slim Python 3.11 image for compatibility and speed
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    DQ_STORE_URL="sqlite:////app/data/triage.db"

# Set work directory
WORKDIR /app

# Install system dependencies needed for compiling certain dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create directory for data persistence
RUN mkdir -p /app/data

# Copy pyproject.toml and README.md first to leverage Docker layer caching
COPY pyproject.toml README.md ./

# Install python dependencies in a single layer
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Copy the application source code
COPY src/ ./src/

# Install the package itself to register entrypoints and cli
RUN pip install --no-cache-dir -e .

# Expose port for Cloud Run
EXPOSE 8080

# Run FastAPI app using uvicorn
CMD exec uvicorn dq_triage.api.app:app --host 0.0.0.0 --port ${PORT}
