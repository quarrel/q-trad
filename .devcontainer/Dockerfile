FROM node:22-trixie-slim AS node

FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim

ARG DEBIAN_FRONTEND=noninteractive
ARG USERNAME=vscode
ARG USER_UID=1000
ARG USER_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=300

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash-completion \
        build-essential \
        ca-certificates \
        curl \
        dnsutils \
        fd-find \
        file \
        git \
        iproute2 \
        jq \
        less \
        libatomic1 \
        lsof \
        netcat-openbsd \
        openssh-client \
        postgresql-client \
        procps \
        ripgrep \
        rsync \
        shellcheck \
        socat \
        sudo \
        tcpdump \
        tmux \
        traceroute \
        unzip \
        vim \
        zip \
    && ln -s /usr/bin/fdfind /usr/local/bin/fd \
    && ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && ln -s /usr/local/lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack \
    && corepack enable \
    && groupadd --gid "${USER_GID}" "${USERNAME}" \
    && useradd --uid "${USER_UID}" --gid "${USER_GID}" --create-home --shell /bin/bash "${USERNAME}" \
    && echo "${USERNAME} ALL=(root) NOPASSWD:ALL" >/etc/sudoers.d/"${USERNAME}" \
    && chmod 0440 /etc/sudoers.d/"${USERNAME}" \
    && rm -rf /var/lib/apt/lists/*

COPY .devcontainer/codex-install /opt/codex-install

RUN cd /opt/codex-install \
    && corepack pnpm install --no-frozen-lockfile --ignore-scripts \
    && npm install --prefix /opt/codex-latest --ignore-scripts @openai/codex@latest \
    && case "$(dpkg --print-architecture)" in \
        amd64) tilth_target="x86_64-unknown-linux-musl" ;; \
        arm64) tilth_target="aarch64-unknown-linux-musl" ;; \
        *) echo "Unsupported Tilth architecture" >&2; exit 1 ;; \
    esac \
    && mkdir -p node_modules/tilth/bin \
    && curl -fsSL \
        "https://github.com/jahala/tilth/releases/download/v0.9.0/tilth-${tilth_target}.tar.gz" \
        | tar xz -C node_modules/tilth/bin \
    && chmod 0755 node_modules/tilth/bin/tilth \
    && ln -s /opt/codex-latest/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex

USER vscode
WORKDIR /workspace
