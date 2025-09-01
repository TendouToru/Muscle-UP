#!/usr/bin/env bash

echo "Running database migration..."
# Alembic kann direkt ausgeführt werden, da die alembic.ini im selben Ordner ist.
alembic upgrade head

echo "Starting Gunicorn server..."

gunicorn --bind 0.0.0.0:$PORT app:app