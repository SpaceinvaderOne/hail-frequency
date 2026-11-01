FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# The base image's bundled pip carries open advisories; upgrade it so the
# published image audits clean, even though pip is never used at runtime.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Version stamp comes after the dependency layers so a new tag reuses them
ARG HF_VERSION=dev
ARG HF_BUILD_DATE=""
ENV HF_VERSION=$HF_VERSION \
    HF_BUILD_DATE=$HF_BUILD_DATE

COPY app/ ./app/

# Test stage: docker build --target test
FROM base AS test
RUN pip install --no-cache-dir pytest==8.3.4
COPY tests/ ./tests/
CMD ["python", "-m", "pytest", "tests", "-q"]

# Production stage (default target)
FROM base AS prod

RUN mkdir -p /app/config \
    && groupadd -r hailfrequency \
    && useradd -r -g hailfrequency -d /app -s /sbin/nologin hailfrequency

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8069

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8069/api/health')" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
# Single worker: the TTS cache, login throttle and sleep timers are in-process
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8069", "--workers", "1"]
