#!/usr/bin/env bash

echo "Running database migration..."
flask db upgrade

echo "Starting Gunicorn server..."
gunicorn --bind 0.0.0.0:$PORT app:app