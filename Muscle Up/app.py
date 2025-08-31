import os
from flask import Flask, render_template, request, redirect, session, url_for, flash, jsonify
from collections import defaultdict
import hashlib, json, secrets, math
from datetime import datetime, timedelta
import pytz
from flask_sqlalchemy import SQLAlchemy
from flask_admin import Admin, AdminIndexView
from flask_admin.contrib.sqla import ModelView

# --- App & DB-Setup ---
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)


# --- SQLALCHEMY Datanbankklassen ---
class User(db.Model):
    __tablename__= 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.Text, unique=True, nullable=False)
    password = db.Column(db.Text, nullable=False)

    profile = db.relationship('UserProfile', backref='user', lazy=True, uselist=False)
    stats = db.relationship('UserStat', backref='user', lazy=True, uselist=False)
    workouts = db.relationship('Workout', backref='user', lazy=True)

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
    sets = db.Column(db.JSON)
    date = db.Column(db.Text)
    type = db.Column(db.Text)

# --- Flask-Admin Konfigurationen ---
class MyAdminIndexView(AdminIndexView):
    def is_accessible(self):
        # Hier muss noch eine echte Authentifizierung hin!
        # Zum Testen: return True
        return False # Standardmäßig deaktiviert
    
    def inaccessible_callback(self, name, **kwargs):
        return redirect(url_for('login', next=request.url))

# --- Admin-Instanzen ---
admin = Admin(app, name='Muscle Up Admin', template_mode='bootstrap3', index_view=MyAdminIndexView())

admin.add_view(ModelView(User, db.session, name='Benutzer'))
admin.add_view(ModelView(UserProfile, db.session, name='Profile'))
admin.add_view(ModelView(UserStat, db.session, name='Statistiken'))
admin.add_view(ModelView(Workout, db.session, name='Workouts'))

# --- Hilfsfunktion für DB ---
def init_db():
    db.create_all()

@app.teardown_appcontext
def shutdown_session(exception=None):
    db.session.remove()


# --- XP-Funktionen ---
def calculate_xp_and_strength(user_id: int, exercises: list[dict], action="add"):
    """
    Berechnet XP und ändert die Stärke basierend auf der Aktion ('add' oder 'deduct').
    """
    user_stats = db.session.get(UserStat, user_id)
    user_profile = db.session.get(UserProfile, user_id)

    if not user_stats or not user_profile:
        return 0

    current_strength = user_stats.attr_strength if user_stats.attr_strength is not None else 0
    bodyweight = user_profile.bodyweight if user_profile and user_profile.bodyweight is not None else 0


    total_xp = 0
    strength_change = 0

    for ex in exercises:
        for s in ex["sets"]:
            try:
                weight = float(s.get("weight", 0) or 0)
            except (ValueError, TypeError):
                continue

            total_xp += 5
            if bodyweight > 0 and weight >= bodyweight:
                total_xp += weight // 10
                strength_change += 2
            else:
                total_xp += weight // 5
                strength_change += 1

    if action == "add":
        user_stats.attr_strength += strength_change
    elif action == "deduct":
        user_stats.attr_strength = max(0, user_stats.attr_strength - strength_change)

        return total_xp


def calculate_xp_and_endurance(user_id: int, cardio_data: dict, action="add"):

    user_stats = db.session.get(UserStat, user_id)

    if not user_stats:
        return 0

    current_endurance = user_stats.attr_endurance if user_stats.attr_endurance is not None else 0
    current_strength = user_stats.attr_strength if user_stats.attr_strength is not None else 0
    current_iq = user_stats.attr_intelligence if user_stats.attr_intelligence is not None else 0

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
    elif cardio_data.get("type") == "Spielsport":
        total_xp += duration_in_min // 5
        endurance_change += duration_in_h
        strength_change += duration_in_h
        iq_change += duration_in_h

    #Methode
    if action == "add":
        user_stats.attr_endurance += endurance_change
        user_stats.attr_strength += strength_change
        user_stats.attr_intelligence += iq_change
    elif action == "deduct":
        user_stats.attr_endurance = max(0, user_stats.attr_endurance - endurance_change)
        user_stats.attr_strength = max(0, user_stats.attr_strength - strength_change)
        user_stats.attr_intelligence = max(0, user_stats.attr_intelligence - iq_change)

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

# Stärke Funktionen
def staerke(user_id: int):

    user_stats = db.session.get(UserStat, user_id)

    if not user_stats:
        return 0

    base_strength = user_stats.attr_strength
    streak = user_stats.streak_days
    kraft = base_strength + (streak * 2)
    return kraft


