#!/bin/bash

# Führt Datenbank-Migrationen aus
flask db upgrade

gunicorn --bind 0.0.0.0:$PORT app:app