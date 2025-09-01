from app import app, db
from flask_migrate import upgrade

with app.app_context():
    print("Starting database migration...")
    upgrade()
    print("Database migration completed.")