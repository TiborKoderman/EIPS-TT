FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CRAWLER_MODE=websocket

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

COPY pa1/crawler /app/pa1/crawler

ENV PYTHONPATH=/app/pa1/crawler/src

# Unified entry point supports both standalone and websocket (daemon) modes
# Default: webserver mode (server-managed queue via WebSocket)
# Override: CRAWLER_MODE=standalone for direct DB access and CLI utilities
ENTRYPOINT ["python", "/app/pa1/crawler/src/main.py"]
