# Build context: the repository root.   docker build -f services/api/Dockerfile -t artify-api .
# One image serves the API, the Celery worker and one-shot ops commands (the command differs).
ARG PYTHON_IMAGE=python:3.12-slim

FROM ${PYTHON_IMAGE} AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY services/api/pyproject.toml services/api/pyproject.toml
COPY services/api/alembic.ini services/api/alembic.ini
COPY services/api/migrations services/api/migrations
COPY services/api/src services/api/src
# Optional: behind a TLS-intercepting proxy pass its CA with
#   docker build --secret id=extra_ca,src=/path/to/ca.pem ...   (the secret never enters the image)
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then cat /run/secrets/extra_ca >> /etc/ssl/certs/ca-certificates.crt; fi; \
    export PIP_CERT=/etc/ssl/certs/ca-certificates.crt SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt UV_SYSTEM_CERTS=1; \
    pip install uv==0.11.32 && uv sync --frozen --no-dev --package artify-api

FROM ${PYTHON_IMAGE}
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin artify
WORKDIR /app
COPY --from=build /app /app
COPY infrastructure/database/dev_roles.sql infrastructure/database/dev_roles.sql
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER artify
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=6 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "artify_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
