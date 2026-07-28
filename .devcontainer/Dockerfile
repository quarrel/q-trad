FROM node:22-trixie-slim AS node

FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim

ARG DEBIAN_FRONTEND=noninteractive
ARG USERNAME=vscode
ARG USER_UID=1000
ARG USER_GID=1000
ARG OMP_VERSION=17.1.7

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
        docker-cli \
        docker-compose \
        fd-find \
        file \
        git \
        gh \
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
    && case "$(dpkg --print-architecture)" in \
        amd64) codex_target="x64"; tilth_target="x86_64-unknown-linux-musl"; omp_target="x64"; omp_sha256="33be0232de8fff4d542058d666ee15cf154a660092257d4bfb3fea8bc7f9f6e0" ;; \
        arm64) codex_target="arm64"; tilth_target="aarch64-unknown-linux-musl"; omp_target="arm64"; omp_sha256="a364f2d87f8634a8ef02d765dfa3061c44be842f80971e34ae7df90359ae27e3" ;; \
        *) echo "Unsupported development-container architecture" >&2; exit 1 ;; \
    esac \
    && codex_version="$(npm view @openai/codex@latest version)" \
    && npm install --global --prefix /opt/codex-latest --ignore-scripts \
        "@openai/codex@${codex_version}" \
        "@openai/codex-linux-${codex_target}@npm:@openai/codex@${codex_version}-linux-${codex_target}" \
    && /opt/codex-latest/bin/codex --version \
    && mkdir -p node_modules/tilth/bin \
    && curl -fsSL \
        "https://github.com/jahala/tilth/releases/download/v0.9.0/tilth-${tilth_target}.tar.gz" \
        | tar xz -C node_modules/tilth/bin \
    && chmod 0755 node_modules/tilth/bin/tilth \
    && curl -fsSL \
        "https://github.com/can1357/oh-my-pi/releases/download/v${OMP_VERSION}/omp-linux-${omp_target}" \
        -o /usr/local/bin/omp \
    && echo "${omp_sha256}  /usr/local/bin/omp" | sha256sum --check --strict \
    && chmod 0755 /usr/local/bin/omp \
    && /usr/local/bin/omp --version \
    && ln -s /opt/codex-latest/bin/codex /usr/local/bin/codex

USER vscode
WORKDIR /workspace
