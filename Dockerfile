FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies including coinor-cbc solver
RUN apt-get update && apt-get install -y --no-install-recommends \
    coinor-cbc \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY app/ ./app/
COPY BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json .

# Expose API port
EXPOSE 8000

# Non-root user for security
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# Healthcheck for container readiness
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# Start FastAPI server binding to 0.0.0.0:8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
