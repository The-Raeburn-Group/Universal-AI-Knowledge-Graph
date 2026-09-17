FROM python:3.11.16-alpine3.24 AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apk add --no-cache build-base

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --upgrade \
        pip \
        setuptools==84.0.0 \
        wheel==0.48.0 \
    && /opt/venv/bin/pip install . \
    && /opt/venv/bin/pip check

FROM python:3.11.16-alpine3.24 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

RUN apk upgrade --no-cache \
    && apk add --no-cache \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-data-eng \
        tesseract-ocr-data-osd \
    && addgroup -S -g 10001 ukg \
    && adduser -S -D -H -u 10001 -G ukg ukg

COPY --from=build /opt/venv /opt/venv
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations

# Build frontends are not required by the running service. Removing them also
# prevents vendored build-only libraries from becoming runtime attack surface.
RUN find /opt/venv/lib/python3.11/site-packages -maxdepth 1 \
        \( -name 'pip*' -o -name 'setuptools*' -o -name 'wheel*' \
           -o -name '_distutils_hack' -o -name 'pkg_resources' \) \
        -exec rm -rf '{}' + \
    && rm -f /opt/venv/bin/pip /opt/venv/bin/pip3 /opt/venv/bin/pip3.11 /opt/venv/bin/wheel

USER ukg
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"

CMD ["uvicorn", "universal_kg.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
