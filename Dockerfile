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

RUN useradd --create-home appuser && mkdir -p /models
# Bake the local embedding model into the image so cold starts (and hosts with ephemeral disks,
# like Render's free tier) don't re-download it.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/models')" \
    && chown -R appuser /models
USER appuser

EXPOSE 8000
# init-db is idempotent: creates schema/roles, seeds demo data and indexes the KB on first run.
# DEMO_RESET_ON_START=true (public demo) restores pristine demo data on every cold start.
# PORT is set by hosts such as Render; defaults to 8000 locally.
CMD ["sh", "-c", "if [ \"$DEMO_RESET_ON_START\" = \"true\" ]; then ops-agent init-db --reset; else ops-agent init-db; fi && exec uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
