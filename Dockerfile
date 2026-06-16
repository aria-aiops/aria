FROM python:3.11-slim

# curl is needed for the HEALTHCHECK command below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create a non-root user before copying any files.
RUN adduser --disabled-password --uid 1000 aria

# Install dependencies first so this layer is cached when only source changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source.
COPY . .

RUN chown -R aria:aria /app
USER aria

EXPOSE 8000

# Health check hits the /health endpoint — fails fast if the API is down.
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
