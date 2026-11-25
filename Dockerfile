FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    EMBEDDING_CACHE_DIR=/models

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
COPY evals ./evals
RUN pip install --upgrade pip && pip install .

COPY db ./db
COPY data ./data

RUN useradd --create-home appuser && mkdir -p /models && chown appuser /models
USER appuser

EXPOSE 8000
# init-db is idempotent: creates schema/roles, seeds demo data and indexes the KB on first run.
CMD ["sh", "-c", "ops-agent init-db && uvicorn app.api.main:app --host 0.0.0.0 --port 8000"]