# Ausdauer Funktionen
def ausdauer(user_id: int):
    
    user_stats = db.session.get(UserStat, user_id)
    if not user_stats:
        return 0

    base_endurance = user_stats.attr_endurance
    streak = user_stats.streak_days
    ausdauer = base_endurance + (streak * 2)
    return ausdauer


# Streak Funktionen
def update_streak(user_id: int):

    try:
        workout_dates_rows = db.session.query(Workout.date).filter_by(user_id=user_id).order_by(Workout.date.desc()).all()

        workout_dates = [row[0] for row in workout_dates_rows]
        
        current_streak = 0
        if workout_dates:
            parsed_dates = [datetime.strptime(d, "%Y-%m-%d").date() for d in workout_dates]
            today = datetime.now(pytz.utc).date()
            
            # Starten Sie den Streak, wenn der letzte Trainingstag entweder heute oder gestern war
            if parsed_dates[0] == today or parsed_dates[0] == today - timedelta(days=1):
                current_streak = 1
                
                # Gehe die Daten in umgekehrter Reihenfolge durch und zähle aufeinanderfolgende Tage
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


# -- Restday
def check_restday(user_id: int):
    user_stats = db.session.get(UserStat, user_id)
    if not user_stats:
        return False

    streak = user_stats.streak_days

    # Prüfe, ob es bereits einen Ruhetag für heute gibt
    today = datetime.now(pytz.utc).date().strftime("%Y-%m-%d")
    restday_exists = Workout.query.filter_by(
        user_id=user_id, date=today, exercise='Restday'
    ).first() is not None

    restday_available = streak >= 2 and not restday_exists
    return restday_available

