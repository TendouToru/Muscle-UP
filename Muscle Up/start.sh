#!/usr/bin/env bash

echo "Forcing database migration..."
# This command bypasses the alembic history and runs the upgrade function directly
alembic upgrade head

echo "Starting Gunicorn server..."
gunicorn --bind 0.0.0.0:$PORT app:app