# syntax=docker/dockerfile:1
# optionsbot: runs the trading loop. State (SQLite journal, kill-switch file) lives in /app/data.
FROM python:3.12.15-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app

COPY requirements.txt .
# Optional: behind a TLS-intercepting proxy, pass its CA bundle as a build secret:
#   docker build --secret id=extra_ca,src=/path/to/ca-bundle.crt .
RUN --mount=type=secret,id=extra_ca \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install --no-cache-dir -r requirements.txt

COPY optionsbot ./optionsbot
COPY config.example.yaml ./
RUN mkdir -p /app/data && chown -R app:app /app/data

USER app
VOLUME ["/app/data"]

# Mount your config at /app/config.yaml and pass secrets with --env-file .env
ENTRYPOINT ["python", "-m", "optionsbot"]
CMD ["run"]
