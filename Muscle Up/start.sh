#!/usr/bin/env bash

# This command ensures that Alembic runs within the Flask application context.
# The 'flask db upgrade' command is a shortcut for 'alembic upgrade head' that
# handles the application context for you. This is the simplest and most
# reliable way to fix the 'RuntimeError'.
echo "Running database migrations with Flask..."
flask db upgrade

# Check if the migration command was successful.
if [ $? -ne 0 ]; then
  echo "Database migration failed. Exiting."
  exit 1
fi

echo "Starting Gunicorn server..."
gunicorn --bind 0.0.0.0:$PORT app:app