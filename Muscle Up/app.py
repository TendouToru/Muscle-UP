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
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL", "sqlite:///test.db")
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

    profile = db.relationship('UserProfile', back_populates='user', lazy=True, uselist=False, cascade='all, delete-orphan')
    stats = db.relationship('UserStat', back_populates='user', lazy=True, uselist=False, cascade='all, delete-orphan')
    workouts = db.relationship('Workout', back_populates='user', lazy=True, cascade='all, delete-orphan')
    sets = db.relationship('Set', back_populates='user', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return self.username

class UserProfile(db.Model):
    __tablename__ = 'user_profile'
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    name = db.Column(db.Text)
    gender = db.Column(db.Text)
    bodyweight = db.Column(db.Float)
    height = db.Column(db.Float)
    user = db.relationship('User', back_populates='profile')

class UserStat(db.Model):
    __tablename__ = 'user_stats'
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    xp_total = db.Column(db.Integer, default=0)
    streak_days = db.Column(db.Integer, default=0)
    attr_strength = db.Column(db.Integer, default=0)
    attr_endurance = db.Column(db.Integer, default=0)
    attr_intelligence = db.Column(db.Integer, default=0)
    user = db.relationship('User', back_populates='stats')

class Workout(db.Model):
    __tablename__ = 'workouts'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'))
    exercise = db.Column(db.Text)
    date = db.Column(db.Text)
    type = db.Column(db.Text)
    
    user = db.relationship('User', back_populates='workouts')
    sets = db.relationship('Set', back_populates='workout', lazy=True, cascade="all, delete-orphan")
    
class Set(db.Model):
    __tablename__ = 'sets'
    id = db.Column(db.Integer, primary_key=True)
    workout_id = db.Column(db.Integer, db.ForeignKey('workouts.id', ondelete='CASCADE'))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'))
    reps = db.Column(db.Integer, nullable=False)
    weight = db.Column(db.Float, nullable=False)
    
    user = db.relationship('User', back_populates='sets')
    workout = db.relationship('Workout', back_populates='sets')

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
    column_list = ('id', 'username', 'is_admin', 'profile', 'stats', 'workouts', 'sets')
    column_labels = dict(id='ID', username='Benutzername', is_admin='Ist Admin', profile='Profil', stats='Statistiken', workouts='Workouts', sets='Sätze')
    column_searchable_list = ('username',)
    column_filters = ('is_admin',)
    column_default_sort = ('id', False)

class WorkoutAdmin(ModelView):
    column_list = ('id', 'user', 'date', 'type', 'exercise', 'sets')
    column_searchable_list = ('user.username', 'exercise')
    column_filters = ('user.username', 'date', 'type')
    
class SetAdmin(ModelView):
    column_list = ('id', 'user', 'workout', 'reps', 'weight')
    column_searchable_list = ('user.username',)
    column_filters = ('user.username', 'workout.date')
    
# --- Admin-Instances ---
admin = Admin(app, name='Muscle Up Admin', template_mode='bootstrap3', index_view=MyAdminIndexView())
admin.add_view(UserAdmin(User, db.session, name='Benutzer'))
admin.add_view(ModelView(UserProfile, db.session, name='Profile'))
admin.add_view(ModelView(UserStat, db.session, name='Statistiken'))
admin.add_view(WorkoutAdmin(Workout, db.session, name='Workouts'))
admin.add_view(SetAdmin(Set, db.session, name='Sätze'))


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
            weight = s.weight
            
            total_xp += 5
            if bodyweight > 0 and weight >= bodyweight:
                total_xp += int(weight / 5)
                strength_change += 2
            else:
                total_xp += int(weight / 10)
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

    duration_in_min = float(cardio_data.get("duration", 0) or 0)
    distance_in_km = float(cardio_data.get("distance", 0) or 0)
    duration_in_h = math.ceil(duration_in_min / 60)
    total_xp = 0
    endurance_change = 0
    strength_change = 0
    iq_change = 0

    if cardio_data.get("type") == "Laufen":
        total_xp += (distance_in_km * 10) + (duration_in_min / 2)
        endurance_change += math.ceil(distance_in_km // 5 + duration_in_h)
    elif cardio_data.get("type") == "Schwimmen":
        total_xp += distance_in_km * 10 - duration_in_h
        endurance_change += int(distance_in_km + duration_in_h) * 2
        strength_change += int(distance_in_km + duration_in_h) * 2
    elif cardio_data.get("type") == "Spielsport":
        total_xp += duration_in_min // 5
        endurance_change += duration_in_h
        strength_change += duration_in_h
        iq_change += duration_in_h

    if action == "add":
        user_stats.attr_endurance = (user_stats.attr_endurance or 0) + endurance_change
        user_stats.attr_strength = (user_stats.attr_strength or 0) + strength_change
        user_stats.attr_intelligence = (user_stats.attr_intelligence or 0) + iq_change
    elif action == "deduct":
        user_stats.attr_endurance = max(0, (user_stats.attr_endurance or 0) - endurance_change)
        user_stats.attr_strength = max(0, (user_stats.attr_strength or 0) - strength_change)
        user_stats.attr_intelligence = max(0, (user_stats.attr_intelligence or 0) - iq_change)
    db.session.commit()
    return total_xp
    
def calculate_level_and_progress(xp_total: int):
    level = 1
    base_xp = 100
    xp_required_for_level = base_xp

    while xp_total >= xp_required_for_level:
        xp_total -= xp_required_for_level
        level += 1
        xp_required_for_level = int(xp_required_for_level * 1.5)

    while xp_required_for_level % 10 != 0:
        xp_required_for_level += 1

    xp_for_next = xp_required_for_level
    progress = xp_total / xp_for_next if xp_for_next > 0 else 0
    xp_remaining_in_level = xp_total
    
    return level, progress, int(xp_for_next), xp_remaining_in_level

# Strength Functions
def staerke(user_id: int):
    user_stats = db.session.get(UserStat, user_id)
    if not user_stats:
        return 0
    base_strength = user_stats.attr_strength
    streak = user_stats.streak_days
    kraft = base_strength + (streak * 2)
    return kraft

# Endurance Functions
def ausdauer(user_id: int):
    user_stats = db.session.get(UserStat, user_id)
    if not user_stats:
        return 0
    base_endurance = user_stats.attr_endurance
    streak = user_stats.streak_days
    ausdauer = base_endurance + (streak * 2)
    return ausdauer

# Streak Functions
def update_streak(user_id: int):
    try:
        workout_dates_rows = db.session.query(Workout.date).filter_by(user_id=user_id).distinct().order_by(Workout.date.desc()).all()
        workout_dates = [row[0] for row in workout_dates_rows]
        
        current_streak = 0
        if workout_dates:
            parsed_dates = [datetime.strptime(d, "%Y-%m-%d").date() for d in workout_dates]
            today = datetime.now(pytz.utc).date()
            
            if parsed_dates[0] == today or parsed_dates[0] == today - timedelta(days=1):
                current_streak = 1
                
                for i in range(1, len(parsed_dates)):
                    current_date = parsed_dates[i]
                    previous_date = parsed_dates[i-1]
                    
                    if current_date == previous_date - timedelta(days=1):
                        current_streak += 1
                    else:
                        break 

        user_stats = db.session.get(UserStat, user_id)
        if user_stats:
            user_stats.streak_days = current_streak
            db.session.commit()
        
    except:
        db.session.rollback()

# Restday
def check_restday(user_id: int):
    user_stats = db.session.get(UserStat, user_id)
    if not user_stats:
        return False

    streak = user_stats.streak_days
    today = datetime.now(pytz.utc).date().strftime("%Y-%m-%d")
    restday_exists = Workout.query.filter_by(
        user_id=user_id, date=today, exercise='Restday'
    ).first() is not None

    restday_available = streak >= 2 and not restday_exists
    return restday_available

# --- Ranks ---
def calculate_rank(user_id: int):
    stats = db.session.get(UserStat, user_id)
    if stats:
        level, _, _, _ = calculate_level_and_progress(stats.xp_total)
    else:
        return 0
    
    if level <= 5:
        return 1
    elif 5 < level <= 10:
        return 2
    elif 10 < level <= 15:
        return 3
    elif 15 < level <= 20:
        return 4
    elif 20 < level <= 25:
        return 5
    elif 25 < level <= 30:
        return 6
    elif 30 < level <= 35:
        return 7
    elif 35 < level <= 40:
        return 8
    elif 40 < level <= 45:
        return 9
    elif 45 < level <= 50:
        return 10
        

# --- Homepage ---
@app.route("/")
def index():
    leaderboard = db.session.query(
        User.id, UserProfile.name, User.username, UserStat.xp_total, UserStat.streak_days
    ).outerjoin(UserStat, User.id == UserStat.user_id) \
     .outerjoin(UserProfile, User.id == UserProfile.user_id) \
     .order_by(UserStat.xp_total.desc()) \
     .limit(10) \
     .all()

    leaderboard_data = []
    for row in leaderboard:
        user_id, name, username, xp_total, streak_days = row
        level, _, _, _ = calculate_level_and_progress(xp_total)
        rank = calculate_rank(user_id)
        leaderboard_data.append({
            "name": name,
            "username": username,
            "xp": xp_total,
            "level": level,
            "rank": rank,
            "profile_pic": "default.png.png",
            "streak": streak_days
        })
    return render_template("index.html", leaderboard=leaderboard_data)

@app.template_filter("xpformat")
def xpformat_filter(value):
    try:
        value = int(value)
    except:
        return value
    if value >= 1_000_000_000:
        return f"{value/1_000_000_000:.1f}B"
    elif value >= 1_000_000:
        return f"{value/1_000_000:.1f}M"
    elif value >= 1_000:
        return f"{value/1_000:.1f}k"
    return str(value)


# --- Login ---
@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("profile"))

    error = None
    if request.method == "POST":
        username = request.form["username"]
        password = hashlib.sha256(request.form["password"].encode()).hexdigest()
        
        user = User.query.filter_by(username=username, password=password).first()
        if user:
            session["user_id"] = user.id
            session["username"] = user.username
            return redirect(url_for("profile"))
        else:
            error = "Incorrect username or password."
            
    return render_template("login.html", error=error)


# --- Registration ---
@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("profile"))

    error = None
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        confirm_password = request.form["confirm_password"]

        if password != confirm_password:
            error = "Passwords do not match."
        else:
            hashed_password = hashlib.sha256(password.encode()).hexdigest()
            
            existing_user = User.query.filter_by(username=username).first()
            if existing_user:
                error = "Username is already taken."
            else:
                try:
                    new_user = User(username=username, password=hashed_password)
                    db.session.add(new_user)
                    db.session.flush()
                    
                    new_profile = UserProfile(user_id=new_user.id, bodyweight=0, height=0, gender='', name=username)
                    new_stats = UserStat(
                        user_id=new_user.id, xp_total=0, streak_days=0,
                        attr_strength=0, attr_endurance=0, attr_intelligence=0
                    )
                    
                    db.session.add(new_profile)
                    db.session.add(new_stats)
                    db.session.commit()
                    return redirect(url_for("login"))
                except Exception as e:
                    db.session.rollback()
                    error = f"Registration error: {e}"
    return render_template("register.html", error=error)

