FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer cache friendly)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY agent/ ./agent/
COPY frontend/ ./frontend/
COPY run_agent.py ./

# Parquet output lives here; mount a volume to persist across runs
RUN mkdir -p output

EXPOSE 8080

CMD ["python", "run_agent.py"]
