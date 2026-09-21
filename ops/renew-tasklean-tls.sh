#!/bin/sh
set -eu
if [ "${RENEWED_LINEAGE:-}" = /etc/letsencrypt/live/codex-lean-task.thinkelution.com ]; then
    nginx -t
    systemctl reload nginx
fi
