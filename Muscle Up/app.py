import os
from flask import Flask, render_template, request, redirect, session, url_for, flash, jsonify
from collections import defaultdict
import hashlib, json, secrets, math
from datetime import datetime, timedelta
import pytz
from flask_sqlalchemy import SQLAlchemy
from flask_admin import Admin, AdminIndexView
from flask_admin.contrib.sqla import ModelView
from flask_migrate import Migrate
from sqlalchemy import exc as sa_exc

# --- App & DB-Setup ---
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
# For local development, use a local SQLite DB
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL') or 'sqlite:///test.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# --- SQLALCHEMY Database Classes ---
class User(db.Model):
    __tablename__= 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.Text, unique=True, nullable=False)
    password = db.Column(db.Text, nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

    profile = db.relationship('UserProfile', backref='user', lazy=True, uselist=False)
    stats = db.relationship('UserStat', backref='user', lazy=True, uselist=False)
    workouts = db.relationship('Workout', backref='user', lazy=True, cascade="all, delete-orphan")
    # ✅ Korrigiert: Added sets and exercises relationships for direct access from User
    sets = db.relationship('Set', backref='user_sets', lazy=True, cascade="all, delete-orphan")
    exercises = db.relationship('Exercise', backref='user_exercises', lazy=True, cascade="all, delete-orphan")


class UserProfile(db.Model):
    __tablename__ = 'user_profile'
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    bodyweight = db.Column(db.Float)
    height = db.Column(db.Float)

class UserStat(db.Model):
    __tablename__ = 'user_stats'
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    xp_total = db.Column(db.Integer, default=0)
    streak_days = db.Column(db.Integer, default=0)
    attr_strength = db.Column(db.Integer, default=0)
    attr_endurance = db.Column(db.Integer, default=0)
    attr_intelligence = db.Column(db.Integer, default=0)

class Workout(db.Model):
    __tablename__ = 'workouts'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'))
    exercise = db.Column(db.Text)
    date = db.Column(db.Text)
    type = db.Column(db.Text)
    
    sets = db.relationship('Set', backref='workout', lazy=True, cascade="all, delete-orphan")

class Set(db.Model):
    __tablename__ = 'sets'
    id = db.Column(db.Integer, primary_key=True)
    workout_id = db.Column(db.Integer, db.ForeignKey('workouts.id', ondelete='CASCADE'))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'))
    reps = db.Column(db.Integer, nullable=False)
    weight = db.Column(db.Float, nullable=False)
    
    # ✅ Korrigiert: Direct relationship to User for the Admin view to work
    user = db.relationship('User', backref='set_user', lazy=True)
    exercises = db.relationship('Exercise', backref='set', lazy=True, cascade="all, delete-orphan")


class Exercise(db.Model):
    __tablename__ = 'exercises'
    id = db.Column(db.Integer, primary_key=True)
    set_id = db.Column(db.Integer, db.ForeignKey('sets.id', ondelete='CASCADE'))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'))
    name = db.Column(db.Text, nullable=False)

# --- Flask-Admin Configurations ---
class MyAdminIndexView(AdminIndexView):
    def is_accessible(self):
        if 'user_id' not in session:
            return False
        user = db.session.get(User, session["user_id"])
        return user is not None and user.is_admin
    
    def inaccessible_callback(self, name, **kwargs):
        return redirect(url_for('login', next=request.url))

class UserAdmin(ModelView):
    # ✅ Korrigiert: Added 'sets' and 'exercises' to column_list
    column_list = ('id', 'username', 'is_admin', 'workouts', 'sets', 'exercises')

class WorkoutAdmin(ModelView):
    column_list = ('id', 'user', 'date', 'type', 'exercise', 'sets')
    column_searchable_list = ('user.username', 'exercise')
    column_filters = ('user.username', 'date', 'type')
    
class SetAdmin(ModelView):
    column_list = ('id', 'user', 'workout', 'reps', 'weight', 'exercises')
    column_searchable_list = ('user.username',)
    column_filters = ('user.username', 'workout.date')
    
class ExerciseAdmin(ModelView):
    column_list = ('id', 'user', 'set', 'name')
    column_searchable_list = ('user.username', 'name')
    column_filters = ('user.username', 'name', 'set_id')


# --- Admin-Instances ---
admin = Admin(app, name='Muscle Up Admin', template_mode='bootstrap3', index_view=MyAdminIndexView())
admin.add_view(UserAdmin(User, db.session, name='Benutzer'))
admin.add_view(ModelView(UserProfile, db.session, name='Profile'))
admin.add_view(ModelView(UserStat, db.session, name='Statistiken'))
admin.add_view(WorkoutAdmin(Workout, db.session, name='Workouts'))
admin.add_view(SetAdmin(Set, db.session, name='Sätze'))
admin.add_view(ExerciseAdmin(Exercise, db.session, name='Übungen'))


# --- Helper Function for DB ---
def init_db():
    try:
        db.create_all()
        print("Database tables created successfully!")
    except sa_exc.OperationalError as e:
        print(f"OperationalError: {e}")
        print("Tables might already exist. This is normal during migrations.")
    except Exception as e:
        print(f"An error occurred: {e}")

@app.teardown_appcontext
def shutdown_session(exception=None):
    db.session.remove()


# --- XP Functions ---
def calculate_xp_and_strength(user_id: int, sets: list, action="add"):
    user_stats = db.session.get(UserStat, user_id)
    user_profile = db.session.get(UserProfile, user_id)

    if not user_stats or not user_profile:
        return 0

    bodyweight = user_profile.bodyweight if user_profile and user_profile.bodyweight is not None else 0

    total_xp = 0
    strength_change = 0

    for s in sets:
        try:
            # ✅ Korrigiert: Use `s.weight` directly since sets is a list of objects
            weight = s.weight
            
            total_xp += 5
            if bodyweight > 0 and weight >= bodyweight:
                total_xp += weight // 10
                strength_change += 2
            else:
                total_xp += weight // 5
                strength_change += 1
        except (ValueError, TypeError):
            continue

    if action == "add":
        user_stats.attr_strength = (user_stats.attr_strength or 0) + strength_change
    elif action == "deduct":
        user_stats.attr_strength = max(0, (user_stats.attr_strength or 0) - strength_change)
    db.session.commit()
    return total_xp

def calculate_xp_and_endurance(user_id: int, cardio_data: dict, action="add"):
    user_stats = db.session.get(UserStat, user_id)

    if not user_stats:
        return 0

    # ✅ Korrigiert: Ensure type is `float`
    duration_in_min = float(cardio_data.get("duration",0) or 0)
    duration_in_h = math.ceil(duration_in_min / 60)
    total_xp = 0
    endurance_change = 0
    strength_change = 0
    iq_change = 0

    if cardio_data.get("type") == "Laufen":
        distance_km = float(cardio_data.get("distance", 0) or 0)
        total_xp += distance_km * 10 - duration_in_min
        endurance_change += math.ceil(distance_km // 5 + duration_in_h)
    elif cardio_data.get("type") == "Schwimmen":
        distance_km = float(cardio_data.get("distance", 0) or 0)
        total_xp += distance_km * 10 - duration_in_h
        endurance_change += int(distance_km + duration_in_h) * 2
        strength_change += int(distance_km + duration_in_h) * 2
    elif cardio_data.get("type