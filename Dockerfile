FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

ARG DOUYIN_SPIDER_REPO=https://github.com/xluos/DouYin_Spider.git
ARG DOUYIN_SPIDER_REF=feature/anonymous-public-spider

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DOUYIN_SPIDER_PATH=/opt/DouYin_Spider \
    SERVICE_DATA_DIR=/app/data \
    SERVICE_HOST=0.0.0.0 \
    SERVICE_PORT=18099 \
    BROWSER_CHANNEL=

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git nodejs npm \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "${DOUYIN_SPIDER_REF}" "${DOUYIN_SPIDER_REPO}" /opt/DouYin_Spider

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir playwright==1.60.0 \
    && python -m pip install --no-cache-dir .

RUN mkdir -p /app/data

EXPOSE 18099
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18099/health', timeout=3).read()"

CMD ["douyin-crawler-service"]
