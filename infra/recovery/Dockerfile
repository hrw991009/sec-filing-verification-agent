# Build only from a frozen source export with recorded patch identity; never copy .env.
FROM ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c AS uv
FROM python:3.13.14-slim-bookworm@sha256:67a1e1f215ccda113cfc024e8639049257e88f273898f595b61476d128d387e8
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy PYTHONUNBUFFERED=1
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock README.md ./
COPY apps/backend/ apps/backend/
COPY evals/fixtures/sec/ evals/fixtures/sec/
RUN uv sync --locked --no-dev --package sec-filing-verification-agent-backend
ARG SOURCE_REVISION
ARG SOURCE_PATCH_SHA256=none
LABEL org.opencontainers.image.revision=$SOURCE_REVISION
LABEL org.opencontainers.image.source-patch-sha256=$SOURCE_PATCH_SHA256
ENV PATH="/app/.venv/bin:$PATH"
USER 10001:10001
CMD ["python", "-m", "uvicorn", "industry_platform.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
