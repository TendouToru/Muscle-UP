# app.py

import os
import psycopg2
import psycopg2.extras # Wichtig für Wörterbuch-Cursor
from flask import Flask, render_template, request, redirect, session, url_for, flash, jsonify
import hashlib, json, secrets
from datetime import datetime, timedelta
import sqlite3

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

# --- XP-Funktionen
def calculate_xp_and_strength(user_id: int, exercises: list[dict], action="add"):
    """
    Berechnet XP und ändert die Stärke basierend auf der Aktion ('add' oder 'deduct').
    """
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor) # Verwende DictCursor für den Zugriff auf Spaltennamen
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

def calculate_level_and_progress(xp_total: int):
    level = 1
    base_xp = 100
    factor = 1.5
    xp_for_next = base_xp

    # Temporäre Variable, um xp_total nicht zu ändern
    temp_xp = xp_total

    while temp_xp >= xp_for_next:
        temp_xp -= xp_for_next
        level += 1
        xp_for_next = int(xp_for_next * factor)
        factor += 0.005

    while not xp_for_next % 10 == 0:
        xp_for_next +=1

    progress = temp_xp / xp_for_next if xp_for_next > 0 else 0
    return level, progress, int(xp_for_next), temp_xp

# Stärke Funktionen
def staerke(user_id: int):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute(
            "SELECT attr_strength, streak_days FROM user_stats WHERE user_id=%s",
            (user_id,)
        )
        kraft_db = cursor.fetchone()
        if not kraft_db:
            return 0
        base_strength = kraft_db["attr_strength"] or 0
        streak = kraft_db["streak_days"] or 0
        kraft = base_strength + (streak * 2)
        return kraft
    finally:
        conn.close()

