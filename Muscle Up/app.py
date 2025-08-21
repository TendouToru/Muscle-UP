from flask import Flask, render_template, request, redirect, session, url_for, flash, jsonify
import sqlite3, hashlib, json, secrets
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

# --- XP-Funktionen
def calculate_xp_and_strength(user_id: int, exercises: list[dict], action="add"):
    """
    Berechnet XP und ändert die Stärke basierend auf der Aktion ('add' oder 'deduct').
    """
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT attr_strength FROM user_stats WHERE user_id=?",
            (user_id,)
        ).fetchone()
        current_strength = row["attr_strength"] if row else 0

        profile = conn.execute(
            "SELECT bodyweight FROM user_profile WHERE user_id=?",
            (user_id,)
        ).fetchone()
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
        
        # Aktualisiere die Stärke basierend auf der Aktion
        if action == "add":
            new_strength = current_strength + strength_change
        elif action == "deduct":
            new_strength = max(0, current_strength - strength_change)
        else:
            new_strength = current_strength 

        conn.execute(
            "UPDATE user_stats SET attr_strength=? WHERE user_id=?",
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
    try:
        kraft_db = conn.execute(
            "SELECT attr_strength, streak_days FROM user_stats WHERE user_id=?",
            (user_id,)
        ).fetchone()
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
    try:
        today = datetime.now().date()
        
        # Holen Sie sich das Datum des letzten Workouts
        last_workout = conn.execute(
            "SELECT date FROM workouts WHERE user_id=? ORDER BY date DESC LIMIT 1 OFFSET 1",
            (user_id,)
        ).fetchone()

        if not last_workout:
            # Erster Workout-Tag
            conn.execute("UPDATE user_stats SET streak_days = 1 WHERE user_id = ?", (user_id,))
        else:
            last_date = datetime.strptime(last_workout["date"], "%Y-%m-%d").date()
            yesterday = today - timedelta(days=1)
            
            if last_date == yesterday:
                conn.execute("UPDATE user_stats SET streak_days = streak_days + 1 WHERE user_id = ?", (user_id,))
            else:
                conn.execute("UPDATE user_stats SET streak_days = 1 WHERE user_id = ?", (user_id,))

        conn.commit()
    finally:
        conn.close()

# --- Hilfsfunktion für DB ---
def get_db():
    conn = sqlite3.connect("users.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    # Profil
    conn.execute("""
    CREATE TABLE IF NOT EXISTS user_profile (
        user_id INTEGER PRIMARY KEY,
        bodyweight REAL,
        height REAL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)
    conn.execute("""
    INSERT INTO user_profile (user_id)
    SELECT id FROM users
    WHERE id NOT IN (SELECT user_id FROM user_profile)
    """)

    # Stats
    conn.execute("""
    CREATE TABLE IF NOT EXISTS user_stats (
        user_id INTEGER PRIMARY KEY,
        xp_total INTEGER DEFAULT 0,
        streak_days INTEGER DEFAULT 0,
        attr_strength INTEGER DEFAULT 0,
        attr_endurance INTEGER DEFAULT 0,
        attr_intelligence INTEGER DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)
    conn.execute("""
    INSERT INTO user_stats (user_id)
    SELECT id FROM users
    WHERE id NOT IN (SELECT user_id FROM user_stats)
    """)
    conn.commit()

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
        user = conn.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password)).fetchone()
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
            existing_user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if existing_user:
                error = "Der Benutzername ist bereits vergeben."
            else:
                # User anlegen
                conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed_password))
                user_id = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]

                # Profil & Stats mit Default-Werten anlegen
                conn.execute("INSERT INTO user_profile (user_id, bodyweight, height) VALUES (?, ?, ?)",
                             (user_id, 0, 0))
                conn.execute("""INSERT INTO user_stats (
                                     user_id, xp_total, streak_days, attr_strength, attr_endurance, attr_intelligence
                                 ) VALUES (?, ?, ?, ?, ?, ?)""",
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
    kraft = staerke(session["user_id"])
    # POST: Profilwerte speichern
    if request.method == "POST":
        bodyweight = request.form.get("bodyweight")
        height = request.form.get("height")

        # Sicherstellen, dass Werte nicht leer sind
        bodyweight_val =  0
        height_val =  0

        try:
            if bodyweight:
                bodyweight_val = float(bodyweight)
            if height:
                height_val = float(height)
        except ValueError:
            return redirect(url_for("profile"))

        try:
            conn.execute("""
                UPDATE user_profile
                SET bodyweight=?, height=?
                WHERE user_id=?
            """, (bodyweight_val, height_val, session["user_id"]))
            conn.commit()
        except sqlite3.Error as e:
            flash("Fehler {e}","error")
        finally:
            conn.close()

        return redirect(url_for("profile"))

    # GET: Profil laden
    profile = conn.execute("""
        SELECT * FROM user_profile WHERE user_id=?
    """, (session["user_id"],)).fetchone()

    stats = conn.execute("""
        SELECT xp_total, streak_days, attr_strength, attr_endurance, attr_intelligence
        FROM user_stats WHERE user_id=?
    """, (session["user_id"],)).fetchone()

    if stats:
        level, progress, xp_for_next, xp_remaining = calculate_level_and_progress(stats["xp_total"])
    else:
        level, progress, xp_for_next, xp_remaining = 1, 0, 100, 0

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

    if request.method == "POST":
        data = request.get_json()
        if not data:
            return jsonify({"error": "Ungültiges JSON"}), 400

        exercise_name = data.get("exercise_name")
        sets = data.get("sets")
        if not exercise_name or not sets:
            return jsonify({"error": "Fehlende Daten"}), 400

        try:
            # XP berechnen und Stärke updaten
            xp_gained = calculate_xp_and_strength(session["user_id"], [{"exercise": exercise_name, "sets": sets}], "add")
            conn.execute(
                "INSERT INTO workouts (user_id, exercise, sets, date) VALUES (?, ?, ?, ?)",
                (session["user_id"], exercise_name, json.dumps(sets), today)
            )
            conn.execute("""
                UPDATE user_stats
                SET xp_total = xp_total + ?
                WHERE user_id = ?
            """, (xp_gained, session["user_id"]))
            conn.commit()
            
            update_streak(session["user_id"])
            
            return jsonify({"message": "Workout erfolgreich hinzugefügt!", "xp_gained": xp_gained}), 200
        except Exception as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 500
        finally:
            conn.close()

    # Workouts für heute laden
    rows = conn.execute(
        "SELECT * FROM workouts WHERE user_id=? AND date=?",
        (session["user_id"], today)
    ).fetchall()

    today_workouts = []
    for row in rows:
        sets_data = row["sets"]
        if not isinstance(sets_data, str):
            sets_data = "[]"
        today_workouts.append({
            "id": row["id"],
            "exercise": row["exercise"],
            "sets": json.loads(sets_data)
        })

    return render_template("workouts.html", today_workouts=today_workouts)

# --- Workout löschen ---
@app.route("/delete_workout/<int:workout_id>", methods=["POST"])
def delete_workout(workout_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    try:
        workout = conn.execute(
            "SELECT * FROM workouts WHERE id=? AND user_id=?",
            (workout_id, session["user_id"])
        ).fetchone()

        if workout:
            exercises = [{"exercise": workout["exercise"], "sets": json.loads(workout["sets"])}]

            # XP und Stärke abziehen
            xp_to_deduct = calculate_xp_and_strength(session["user_id"], exercises, "deduct")
            
            conn.execute("""
                UPDATE user_stats
                SET xp_total = MAX(xp_total - ?, 0)
                WHERE user_id = ?
            """, (xp_to_deduct, session["user_id"]))

            # Workout löschen
            conn.execute("DELETE FROM workouts WHERE id=? AND user_id=?", (workout_id, session["user_id"]))
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
    rows = conn.execute(
        "SELECT * FROM workouts WHERE user_id=? ORDER BY date DESC",
        (session["user_id"],)
    ).fetchall()

    grouped_workouts = []
    temp_dict = {}

    for row in rows:
        d = datetime.strptime(row["date"], "%Y-%m-%d")
        display_date = d.strftime("%d.%m.%Y")

        sets_data = row["sets"]
        if not isinstance(sets_data, str):
            sets_data = "[]"

        workout_item = {
            "id": row["id"],
            "exercise": row["exercise"],
            "sets": json.loads(sets_data)
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
    try:
        workout = conn.execute(
            "SELECT * FROM workouts WHERE id=? AND user_id=?",
            (workout_id, session["user_id"])
        ).fetchone()

        if workout:
            exercises = [{"exercise": workout["exercise"], "sets": json.loads(workout["sets"])}]
            
            # XP und Stärke abziehen
            xp_to_deduct = calculate_xp_and_strength(session["user_id"], exercises, "deduct")

            conn.execute("""
                UPDATE user_stats
                SET xp_total = MAX(xp_total - ?, 0)
                WHERE user_id = ?
            """, (xp_to_deduct, session["user_id"]))

            conn.execute("DELETE FROM workouts WHERE id=? AND user_id=?", (workout_id, session["user_id"]))
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

# --- App starten & DB vorbereiten ---
if __name__ == "__main__":
    conn = sqlite3.connect("users.db")
    conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, password TEXT)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workouts (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            exercise TEXT,
            sets TEXT,
            date TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    conn.close()
    init_db()
    app.run(debug=True)