FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MUSIC_DIR=/music \
    CONFIG_DIR=/config \
    PORT=8123

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

RUN useradd --create-home --uid 10001 appuser && \
    mkdir -p /music /config && chown -R appuser:appuser /app /config
USER appuser

EXPOSE 8123
CMD ["python", "-m", "app.main"]