# Streak Funktionen
def update_streak(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    try:
        today = datetime.now().date()
        
        cursor.execute(
            "SELECT date FROM workouts WHERE user_id=%s ORDER BY date DESC LIMIT 1 OFFSET 1",
            (user_id,)
        )
        last_workout = cursor.fetchone()

        if not last_workout:
            cursor.execute("UPDATE user_stats SET streak_days = 1 WHERE user_id = %s", (user_id,))
        else:
            last_date = datetime.strptime(last_workout[0], "%Y-%m-%d").date()
            yesterday = today - timedelta(days=1)
            
            if last_date == yesterday:
                cursor.execute("UPDATE user_stats SET streak_days = streak_days + 1 WHERE user_id = %s", (user_id,))
            else:
                cursor.execute("UPDATE user_stats SET streak_days = 1 WHERE user_id = %s", (user_id,))

        conn.commit()
    finally:
        conn.close()

# -- Restday
def check_restday(user_id:int):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        result = cursor.execute(
            "SELECT streak_days FROM user_stats WHERE user_id=%s", (user_id,)
        ).fetchone()

        if result:
            streak = result["streak_days"]
            restday_available = streak >= 2
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
        
# --- Hilfsfunktion für DB ---
def get_db():
    try:
        conn = psycopg2.connect(os.environ['DATABASE_URL'])
        return conn
    except KeyError:
        print("DATABASE_URL Umgebungsvariable nicht gefunden. Verwende SQLite.")
        conn = sqlite3.connect("users.db")
        conn.row_factory = sqlite3.Row
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
            date TEXT
        )
    """)
    
    conn.commit()
    cursor.close()
    conn.close()

# --- Homepage ---
@app.route("/")
def index():
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
                           progress=progress,
                           xp_for_next=xp_for_next,
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

    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
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
            xp_gained = calculate_xp_and_strength(session["user_id"], [{"exercise": exercise_name, "sets": sets}], "add")
            
            cursor.execute(
                "INSERT INTO workouts (user_id, exercise, sets, date) VALUES (%s, %s, %s, %s)",
                (session["user_id"], exercise_name, json.dumps(sets), today)
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

    cursor.execute(
        "SELECT * FROM workouts WHERE user_id=%s AND date=%s",
        (session["user_id"], today)
    )
    rows = cursor.fetchall()

    today_workouts = []
    for row in rows:
        sets_data = row["sets"]
        if isinstance(sets_data, dict):
            today_workouts.append({
                "id": row["id"],
                "exercise": row["exercise"],
                "sets": sets_data
            })
        else:
            today_workouts.append({
                "id": row["id"],
                "exercise": row["exercise"],
                "sets": json.loads(sets_data)
            })

    conn.close()
    return render_template("workouts.html", today_workouts=today_workouts, ruhe=ruhe)

# --- Workout löschen ---
@app.route("/delete_workout/<int:workout_id>", methods=["POST"])
def delete_workout(workout_id):
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
            exercises = [{"exercise": workout["exercise"], "sets": workout["sets"]}]

            xp_to_deduct = calculate_xp_and_strength(session["user_id"], exercises, "deduct")
            
            cursor.execute("""
                UPDATE user_stats
                SET xp_total = GREATEST(xp_total - %s, 0)
                WHERE user_id = %s
            """, (xp_to_deduct, session["user_id"]))

            cursor.execute("DELETE FROM workouts WHERE id=%s AND user_id=%s", (workout_id, session["user_id"]))
            conn.commit()

            update_streak(session["user_id"])

            flash(f"Workout gelöscht! {xp_to_deduct} XP wurden abgezogen.", "success")
        else:
            flash("Workout nicht gefunden.", "error")
    except Exception as e:
        conn.rollback()
        flash(f"Ein Fehler ist aufgetreten: {str(e)}", "error")
    finally:
        conn.close()
    
    return redirect(url_for("workout_page"))


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

    grouped_workouts = []
    temp_dict = {}

    for row in rows:
        d = datetime.strptime(row["date"], "%Y-%m-%d")
        display_date = d.strftime("%d.%m.%Y")

        sets_data = row["sets"]

        workout_item = {
            "id": row["id"],
            "exercise": row["exercise"],
            "sets": sets_data
        }

        if display_date not in temp_dict:
            temp_dict[display_date] = {
                "date": display_date,
                "exercises": [workout_item]
            }
        else:
            temp_dict[display_date]["exercises"].append(workout_item)

    grouped_workouts = sorted(temp_dict.values(), key=lambda x: datetime.strptime(x["date"], "%d.%m.%Y"), reverse=True)

    return render_template("fitness-kalendar.html", workouts=grouped_workouts)

#Löschen im Kalendar
@app.route("/delete_workout_calendar/<int:workout_id>", methods=["POST"])
def delete_workout_from_calendar(workout_id):
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
            exercises = [{"exercise": workout["exercise"], "sets": workout["sets"]}]
            
            xp_to_deduct = calculate_xp_and_strength(session["user_id"], exercises, "deduct")

            cursor.execute("""
                UPDATE user_stats
                SET xp_total = GREATEST(xp_total - %s, 0)
                WHERE user_id = %s
            """, (xp_to_deduct, session["user_id"]))

            cursor.execute("DELETE FROM workouts WHERE id=%s AND user_id=%s", (workout_id, session["user_id"]))
            conn.commit()
            update_streak(session["user_id"])
            
            flash(f"Workout gelöscht! {xp_to_deduct} XP wurden abgezogen.", "success")
        else:
            flash("Workout nicht gefunden.", "error")
    except Exception as e:
        conn.rollback()
        flash(f"Ein Fehler ist aufgetreten: {str(e)}", "error")
    finally:
        conn.close()

    return redirect(url_for("fitness_kalendar"))

# --- Restday verarbeiten ---
@app.route("/restday", methods=["POST"])
def post_restday():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        if check_restday(session["user_id"]):
            # Füge einen Workout-Eintrag für den Ruhetag hinzu
            cursor.execute(
                "INSERT INTO workouts (user_id, exercise, sets, date) VALUES (%s, %s, %s, %s)",
                (session["user_id"], "Restday", "[]", today)
            )
            conn.commit()
            
            # Aktualisiere den Streak
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


# --- App starten & DB vorbereiten ---
if __name__ == "__main__":
    app.run(debug=True)


