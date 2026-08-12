# Independent release lifecycle: the runtime is built, versioned and scaled
# separately from the Frappe image and shares no volumes with it.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# uvicorn is the only runtime dependency and belongs to the image, not the code.
RUN pip install --no-cache-dir "uvicorn[standard]==0.35.0" \
    && useradd --create-home --uid 10001 runtime

COPY pyproject.toml README.md ./
COPY src ./src

USER runtime

EXPOSE 8400

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8400/healthz', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "nextgen_agent_runtime.api.asgi:application", "--host", "0.0.0.0", "--port", "8400"]
