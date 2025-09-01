#!/usr/bin/env bash

echo "Running database migration..."
flask db upgrade --tag force_migration

echo "Starting Gunicorn server..."
gunicorn --bind 0.0.0.0:$PORT app:app