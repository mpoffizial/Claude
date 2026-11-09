# Clipmaschine als Container – mit "restart: unless-stopped" (docker-compose.yml) startet sie
# nach Abstürzen und Server-Neustarts automatisch wieder.
#   docker compose -f clipmaschine/docker-compose.yml up -d --build
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fontconfig fonts-dejavu-core fonts-liberation ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app
COPY requirements.txt /app/clipmaschine/requirements.txt
RUN pip install --no-cache-dir -r /app/clipmaschine/requirements.txt
COPY . /app/clipmaschine/

# Alles Persistente liegt im Volume: config.yaml, .env, client_secret.json, Datenbank, Tokens, Videos, Logs
WORKDIR /data
VOLUME ["/data"]

HEALTHCHECK --interval=5m --timeout=30s --start-period=15m \
  CMD ["python", "-m", "clipmaschine", "health"]

CMD ["python", "-m", "clipmaschine", "run"]
