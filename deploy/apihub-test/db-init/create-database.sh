#!/bin/sh
# Idempotently create the API Hub platform-store database in the shared
# isap-postgres. No-op when the database already exists.
set -e

: "${POSTGRES_HOST:?POSTGRES_HOST not set}"
: "${POSTGRES_PORT:?POSTGRES_PORT not set}"
: "${POSTGRES_USER:?POSTGRES_USER not set}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD not set}"
: "${POSTGRES_DB:?POSTGRES_DB not set}"

echo "Waiting for postgres at $POSTGRES_HOST:$POSTGRES_PORT ..."
until pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -q; do
  sleep 1
done

export PGPASSWORD="$POSTGRES_PASSWORD"
if psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres -tAc \
     "SELECT 1 FROM pg_database WHERE datname='$POSTGRES_DB'" | grep -q 1; then
  echo "Database '$POSTGRES_DB' already exists — nothing to do."
else
  echo "Creating database '$POSTGRES_DB' ..."
  psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres -c \
    "CREATE DATABASE \"$POSTGRES_DB\""
  echo "Created."
fi
