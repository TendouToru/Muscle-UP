#!/usr/bin/env bash

echo "Running database migration..."
flask db downgrade base

echo "Cleaning up alembic_version table..."
# You might need to adjust this command if your alembic_version table name is different
flask shell <<EOF
from app import db
db.engine.execute("DELETE FROM alembic_version;")
db.session.commit()
EOF

echo "Forcing database migration..."
flask db upgrade --tag force_migration

# Exit after migration to prevent the server from starting prematurely
exit 0