#!/bin/sh
set -eu
# This maintenance container never starts PostgreSQL or the Telegram bot.
# Initial deployment is inert until explicitly configured.
if [ "${RESET_AUTHORIZATION:-}" != 'betroxy-test-mobile-clear-20260922' ]; then
    echo 'BTX_TEST_MOBILE_RESET_INACTIVE authorization_not_armed'
    exit 0
fi
if [ -z "${DATABASE_URL:-}" ] || [ -z "${ADMIN_ID:-}" ] || [ -z "${TARGET_USER_ID:-}" ]; then
    echo 'BTX_TEST_MOBILE_RESET_INACTIVE configuration_incomplete'
    exit 0
fi
case "$TARGET_USER_ID:$ADMIN_ID" in *[!0-9:]*|:*|*:) echo 'Invalid numeric scope'; exit 2;; esac
case "${RESET_MODE:-preview}" in preview|apply) ;; *) echo 'Invalid reset mode'; exit 2;; esac
export PGCONNECT_TIMEOUT=10
exec psql "$DATABASE_URL" --no-psqlrc --no-align --tuples-only \
  --set=ON_ERROR_STOP=1 \
  --set=target_uid="$TARGET_USER_ID" \
  --set=target_username='mohit_97saxena' \
  --set=actor_id="$ADMIN_ID" \
  --set=reset_mode="${RESET_MODE:-preview}" \
  --file=/maintenance/reset.sql
