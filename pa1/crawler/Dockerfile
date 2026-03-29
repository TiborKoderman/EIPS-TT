FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

COPY pa1/crawler /app/pa1/crawler

ENV PYTHONPATH=/app/pa1/crawler/src

# Unified entry point runs websocket daemon mode
# Default: manager-controlled runtime over authenticated WebSocket
ENTRYPOINT ["python", "/app/pa1/crawler/src/main.py"]
