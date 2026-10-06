# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DB_PATH=/data/bot.db \
    PORT=8080

WORKDIR /app

# Install dependencies first so Docker can cache them.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./

# The SQLite database lives here; mount a persistent volume over it on the host.
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8080

CMD ["python", "bot.py"]