#!/bin/sh
set -eu

# Bind mounts hide the ownership set in the image. Docker may create the
# host directory as root, so repair it before dropping privileges.
if [ "$(id -u)" = '0' ]; then
    mkdir -p /data
    chown -R ankipaper:ankipaper /data
    exec gosu ankipaper "$@"
fi

# Support an explicit docker --user / Compose user override.
exec "$@"
