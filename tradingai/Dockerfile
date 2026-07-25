FROM python:3.12-slim

# System deps for lxml/trafilatura wheels (kept minimal).
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SQLite db + caches live here; mount as a volume to persist across restarts.
RUN mkdir -p storage
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