# --- Profile Page ---
@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    user = db.session.get(User, session["user_id"])
    if not user:
        return redirect(url_for("logout")) 
        
    kraft = staerke(user.id)
    ausdauerr = ausdauer(user.id)
    ruhe = check_restday(user.id)
    rank = calculate_rank(user.id)

    if request.method == "POST":
            try:
                # Stelle sicher, dass das Profilobjekt existiert
                if not user.profile:
                    user.profile = UserProfile(user_id=user.id)

                # Prüfe, ob die Formularwerte vorhanden sind, bevor sie zugewiesen werden
                gender = request.form.get("gender")
                name = request.form.get("username")

                if name:
                    user.profile.name = name

                if gender:
                    user.profile.gender = gender

                bodyweight_str = request.form.get("bodyweight")
                if bodyweight_str:
                    user.profile.bodyweight = float(bodyweight_str)

                height_str = request.form.get("height")
                if height_str:
                    user.profile.height = float(height_str)  # Ändern von int auf float für mehr Flexibilität
                
                db.session.commit()
                flash("Profil erfolgreich aktualisiert!", "success")
                return redirect(url_for("profile"))
            except ValueError:
                db.session.rollback()
                flash("Körpergewicht und Körpergröße müssen gültige Zahlen sein.", "error")
            except Exception as e:
                db.session.rollback()
                flash(f"Fehler beim Aktualisieren des Profils: {e}", "error")
            return redirect(url_for("profile"))

    stats = user.stats
    if stats:
        level, progress, xp_for_next, xp_remaining = calculate_level_and_progress(stats.xp_total)
    else:
        level, progress, xp_for_next, xp_remaining = 1, 0, 100, 0

    return render_template("profile.html",
                           profile=user.profile,
                           stats=stats,
                           level=level,
                           kraft=kraft,
                           ausdauer=ausdauerr,
                           ruhe=ruhe,
                           rank=rank,
                           progress=progress,
                           xp_for_next=xp_for_next,
                           xp_remaining=xp_remaining,
                           username=user.username)

