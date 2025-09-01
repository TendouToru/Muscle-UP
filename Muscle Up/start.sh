#!/usr/bin/env bash

echo "Running database migration..."
flask db upgrade

# Exit after migration to prevent the server from starting prematurely
# Remove this line after successful migration
exit 0