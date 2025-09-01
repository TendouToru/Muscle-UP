#!/usr/bin/env bash



echo "Running database migration script..."
python migrate_db.py

echo "Starting Gunicorn server..."
gunicorn --bind 0.0.0.0:$PORT app:app