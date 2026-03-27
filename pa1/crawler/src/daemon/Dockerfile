FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CRAWLER_MODE=websocket

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

# Package full project crawler/daemon tree so daemon can spawn local workers.
COPY pa1/crawler /app/pa1/crawler

ENV PYTHONPATH=/app/pa1/crawler/src

# Compatibility wrapper entry point: delegates to unified main.py with CRAWLER_MODE=websocket
# Prefer pa1/crawler/src/main.py directly for new deployments
ENTRYPOINT ["python", "/app/pa1/crawler/src/daemon/main.py"]
