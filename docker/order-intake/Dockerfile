FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system nextgen \
    && useradd --system --gid nextgen --home-dir /app nextgen

WORKDIR /app
COPY --chown=nextgen:nextgen apps/order-intake-api /app

USER nextgen
EXPOSE 8200
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8200/health', timeout=3)"]

CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8200", "--db", "/tmp/order-intake.sqlite3", "--export-dir", "/tmp/order-intake-exports"]