def restday(user_id: int):
    if check_restday(user_id):
        user_stats = db.session.get(UserStat, user_id)
        if user_stats:
            user_stats.streak_days += 1
            db.session.commit()

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
    # Führe eine Abfrage über die Modelle aus
    leaderboard = db.session.query(
        User.id, User.username, UserStat.xp_total, UserStat.streak_days
    ).join(UserStat).order_by(UserStat.xp_total.desc()).limit(10).all()

    leaderboard_data = []
    for row in leaderboard:
        # Die Abfrage gibt ein Tuple zurück
        user_id, username, xp_total, streak_days = row
        level, _, _, _ = calculate_level_and_progress(xp_total)
        rank = calculate_rank(user_id)
        leaderboard_data.append({
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
        return f"{value/1_000_000_000:.1f}Mrd"
    elif value >= 1_000_000:
        return f"{value/1_000_000:.1f}Mio"
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
        
        # Finde den Benutzer über SQLAlchemy
        user = User.query.filter_by(username=username, password=password).first()
        if user:
            session["user_id"] = user.id
            session["username"] = user.username
            return redirect(url_for("profile"))
        else:
            error = "Benutzername oder Passwort ist falsch."
            
    return render_template("login.html", error=error)


# --- Registrierung ---
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
            error = "Die Passwörter stimmen nicht überein."
        else:
            hashed_password = hashlib.sha256(password.encode()).hexdigest()
            
            # Prüfe, ob der Benutzername bereits existiert
            existing_user = User.query.filter_by(username=username).first()
            if existing_user:
                error = "Der Benutzername ist bereits vergeben."
            else:
                try:
                    # Neue Model-Instanzen erstellen
                    new_user = User(username=username, password=hashed_password)
                    db.session.add(new_user)
                    db.session.flush() # Benötigt, um die ID vor dem Commit zu bekommen
                    
                    new_profile = UserProfile(user_id=new_user.id, bodyweight=0, height=0)
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
                    error = f"Fehler bei der Registrierung: {e}"
    return render_template("register.html", error=error)

# --- Profilseite ---
@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    # Holen des Benutzers und seiner verbundenen Datensätze
    user = db.session.get(User, session["user_id"])
    if not user:
        return redirect(url_for("logout")) 
        
    kraft = staerke(user.id)
    ausdauerr = ausdauer(user.id)
    ruhe = check_restday(user.id)
    rank = calculate_rank(user.id)

    if request.method == "POST":
        bodyweight_str = request.form.get("bodyweight")
        height_str = request.form.get("height")
        
        bodyweight_val = 0
        height_val = 0

        try:
            if bodyweight_str:
                bodyweight_val = float(bodyweight_str)
            if height_str:
                height_val = float(height_str)
        except ValueError:
            flash("Körpergewicht und Körpergröße müssen gültige Zahlen sein.", "error")
            return redirect(url_for("profile"))

        try:
            # Die Werte direkt am Objekt aktualisieren
            user.profile.bodyweight = bodyweight_val
            user.profile.height = height_val
            db.session.commit()
            flash("Profil erfolgreich aktualisiert!", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Fehler {e}","error")
        return redirect(url_for("profile"))

    # Daten für die Template-Seite
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

# --- Fitnessseite (Workouts) ---
@app.route('/workout', methods=['GET', 'POST'])
def workout_page():
    if "user_id" not in session:
        return redirect(url_for("login"))

    today = datetime.now(pytz.utc).date().strftime("%Y-%m-%d")
    ruhe = check_restday(session["user_id"])

    if request.method == "POST":
        data = request.get_json()
        if not data:
            return jsonify({"error": "Ungültiges JSON"}), 400

        exercise_name = data.get("exercise_name")
        sets = data.get("sets")
        if not exercise_name or not sets:
            return jsonify({"error": "Fehlende Daten"}), 400

        try:
            # XP berechnen
            xp_gained = calculate_xp_and_strength(session["user_id"], [{"exercise": exercise_name, "sets": sets}], "add")
            
            # Neues Workout-Objekt erstellen
            new_workout = Workout(
                user_id=session["user_id"],
                exercise=exercise_name,
                sets=sets,
                date=today,
                type='strength'
            )
            db.session.add(new_workout)
            
            # XP aktualisieren (da calculate_xp_and_strength bereits committed, müssen wir hier nicht mehr committen)
            user_stats = db.session.get(UserStat, session["user_id"])
            if user_stats:
                user_stats.xp_total += xp_gained
            
            db.session.commit()
            update_streak(session["user_id"])
            
            return jsonify({"message": "Workout erfolgreich hinzugefügt!", "xp_gained": xp_gained}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({"error": str(e)}), 500

    # Logik für GET-Anfragen (Seite anzeigen)
    try:
        today_workouts = Workout.query.filter_by(
            user_id=session["user_id"], date=today, type='strength'
        ).all()
        
        today_cardio_workouts = Workout.query.filter_by(
            user_id=session["user_id"], date=today, type='cardio'
        ).all()
        
    except Exception as e:
        flash(f"Ein Fehler ist aufgetreten: {e}", "error")
        today_workouts = []
        today_cardio_workouts = []

    return render_template("workouts.html", today_workouts=today_workouts, today_cardio_workouts=today_cardio_workouts, ruhe=ruhe)

# --- Cardio Route ---
@app.route('/add_cardio_workout', methods=['POST'])
def add_cardio_workout():
    if "user_id" not in session:
        return jsonify({"error": "Nicht angemeldet"}), 401
    
    data = request.get_json()
    if not data or "type" not in data or "duration" not in data:
        return jsonify({"error": "Fehlende Daten"}), 400

    workout_type = data.get("type")
    duration = data.get("duration")
    today = datetime.now(pytz.utc).date().strftime("%Y-%m-%d")

    sets_data = {}
    exercise_name = workout_type

    if workout_type == "Laufen":
        distance = data.get("distance")
        if distance is None:
            return jsonify({"error": "Distanz fehlt"}), 400
        sets_data = {"type": workout_type, "duration": duration, "distance": distance}
    elif workout_type == "Schwimmen":
        distance = data.get("distance")
        if distance is None:
            return jsonify({"error": "Distanz fehlt"}), 400
        sets_data = {"type": workout_type, "duration": duration, "distance": distance}
    elif workout_type == "Spielsport":
        sportart = data.get("sportart")
        if sportart is None:
            return jsonify({"error": "Sportart fehlt"}), 400
        sets_data = {"type": workout_type, "duration": duration, "sportart": sportart}
        exercise_name = sportart
    else:
        return jsonify({"error": "Ungültiger Workout-Typ"}), 400
    
    try:
        xp_gained = calculate_xp_and_endurance(session["user_id"], sets_data, "add")

        new_workout = Workout(
            user_id=session["user_id"],
            exercise=exercise_name,
            type='cardio',
            sets=sets_data,
            date=today
        )
        db.session.add(new_workout)
        
        user_stats = db.session.get(UserStat, session["user_id"])
        if user_stats:
            user_stats.xp_total += xp_gained

        db.session.commit()
        update_streak(session["user_id"])
        
        return jsonify({"message": "Kardio-Workout erfolgreich hinzugefügt!", "xp_gained": xp_gained}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# --- Zentrale Funktion zum Löschen von Workouts ---
def _delete_workout_and_update_stats(workout_id, redirect_url):
    if "user_id" not in session:
        return redirect(url_for("login"))

    try:
        # Finde das Workout über SQLAlchemy
        workout = db.session.get(Workout, workout_id)

        if workout and workout.user_id == session["user_id"]:
            sets_data = workout.sets
            is_cardio = workout.type == "cardio"
            
            if is_cardio:
                xp_to_deduct = calculate_xp_and_endurance(session["user_id"], sets_data, "deduct")
            else:
                exercises = [{"exercise": workout.exercise, "sets": sets_data}]
                xp_to_deduct = calculate_xp_and_strength(session["user_id"], exercises, "deduct")

            # XP abziehen
            user_stats = db.session.get(UserStat, session["user_id"])
            if user_stats:
                user_stats.xp_total = max(0, user_stats.xp_total - xp_to_deduct)

            # Workout löschen
            db.session.delete(workout)
            db.session.commit()
            
            update_streak(session["user_id"])
            
            flash(f"Workout gelöscht! {int(xp_to_deduct)} XP wurden abgezogen.", "success")
        else:
            flash("Workout nicht gefunden.", "error")
    except Exception as e:
        db.session.rollback()
        flash(f"Ein Fehler ist aufgetreten: {str(e)}", "error")
    
    return redirect(url_for(redirect_url))

# --- Workout löschen (von der Workout-Seite) ---
@app.route("/delete_workout/<int:workout_id>", methods=["POST"])
def delete_workout(workout_id):
    return _delete_workout_and_update_stats(workout_id, "workout_page")

# --- Workout löschen (vom Kalender) ---
@app.route("/delete_workout_from_calendar/<int:workout_id>", methods=["POST"])
def delete_workout_from_calendar(workout_id):
    return _delete_workout_and_update_stats(workout_id, "fitness_kalendar")

# --- Restday verarbeiten ---
@app.route("/restday", methods=["POST"])
def post_restday():
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    try:
        today = datetime.now(pytz.utc).date().strftime("%Y-%m-%d")
        
        # Prüfe, ob es bereits einen Ruhetag gibt
        restday_exists = Workout.query.filter_by(
            user_id=session["user_id"], date=today, exercise='Restday'
        ).first() is not None

        if restday_exists:
            flash("Du hast für heute bereits einen Ruhetag eingetragen.", "error")
            return redirect(url_for("workout_page"))

        if check_restday(session["user_id"]):
            new_restday = Workout(
                user_id=session["user_id"],
                exercise="Restday",
                sets={},
                date=today,
                type='restday'
            )
            db.session.add(new_restday)
            db.session.commit()
            
            restday(session["user_id"])
            flash("Ruhetag eingetragen. Dein Streak wird fortgesetzt.", "success")
        else:
            flash("Ein Ruhetag ist erst nach mindestens 2 Trainingstagen am Stück möglich.", "error")

        return redirect(url_for("workout_page"))

    except Exception as e:
        db.session.rollback()
        flash(f"Fehler beim Eintragen des Ruhetags: {str(e)}", "error")
        return redirect(url_for("workout_page"))

# --- Fitness-Kalender ---
@app.route('/fitness-kalendar')
def fitness_kalendar():
    if "user_id" not in session:
        return redirect(url_for("login"))

    try:
        # Hole alle Workouts des Benutzers, sortiert nach Datum
        workouts = Workout.query.filter_by(user_id=session["user_id"]).order_by(Workout.date.desc()).all()
    except Exception as e:
        flash(f"Ein Fehler ist aufgetreten: {e}", "error")
        workouts = []

    grouped_workouts = defaultdict(list)
    for workout_item in workouts:
        # Die Daten sind bereits Python-Objekte, keine Notwendigkeit für json.loads
        workout_date = datetime.strptime(workout_item.date, "%Y-%m-%d")
        display_date = workout_date.strftime("%d.%m.%Y")
        
        workout_data = {
            "id": workout_item.id,
            "exercise": workout_item.exercise,
            "type": workout_item.type
        }
        
        if workout_item.type == "cardio":
            workout_data["duration"] = workout_item.sets.get("duration")
            workout_data["distance"] = workout_item.sets.get("distance")
            workout_data["sportart"] = workout_item.sets.get("sportart")
        else:
            workout_data["sets"] = workout_item.sets
            
        grouped_workouts[display_date].append(workout_data)
    
    sorted_workouts = sorted(grouped_workouts.items(), key=lambda item: datetime.strptime(item[0], "%d.%m.%Y"), reverse=True)

    return render_template("fitness-kalendar.html", workouts=dict(sorted_workouts))

# --- App starten & DB vorbereiten ---
if __name__ == "__main__":
    with app.app_context()
        init_db()
    app.run(debug=True)



