FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim

ARG DEBIAN_FRONTEND=noninteractive

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=120

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libatomic1 nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY tests ./tests
COPY migrations ./migrations
COPY config ./config
COPY alembic.ini ./

RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen

RUN useradd --create-home --uid 10001 qtrad \
    && chown -R qtrad:qtrad /app

USER qtrad

ENTRYPOINT ["uv", "run", "--frozen"]
CMD ["python", "-m", "qtrad", "--help"]
