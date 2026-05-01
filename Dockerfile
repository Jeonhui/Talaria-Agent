FROM ghcr.io/astral-sh/uv:0.11.6-python3.13-trixie@sha256:b3c543b6c4f23a5f2df22866bd7857e5d304b67a564f4feab6ac22044dde719b AS uv_source
FROM tianon/gosu:1.19-trixie@sha256:3b176695959c71e123eb390d427efc665eeb561b1540e82679c15e992006b8b9 AS gosu_source
FROM debian:13.4

# Disable Python stdout buffering so logs flush immediately.
ENV PYTHONUNBUFFERED=1

# Keep Playwright browsers outside the /opt/data volume so the build-time
# install survives the runtime volume overlay.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/talaria/.playwright

# System deps. tini reaps orphaned zombie subprocesses (MCP stdio, git, etc.)
# that accumulate when talaria runs as PID 1.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential curl python3 python3-dev libffi-dev gcc \
        ripgrep ffmpeg git openssh-client docker-cli tini procps \
        nodejs npm && \
    rm -rf /var/lib/apt/lists/*

# Non-root user for runtime; UID can be overridden via TALARIA_UID at runtime.
RUN useradd -u 10000 -m -d /opt/data talaria

COPY --chmod=0755 --from=gosu_source /gosu /usr/local/bin/
COPY --chmod=0755 --from=uv_source /usr/local/bin/uv /usr/local/bin/uvx /usr/local/bin/

WORKDIR /opt/talaria

# Layer-cached node deps for Playwright (browser tool). Copy lockfiles first.
COPY package.json package-lock.json ./
RUN npm install --omit=dev --prefer-offline --no-audit && \
    npx playwright install --with-deps chromium --only-shell && \
    npm cache clean --force

# Source.
COPY --chown=talaria:talaria . .

# Python venv with all extras (messaging, cli, pty, mcp, acp).
RUN uv venv && \
    uv pip install --no-cache-dir -e ".[all]"

# Make install dir world-readable so any TALARIA_UID can run it.
USER root
RUN chmod -R a+rX /opt/talaria && \
    chmod 0755 /opt/talaria/docker/entrypoint.sh

ENV TALARIA_HOME=/opt/data
ENV PATH="/opt/talaria/.venv/bin:/opt/data/.local/bin:${PATH}"
VOLUME ["/opt/data"]

# Entrypoint runs as root so it can remap UID/GID, then drops to the
# `talaria` user via gosu before exec'ing the talaria CLI.
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/opt/talaria/docker/entrypoint.sh"]
CMD ["gateway", "run"]
