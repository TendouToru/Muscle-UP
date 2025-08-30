import os
import psycopg2
import psycopg2.extras
from flask import Flask, render_template, request, redirect, session, url_for, flash, jsonify
from collections import defaultdict
import hashlib, json, secrets, math
from datetime import datetime, timedelta
import pytz

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

# --- XP-Funktionen
def calculate_xp_and_strength(user_id: int, exercises: list[dict], action="add"):
    """
    Berechnet XP und ändert die Stärke basierend auf der Aktion ('add' oder 'deduct').
    """
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute(
            "SELECT attr_strength FROM user_stats WHERE user_id=%s",
            (user_id,)
        )
        row = cursor.fetchone()
        current_strength = row["attr_strength"] if row else 0

        cursor.execute(
            "SELECT bodyweight FROM user_profile WHERE user_id=%s",
            (user_id,)
        )
        profile = cursor.fetchone()
        bodyweight = profile["bodyweight"] if profile and profile["bodyweight"] else 0

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
            new_strength = current_strength + strength_change
        elif action == "deduct":
            new_strength = max(0, current_strength - strength_change)
        else:
            new_strength = current_strength

        cursor.execute(
            "UPDATE user_stats SET attr_strength=%s WHERE user_id=%s",
            (new_strength, user_id)
        )
        conn.commit()

        return total_xp
    finally:
        conn.close()

def calculate_xp_and_endurance(user_id: int, cardio_data: dict, action="add"):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute("SELECT attr_endurance, attr_strength, attr_intelligence FROM user_stats WHERE user_id=%s",(user_id,))
        row = cursor.fetchone()
        if not row:
            return 0
        current_endurance = row["attr_endurance"]
        current_strength = row["attr_strength"]
        current_iq = row["attr_intelligence"]

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
            endurance_new = current_endurance + endurance_change
            strength_new = current_strength + strength_change
            iq_new = current_iq + iq_change
        elif action == "deduct":
            endurance_new = max(0, current_endurance - endurance_change)
            strength_new = max(0, current_strength - strength_change)
            iq_new = max(0, current_iq - iq_change)
        else:
            endurance_new = current_endurance
            strength_new = current_strength
            iq_new = current_iq

        cursor.execute("""
            UPDATE user_stats
            SET attr_endurance = %s, attr_strength = %s, attr_intelligence = %s
            WHERE user_id=%s
        """, (endurance_new, strength_new, iq_new, user_id))

        conn.commit()
        return total_xp
    finally:
        cursor.close()
        conn.close()


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
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute("SELECT attr_strength, streak_days FROM user_stats WHERE user_id=%s", (user_id,))
        kraft_db = cursor.fetchone()
        if not kraft_db:
            return 0
        base_strength = kraft_db["attr_strength"] or 0
        streak = kraft_db["streak_days"] or 0
        kraft = base_strength + (streak * 2)
        return kraft
    finally:
        conn.close()

# Ausdauer Funktionen
def ausdauer(user_id: int):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute(
            "SELECT attr_endurance, streak_days FROM user_stats WHERE user_id=%s",(user_id,))
        ausdauer_db = cursor.fetchone()
        if not ausdauer_db:
            return 0
        base_endurance = ausdauer_db["attr_endurance"] or 0
        streak = ausdauer_db["streak_days"] or 0
        ausdauer = base_endurance + (streak * 2)
        return ausdauer
    finally:
        conn.close()

