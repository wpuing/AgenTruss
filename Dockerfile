FROM python:3.11-slim
WORKDIR /app

# Non-root runtime user (UID fixed for reproducible ownership).
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

COPY pyproject.toml README.md ./
COPY app ./app
COPY config ./config
COPY templates ./templates
COPY alembic ./alembic
COPY alembic.ini ./

# Include [storage] so MinIO/Milvus adapters import when Compose enables them.
# Default RAG remains mock; missing MILVUS_URI still fails closed (no silent mock).
RUN pip install --no-cache-dir ".[storage]" \
    && chown -R appuser:appuser /app

USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
