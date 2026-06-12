# syntax=docker/dockerfile:1.7

ARG PLAYWRIGHT_IMAGE=mcr.microsoft.com/playwright/python:v1.60.0-noble

FROM ${PLAYWRIGHT_IMAGE} AS node-runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libjpeg-turbo8 libgif7 librsvg2-2 \
    && install -d -m 0755 /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

FROM node-runtime AS spider-builder

ARG DOUYIN_SPIDER_REPO=https://github.com/xluos/DouYin_Spider.git
ARG DOUYIN_SPIDER_REF=d9766c9dd0f3bf801d3dd09facb1d24f2a1c5c53

ENV DOUYIN_SPIDER_PATH=/opt/DouYin_Spider

RUN apt-get update \
    && apt-get install -y --no-install-recommends git build-essential pkg-config libcairo2-dev libpango1.0-dev libjpeg-dev libgif-dev librsvg2-dev \
    && rm -rf /var/lib/apt/lists/*

RUN git init "${DOUYIN_SPIDER_PATH}" \
    && cd "${DOUYIN_SPIDER_PATH}" \
    && git remote add origin "${DOUYIN_SPIDER_REPO}" \
    && git fetch --depth 1 origin "${DOUYIN_SPIDER_REF}" \
    && git checkout --detach FETCH_HEAD \
    && npm ci --omit=dev --no-audit --no-fund --registry=https://registry.npmmirror.com \
    && npm cache clean --force \
    && rm -rf .git

FROM node-runtime AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DOUYIN_SPIDER_PATH=/opt/DouYin_Spider \
    SERVICE_DATA_DIR=/app/data \
    SERVICE_HOST=0.0.0.0 \
    SERVICE_PORT=18099 \
    BROWSER_CHANNEL= \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

COPY --from=spider-builder --chown=pwuser:pwuser /opt/DouYin_Spider /opt/DouYin_Spider
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir uv==0.9.9 \
    && uv sync --locked --no-dev --no-editable --compile-bytecode \
    && uv cache clean \
    && mkdir -p /app/data \
    && chown -R pwuser:pwuser /app/data

USER pwuser

EXPOSE 18099
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; port = os.environ.get('SERVICE_PORT', '18099'); urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=3).read()"

CMD ["douyin-crawler-service"]
