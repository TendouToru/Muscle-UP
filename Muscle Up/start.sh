#!/usr/bin/env bash

echo "Activating virtual environment..."
source .venv/bin/activate

echo "Running database migration script..."
python migrate_db.py

echo "Starting Gunicorn server..."
gunicorn --bind 0.0.0.0:$PORT run:app