# Streak Funktionen
def update_streak(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    try:
        # Hole alle einzigartigen, sortierten Workout-Daten für den Benutzer
        cursor.execute(
            "SELECT DISTINCT date FROM workouts WHERE user_id=%s ORDER BY date DESC",
            (user_id,)
        )
        workout_dates = cursor.fetchall()
        
        current_streak = 0
        if workout_dates:
            last_date = datetime.strptime(workout_dates[0][0], "%Y-%m-%d").date()
            today = datetime.now(pytz.utc).date()
            
            # Starten Sie den Streak, wenn der letzte Trainingstag entweder heute oder gestern war
            if last_date == today or last_date == today - timedelta(days=1):
                current_streak = 1
                
                # Gehe die Daten in umgekehrter Reihenfolge durch und zähle aufeinanderfolgende Tage
                for i in range(1, len(workout_dates)):
                    current_date = datetime.strptime(workout_dates[i][0], "%Y-%m-%d").date()
                    previous_date = datetime.strptime(workout_dates[i-1][0], "%Y-%m-%d").date()
                    
                    if current_date == previous_date - timedelta(days=1):
                        current_streak += 1
                    else:
                        break # Die Streak-Kette ist unterbrochen

        # Aktualisiere den Streak-Wert in der Datenbank
        cursor.execute(
            "UPDATE user_stats SET streak_days = %s WHERE user_id = %s",
            (current_streak, user_id)
        )
        conn.commit()
    except Exception as e:
        print(f"Fehler beim Aktualisieren des Streaks: {e}")
        conn.rollback()
    finally:
        conn.close()


# -- Restday
def check_restday(user_id:int):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute(
            "SELECT streak_days FROM user_stats WHERE user_id=%s", (user_id,)
        )
        result = cursor.fetchone()

        today = datetime.now(pytz.utc).date().strftime("%Y-%m-%d")
        cursor.execute(
            "SELECT EXISTS(SELECT 1 FROM workouts WHERE user_id=%s AND date=%s AND exercise='Restday')",
            (session["user_id"], today)
        )
        restday_exists = cursor.fetchone()[0]

        if result:
            streak = result["streak_days"]
            restday_available = streak >= 2 and not restday_exists
        else:
            streak = 0
            restday_available = False

        conn.commit()
        return restday_available
    finally:
        conn.close()

def restday(user_id:int):
    conn = get_db()
    cursor = conn.cursor()
    try:
        if check_restday(session["user_id"]) == True:
            cursor.execute("UPDATE user_stats SET streak_days = streak_days + 1 WHERE user_id = %s", (user_id,))
    
        conn.commit()
    finally:
        conn.close()

#-- Ranks
def calculate_rank(user_id:int):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute(
            "SELECT xp_total FROM user_stats WHERE user_id=%s",
            (user_id,)
        )
        stats = cursor.fetchone()

        if stats:
            level, _, _, _ = calculate_level_and_progress(stats["xp_total"])
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
    finally:
        conn.close()
        
# --- Hilfsfunktion für DB ---
def get_db():
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_profile (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            bodyweight REAL,
            height REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_stats (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            xp_total INTEGER DEFAULT 0,
            streak_days INTEGER DEFAULT 0,
            attr_strength INTEGER DEFAULT 0,
            attr_endurance INTEGER DEFAULT 0,
            attr_intelligence INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS workouts (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            exercise TEXT,
            sets JSONB,
            date TEXT,
            type TEXT
        )
    """)
    
    conn.commit()
    cursor.close()
    conn.close()

# --- Homepage ---
@app.route("/")
def index():
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("""
        SELECT u.id, u.username, s.xp_total, s.streak_days
        FROM users u
        JOIN user_stats s ON u.id = s.user_id
        ORDER BY s.xp_total DESC
        LIMIT 10
    """)
    leaderboard = cursor.fetchall()
    conn.close()

    leaderboard_data = []
    for row in leaderboard:
        level, _, _, _ = calculate_level_and_progress(row["xp_total"])
        rank = calculate_rank(row["id"])
        leaderboard_data.append({
            "username": row["username"],
            "xp": row["xp_total"],
            "level": level,
            "rank": rank,
            #"profile_pic": row["profile_pic"] if row["profile_pic"] else "default.png",
            "profile_pic": "default.png.png", #standard
            "streak": row["streak_days"] or 0
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



    return render_template("index.html")

# --- Login ---
@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("profile"))

    error = None
    if request.method == "POST":
        username = request.form["username"]
        password = hashlib.sha256(request.form["password"].encode()).hexdigest()
        conn = get_db()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT * FROM users WHERE username=%s AND password=%s", (username, password))
        user = cursor.fetchone()
        if user:
            session["user_id"] = user["id"]
            session["username"] = user["username"]
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
            conn = get_db()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute("SELECT * FROM users WHERE username=%s", (username,))
            existing_user = cursor.fetchone()
            if existing_user:
                error = "Der Benutzername ist bereits vergeben."
            else:
                cursor.execute("INSERT INTO users (username, password) VALUES (%s, %s) RETURNING id", (username, hashed_password))
                user_id = cursor.fetchone()[0]

                cursor.execute("INSERT INTO user_profile (user_id, bodyweight, height) VALUES (%s, %s, %s)",
                               (user_id, 0, 0))
                cursor.execute("""INSERT INTO user_stats (
                                     user_id, xp_total, streak_days, attr_strength, attr_endurance, attr_intelligence
                               ) VALUES (%s, %s, %s, %s, %s, %s)""",
                               (user_id, 0, 0, 0, 0, 0))
                conn.commit()
                return redirect(url_for("login"))
    return render_template("register.html", error=error)

# --- Profilseite ---
@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    kraft = staerke(session["user_id"])
    ausdauerr = ausdauer(session["user_id"])
    ruhe = check_restday(session["user_id"])
    rank = calculate_rank(session["user_id"])
    
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
            cursor.execute("""
                UPDATE user_profile
                SET bodyweight=%s, height=%s
                WHERE user_id=%s
            """, (bodyweight_val, height_val, session["user_id"]))
            conn.commit()
            flash("Profil erfolgreich aktualisiert!", "success")
        except psycopg2.Error as e:
            flash(f"Fehler {e}","error")
        finally:
            conn.close()
        
        return redirect(url_for("profile"))

    cursor.execute("""
        SELECT * FROM user_profile WHERE user_id=%s
    """, (session["user_id"],))
    profile = cursor.fetchone()

    cursor.execute("""
        SELECT xp_total, streak_days, attr_strength, attr_endurance, attr_intelligence
        FROM user_stats WHERE user_id=%s
    """, (session["user_id"],))
    stats = cursor.fetchone()

    if stats:
        level, progress, xp_for_next, xp_remaining = calculate_level_and_progress(stats["xp_total"])
    else:
        level, progress, xp_for_next, xp_remaining = 1, 0, 100, 0
    conn.close()

    return render_template("profile.html",
                           profile=profile,
                           stats=stats,
                           level=level,
                           kraft=kraft,
                           ausdauer=ausdauerr,
                           ruhe=ruhe,
                           rank=rank,
                           progress=progress,
                           xp_for_next=xp_for_next,
                           xp_remaining=xp_remaining,
                           username=session["username"])

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
    conn = get_db()
    ruhe = check_restday(session["user_id"])

    # Logik für POST-Anfragen
    if request.method == "POST":
        data = request.get_json()
        if not data:
            return jsonify({"error": "Ungültiges JSON"}), 400

        exercise_name = data.get("exercise_name")
        sets = data.get("sets")
        if not exercise_name or not sets:
            return jsonify({"error": "Fehlende Daten"}), 400

        try:
            xp_gained = calculate_xp_and_strength(session["user_id"], [{"exercise": exercise_name, "sets": sets}], "add")
            
            # Überprüfe, ob die Verbindung ein psycopg2- oder sqlite3-Cursor ist
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute(
                "INSERT INTO workouts (user_id, exercise, sets, date, type) VALUES (%s, %s, %s, %s, %s)",
                (session["user_id"], exercise_name, json.dumps(sets), today, 'strength')
            )
            
            cursor.execute("""
                UPDATE user_stats
                SET xp_total = xp_total + %s
                WHERE user_id = %s
            """, (xp_gained, session["user_id"]))
            conn.commit()
            
            update_streak(session["user_id"])
            
            return jsonify({"message": "Workout erfolgreich hinzugefügt!", "xp_gained": xp_gained}), 200
        except Exception as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 500
        finally:
            conn.close()

    # Logik für GET-Anfragen (Seite anzeigen)
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute(
            "SELECT * FROM workouts WHERE user_id=%s AND date=%s",
            (session["user_id"], today)
        )
        
        rows = cursor.fetchall()

        today_workouts = []
        today_cardio_workouts = []
        
        for row in rows:
            sets_data = row["sets"]
            
            if isinstance(sets_data, str):
                sets_content = json.loads(sets_data)
            else:
                sets_content = sets_data

            workout_item = {
                "id": row["id"],
                "exercise": row["exercise"],
                "sets": sets_content,
                "type": row.get("type", 'strength')
            }

            if workout_item["type"] == "cardio":
                today_cardio_workouts.append(workout_item)
            else:
                today_workouts.append(workout_item)

    except Exception as e:
        flash(f"Ein Fehler ist aufgetreten: {e}", "error")
        today_workouts = []
        today_cardio_workouts = []
    finally:
        conn.close()

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
    
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        xp_gained = calculate_xp_and_endurance(session["user_id"], sets_data, "add")

        cursor.execute(
            "INSERT INTO workouts (user_id, exercise, type, sets, date) VALUES (%s, %s, %s, %s, %s)",
            (session["user_id"], exercise_name, 'cardio', json.dumps(sets_data), today)
        )
        cursor.execute(
            "UPDATE user_stats SET xp_total = xp_total + %s WHERE user_id = %s",
            (xp_gained, session["user_id"])
        )
        conn.commit()

        update_streak(session["user_id"])
        
        return jsonify({"message": "Kardio-Workout erfolgreich hinzugefügt!", "xp_gained": xp_gained}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# --- Zentrale Funktion zum Löschen von Workouts ---
def _delete_workout_and_update_stats(workout_id, redirect_url):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute(
            "SELECT * FROM workouts WHERE id=%s AND user_id=%s",
            (workout_id, session["user_id"])
        )
        workout = cursor.fetchone()

        if workout:
            sets_data = workout["sets"]
            if isinstance(sets_data, str):
                sets_data = json.loads(sets_data)
            
            is_cardio = workout.get("type") == "cardio"
            
            if is_cardio:
                xp_to_deduct = calculate_xp_and_endurance(session["user_id"], sets_data, "deduct")
            else:
                exercises = [{"exercise": workout["exercise"], "sets": sets_data}]
                xp_to_deduct = calculate_xp_and_strength(session["user_id"], exercises, "deduct")

            cursor.execute("""
                UPDATE user_stats
                SET xp_total = GREATEST(xp_total - %s, 0)
                WHERE user_id = %s
            """, (xp_to_deduct, session["user_id"]))

            cursor.execute("DELETE FROM workouts WHERE id=%s AND user_id=%s", (workout_id, session["user_id"]))
            conn.commit()
            
            update_streak(session["user_id"])
            
            flash(f"Workout gelöscht! {int(xp_to_deduct)} XP wurden abgezogen.", "success")
        else:
            flash("Workout nicht gefunden.", "error")
    except Exception as e:
        conn.rollback()
        flash(f"Ein Fehler ist aufgetreten: {str(e)}", "error")
    finally:
        conn.close()
    
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

    conn = get_db()
    today = datetime.now(pytz.utc).date().strftime("%Y-%m-%d")

    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute(
            "SELECT EXISTS(SELECT 1 FROM workouts WHERE user_id=%s AND date=%s AND exercise='Restday')",
            (session["user_id"], today)
        )
        restday_exists = cursor.fetchone()[0]

        if restday_exists:
            flash("Du hast für heute bereits einen Ruhetag eingetragen.", "error")
            return redirect(url_for("workout_page"))

        if check_restday(session["user_id"]):
            cursor.execute(
                "INSERT INTO workouts (user_id, exercise, sets, date, type) VALUES (%s, %s, %s, %s, %s)",
                (session["user_id"], "Restday", json.dumps([]), today, 'restday')
            )
            conn.commit()
            
            restday(session["user_id"])
            flash("Ruhetag eingetragen. Dein Streak wird fortgesetzt.", "success")
        else:
            flash("Ein Ruhetag ist erst nach mindestens 2 Trainingstagen am Stück möglich.", "error")

        return redirect(url_for("workout_page"))

    except Exception as e:
        conn.rollback()
        flash(f"Fehler beim Eintragen des Ruhetags: {str(e)}", "error")
        return redirect(url_for("workout_page"))
    finally:
        conn.close()

# --- Fitness-Kalender ---
@app.route('/fitness-kalendar')
def fitness_kalendar():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute(
        "SELECT * FROM workouts WHERE user_id=%s ORDER BY date DESC",
        (session["user_id"],)
    )
    rows = cursor.fetchall()
    conn.close()

    grouped_workouts = defaultdict(list)

    for row in rows:
        workout_date = datetime.strptime(row["date"], "%Y-%m-%d")
        display_date = workout_date.strftime("%d.%m.%Y")
        
        sets_data = row["sets"]
        if isinstance(sets_data, str):
            try:
                sets_content = json.loads(sets_data)
            except json.JSONDecodeError:
                sets_content = {}
        else:
            sets_content = sets_data

        workout_type = row.get("type")
        if not workout_type:
            workout_type = "strength"
            
        workout_item = {
            "id": row["id"],
            "exercise": row["exercise"],
            "type": row.get("type", 'strength')
        }
        
        if workout_item["type"] == "cardio":
            workout_item["duration"] = sets_content.get("duration")
            workout_item["distance"] = sets_content.get("distance")
            workout_item["sportart"] = sets_content.get("sportart")
        else:
            workout_item["sets"] = sets_content
            
        grouped_workouts[display_date].append(workout_item)
    
    sorted_workouts = sorted(grouped_workouts.items(), key=lambda item: datetime.strptime(item[0], "%d.%m.%Y"), reverse=True)

    return render_template("fitness-kalendar.html", workouts=dict(sorted_workouts))

# --- App starten & DB vorbereiten ---
if __name__ == "__main__":
    init_db()
    app.run(debug=True)



