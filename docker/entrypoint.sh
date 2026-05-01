#!/usr/bin/env bash
#
# Talaria Agent docker entrypoint.
#
# Runs as root to remap the internal `talaria` user (UID/GID 10000) to the
# host user that owns the bind-mounted ~/.talaria volume. Without this
# remap, files created inside the container show up as UID 10000 on the
# host and become unreadable by the actual host user.
#
# Pass the desired host UID/GID via the TALARIA_UID / TALARIA_GID env
# vars (docker-compose.yml does this automatically).
#
# After remapping, drops privileges via gosu and exec's the talaria CLI
# with whatever arguments docker passed (CMD or `docker run ... <args>`).
#

set -euo pipefail

TARGET_UID="${TALARIA_UID:-10000}"
TARGET_GID="${TALARIA_GID:-10000}"

CURRENT_UID="$(id -u talaria)"
CURRENT_GID="$(id -g talaria)"

if [[ "${TARGET_GID}" != "${CURRENT_GID}" ]]; then
    groupmod -o -g "${TARGET_GID}" talaria
fi

if [[ "${TARGET_UID}" != "${CURRENT_UID}" ]]; then
    usermod -o -u "${TARGET_UID}" talaria
fi

# Make the data volume writable by the (possibly remapped) talaria user.
# Best-effort: the volume might be a read-only bind mount, in which case
# we skip and let talaria surface the permission error itself.
chown -R talaria:talaria /opt/data 2>/dev/null || true

# Drop privileges and exec the talaria CLI. $@ comes from the Dockerfile
# CMD or `docker run ... <args>` — for example `gateway run`.
exec gosu talaria /opt/talaria/talaria "$@"
