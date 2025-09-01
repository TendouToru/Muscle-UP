#!/usr/bin/env bash

echo "Running database migration..."
# Change into the directory where the alembic.ini and migrations folder are located
cd "Muscle Up"

# Run the migration
alembic upgrade head

# Go back to the parent directory to run gunicorn
cd ..

echo "Starting Gunicorn server..."
gunicorn --bind 0.0.0.0:$PORT app:app