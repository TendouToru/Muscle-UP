#!/bin/bash
python3 -c "from app import init_db; init_db()"
flask db upgrade
gunicorn --bind 0.0.0.0:$PORT app:app
