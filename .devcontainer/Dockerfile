FROM node:22-trixie-slim AS node

FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim

ARG DEBIAN_FRONTEND=noninteractive
ARG USERNAME=vscode
ARG USER_UID=1000
ARG USER_GID=1000
ARG TILTH_VERSION=0.10.1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=300

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && install -d -m 0755 /etc/apt/keyrings /etc/apt/sources.list.d \
    && curl -fsSL \
        -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
        https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    && chmod a+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        >/etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        bash-completion \
        build-essential \
        conntrack \
        dnsutils \
        docker-cli \
        docker-compose \
        fd-find \
        file \
        gdb \
        git \
        gh \
        inotify-tools \
        iproute2 \
        iputils-ping \
        jq \
        less \
        libatomic1 \
        lsof \
        netcat-openbsd \
        openssh-client \
        pandoc \
        postgresql-client \
        procps \
        ripgrep \
        rsync \
        shellcheck \
        socat \
        strace \
        sudo \
        sysstat \
        tcpdump \
        time \
        tmux \
        traceroute \
        unzip \
        vim \
        xxd \
        zip \
    && ln -s /usr/bin/fdfind /usr/local/bin/fd \
    && ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && groupadd --gid "${USER_GID}" "${USERNAME}" \
    && useradd --uid "${USER_UID}" --gid "${USER_GID}" --create-home --shell /bin/bash "${USERNAME}" \
    && install -d -m 0755 -o "${USERNAME}" -g "${USERNAME}" \
        "/home/${USERNAME}/.cache" \
        "/home/${USERNAME}/.cache/uv" \
    && echo "${USERNAME} ALL=(root) NOPASSWD:ALL" >/etc/sudoers.d/"${USERNAME}" \
    && chmod 0440 /etc/sudoers.d/"${USERNAME}" \
    && rm -rf /var/lib/apt/lists/*

RUN architecture="$(dpkg --print-architecture)" \
    && if [ "${architecture}" != "amd64" ]; then \
        echo "Unsupported development-container architecture: ${architecture} (amd64 required)" >&2; \
        exit 1; \
    fi \
    && codex_version="$(npm view @openai/codex@latest version)" \
    && npm install --global --prefix /opt/codex-latest --ignore-scripts \
        "@openai/codex@${codex_version}" \
        "@openai/codex-linux-x64@npm:@openai/codex@${codex_version}-linux-x64" \
    && /opt/codex-latest/bin/codex --version \
    && install -d -m 0755 /opt/tilth/bin \
    && curl -fsSL \
        -o /tmp/tilth.tar.gz \
        "https://github.com/jahala/tilth/releases/download/v${TILTH_VERSION}/tilth-x86_64-unknown-linux-musl.tar.gz" \
    && echo "3df16574ed9fc33e4d9c63107994c78d48f93ab0099a37232b4410223e2ad2ea  /tmp/tilth.tar.gz" \
        | sha256sum -c - \
    && tar xzf /tmp/tilth.tar.gz -C /opt/tilth/bin \
    && chmod 0755 /opt/tilth/bin/tilth \
    && /opt/tilth/bin/tilth --version \
    && unlink /tmp/tilth.tar.gz \
    && ln -s /opt/codex-latest/bin/codex /usr/local/bin/codex

USER vscode
WORKDIR /workspace