# --- Logout ---
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

# --- Fitness Page (Workouts) ---
@app.route('/workout', methods=['GET', 'POST'])
def workout_page():
    if "user_id" not in session:
        return redirect(url_for("login"))

    today = datetime.now(pytz.utc).date().strftime("%Y-%m-%d")
    ruhe = check_restday(session["user_id"])

    if request.method == "POST":
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON"}), 400

        exercise_name = data.get("exercise_name")
        sets_data = data.get("sets")
        if not exercise_name or not isinstance(sets_data, list):
            return jsonify({"error": "Missing data"}), 400

        try:
            new_workout = Workout(
                user_id=session["user_id"],
                exercise=exercise_name,
                date=today,
                type='strength'
            )
            db.session.add(new_workout)
            db.session.flush()

            for set_data in sets_data:
                new_set = Set(
                    workout_id=new_workout.id,
                    user_id=session["user_id"],
                    reps=set_data.get("reps"),
                    weight=set_data.get("weight")
                )
                db.session.add(new_set)
                
            xp_gained = calculate_xp_and_strength(session["user_id"], new_workout.sets, "add")
            user_stats = db.session.get(UserStat, session["user_id"])
            if user_stats:
                user_stats.xp_total = (user_stats.xp_total or 0) + xp_gained
            
            update_streak(session["user_id"])
            db.session.commit()

            return jsonify({"message": "Workout added successfully!", "xp_gained": xp_gained}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({"error": str(e)}), 500

    try:
        today_workouts = Workout.query.filter_by(
            user_id=session["user_id"], date=today, type='strength'
        ).all()
        
        today_cardio_workouts_raw = Workout.query.filter_by(
            user_id=session["user_id"], date=today, type='cardio'
        ).all()

        today_cardio_workouts = []
        for workout in today_cardio_workouts_raw:
            workout_data = {
                'id': workout.id,
                'exercise': workout.exercise,
                'type': workout.type,
                'duration': 0,
                'distance': 0
            }
            if workout.sets:
                cardio_set = workout.sets[0]
                workout_data['duration'] = cardio_set.reps
                workout_data['distance'] = cardio_set.weight
            
            today_cardio_workouts.append(workout_data)
        
    except Exception as e:
        flash(f"An error occurred: {e}", "error")
        today_workouts = []
        today_cardio_workouts = []

    return render_template("workouts.html", today_workouts=today_workouts, today_cardio_workouts=today_cardio_workouts, ruhe=ruhe)

# --- Cardio Route ---
@app.route('/add_cardio_workout', methods=['POST'])
def add_cardio_workout():
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401
    
    data = request.get_json()
    if not data or "type" not in data or "duration" not in data:
        return jsonify({"error": "Missing data"}), 400

    workout_type = data.get("type")
    duration = data.get("duration")
    today = datetime.now(pytz.utc).date().strftime("%Y-%m-%d")

    cardio_data = {}
    exercise_name = workout_type

    if workout_type == "Laufen":
        distance = data.get("distance")
        if distance is None:
            return jsonify({"error": "Distance missing"}), 400
        cardio_data = {"type": workout_type, "duration": duration, "distance": distance}
    elif workout_type == "Schwimmen":
        distance = data.get("distance")
        if distance is None:
            return jsonify({"error": "Distance missing"}), 400
        cardio_data = {"type": workout_type, "duration": duration, "distance": distance}
    elif workout_type == "Spielsport":
        sportart = data.get("sportart")
        if sportart is None:
            return jsonify({"error": "Sport type missing"}), 400
        cardio_data = {"type": workout_type, "duration": duration, "distance": 0, "sportart": sportart}
        exercise_name = sportart
    else:
        return jsonify({"error": "Invalid workout type"}), 400
    
    try:
        xp_gained = calculate_xp_and_endurance(session["user_id"], cardio_data, "add")

        new_workout = Workout(
            user_id=session["user_id"],
            exercise=exercise_name,
            type='cardio',
            date=today,
        )
        db.session.add(new_workout)
        db.session.flush()

        new_set = Set(
            workout_id=new_workout.id,
            user_id=session["user_id"],
            reps=duration,
            weight=cardio_data.get("distance", 0)
        )
        db.session.add(new_set)
        
        user_stats = db.session.get(UserStat, session["user_id"])
        if user_stats:
            user_stats.xp_total = (user_stats.xp_total or 0) + xp_gained

        update_streak(session["user_id"])
        db.session.commit()
        
        return jsonify({"message": "Cardio workout added successfully!", "xp_gained": xp_gained}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# --- Centralized function to delete workouts ---
def _delete_workout_and_update_stats(workout_id, redirect_url):
    if "user_id" not in session:
        return redirect(url_for("login"))

    try:
        workout = db.session.get(Workout, workout_id)

        if workout and workout.user_id == session["user_id"]:
            sets = workout.sets
            xp_to_deduct = 0

            if workout.type == "cardio":
                if sets:
                    cardio_type = workout.exercise
                    if cardio_type not in ['Laufen', 'Schwimmen']:
                        cardio_type = 'Spielsport'

                    cardio_data = {
                        "type": cardio_type,
                        "duration": sets[0].reps,
                        "distance": sets[0].weight if sets[0].weight is not None else 0
                    }
                    xp_to_deduct = calculate_xp_and_endurance(session["user_id"], cardio_data, "deduct")
            elif workout.type == "strength":
                xp_to_deduct = calculate_xp_and_strength(session["user_id"], sets, "deduct")
            elif workout.type == "restday":
                xp_to_deduct = 0
            
            user_stats = db.session.get(UserStat, session["user_id"])
            if user_stats:
                user_stats.xp_total = max(0, (user_stats.xp_total or 0) - xp_to_deduct)

            db.session.delete(workout)
            
            update_streak(session["user_id"])
            
            flash(f"Workout deleted! {int(xp_to_deduct)} XP deducted.", "success")
        else:
            flash("Workout not found.", "error")
    except Exception as e:
        db.session.rollback()
        flash(f"An error occurred: {str(e)}", "error")
    
    return redirect(url_for(redirect_url))

# --- Delete Workout (from Workout Page) ---
@app.route("/delete_workout/<int:workout_id>", methods=["POST"])
def delete_workout(workout_id):
    return _delete_workout_and_update_stats(workout_id, "workout_page")

# --- Delete Workout (from Calendar) ---
@app.route("/delete_workout_from_calendar/<int:workout_id>", methods=["POST"])
def delete_workout_from_calendar(workout_id):
    return _delete_workout_and_update_stats(workout_id, "fitness_kalendar")

# --- Process Restday ---
@app.route("/restday", methods=["POST"])
def post_restday():
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    try:
        today = datetime.now(pytz.utc).date().strftime("%Y-%m-%d")
        
        restday_exists = Workout.query.filter_by(
            user_id=session["user_id"], date=today, exercise='Restday'
        ).first() is not None

        if restday_exists:
            flash("You have already logged a rest day for today.", "error")
            return redirect(url_for("workout_page"))

        if check_restday(session["user_id"]):
            new_restday = Workout(
                user_id=session["user_id"],
                exercise="Restday",
                date=today,
                type='restday'
            )
            db.session.add(new_restday)
            db.session.commit()
            
            update_streak(session["user_id"])
            flash("Rest day logged. Your streak will continue.", "success")
        else:
            flash("A rest day is only possible after at least 2 consecutive training days.", "error")

        return redirect(url_for("workout_page"))

    except Exception as e:
        db.session.rollback()
        flash(f"Error logging rest day: {str(e)}", "error")
        return redirect(url_for("workout_page"))

# --- Fitness Calendar ---
@app.route('/fitness-kalendar')
def fitness_kalendar():
    if "user_id" not in session:
        return redirect(url_for("login"))

    try:
        workouts = Workout.query.filter_by(user_id=session["user_id"]).order_by(Workout.date.desc()).all()
    except Exception as e:
        flash(f"An error occurred: {e}", "error")
        workouts = []

    grouped_workouts = defaultdict(list)
    for workout_item in workouts:
        workout_date = datetime.strptime(workout_item.date, "%Y-%m-%d")
        display_date = workout_date.strftime("%d.%m.%Y")
        
        workout_data = {
            "id": workout_item.id,
            "exercise": workout_item.exercise,
            "type": workout_item.type
        }
        
        if workout_item.type == "cardio":
            if workout_item.sets:
                cardio_set = workout_item.sets[0]
                workout_data["duration"] = cardio_set.reps
                workout_data["distance"] = cardio_set.weight
            else:
                workout_data["duration"] = "N/A"
                workout_data["distance"] = "N/A"
        else:
            workout_data["sets"] = [
                {"reps": s.reps, "weight": s.weight} for s in workout_item.sets
            ]
            
        grouped_workouts[display_date].append(workout_data)
    
    sorted_workouts = sorted(grouped_workouts.items(), key=lambda item: datetime.strptime(item[0], "%d.%m.%Y"), reverse=True)

    return render_template("fitness-kalendar.html", workouts=dict(sorted_workouts))

# --- Start App & Prepare DB ---
if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(debug=True)