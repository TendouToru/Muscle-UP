import os
from werkzeug.utils import secure_filename
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
from PIL import Image
import base64
import requests
from github import Github

# --- App & DB-Setup ---
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL", "sqlite:///test.db")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'profile_pics')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Github Configuration
app.config['GITHUB_TOKEN'] = os.environ.get('GITHUB_TOKEN')
app.config['GITHUB_REPO'] = os.environ.get('GITHUB_REPO', 'TendouToru/Muscle-UP')
app.config['GITHUB_BRANCH'] = os.environ.get('GITHUB_BRANCH', 'main')

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
    age = db.Column(db.Integer)
    bodyweight = db.Column(db.Float)
    height = db.Column(db.Float)
    region = db.Column(db.Text, default='de')
    profile_pic = db.Column(db.Text, default='default.png')
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


# --- Helper Function for DB und Cloud ---
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


def upload_to_github(image_data, filename):
    """Lädt Bild zu GitHub mit Debug-Informationen"""
    try:
        token = app.config['GITHUB_TOKEN']
        repo_path = app.config['GITHUB_REPO']
        branch = app.config['GITHUB_BRANCH']
        
        if not token:
            print("❌ GitHub Token nicht konfiguriert!")
            return False
        
        print(f"🔄 Versuche Upload zu: {repo_path}/static/profile_pics/{filename}")
        
        # ✅ KORRIGIERT: static/profile_pics/ verwenden ggf Muscle Up/static/...
        url = f"https://api.github.com/repos/{repo_path}/contents/Muscle%20Up/static/profile_pics/{filename}"
        
        # Base64 encoden
        content_base64 = base64.b64encode(image_data).decode('utf-8')
        
        # Request an GitHub API
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        data = {
            "message": f"Add profile picture {filename}",
            "content": content_base64,
            "branch": branch
        }
        
        print(f"📤 Sende Request an: {url}")
        
        response = requests.put(url, headers=headers, json=data, timeout=15)
        
        print(f"📥 Response: {response.status_code} - {response.text}")
        
        if response.status_code in [200, 201]:
            print(f"✅ Bild erfolgreich hochgeladen: {filename}")
            return True
        else:
            print(f"❌ GitHub API Fehler: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Error uploading to GitHub: {e}")
        return False

def get_github_url(filename):
    """Generiert URL für Bilder im static/profile_pics Ordner"""
    if not filename or filename == 'default.png':
        filename = 'default.png'
    
    username = app.config['GITHUB_REPO'].split('/')[0]
    repo_name = app.config['GITHUB_REPO'].split('/')[1]
    branch = app.config['GITHUB_BRANCH']
    
    # ✅ KORRIGIERT: static/profile_pics/ verwenden
    return f"https://raw.githubusercontent.com/{username}/{repo_name}/{branch}/Muscle%20Up/static/profile_pics/{filename}"



@app.route("/test_github_config")
def test_github_config():
    """Testet die GitHub Konfiguration"""
    token = app.config['GITHUB_TOKEN']
    repo = app.config['GITHUB_REPO']
    branch = app.config['GITHUB_BRANCH']
    
    result = f"Token: {'✅' if token else '❌'} {token[:10] if token else ''}...<br>"
    result += f"Repo: {repo}<br>"
    result += f"Branch: {branch}<br>"
    
    if token:
        try:
            import requests
            headers = {"Authorization": f"token {token}"}
            response = requests.get(f"https://api.github.com/repos/{repo}", headers=headers, timeout=10)
            result += f"Repo Zugriff: {response.status_code}<br>"
            if response.status_code == 200:
                result += "✅ Repository gefunden und zugreifbar"
            else:
                result += f"❌ Fehler: {response.text}"
        except Exception as e:
            result += f"❌ Exception: {e}"
    
    return result



# --- XP Functions ---
def calculate_xp_and_strength(user_id: int, sets: list, action="add"):
    user_stats = db.session.get(UserStat, user_id)
    user_profile = db.session.get(UserProfile, user_id)

    if not user_stats or not user_profile:
        return 0

    bodyweight = user_profile.bodyweight or 70  
    total_xp = 0
    strength_change = 0

    for i, s in enumerate(sets, start=1):
        reps = s.reps or 0
        weight = s.weight or 0

        # --- Volumen ---
        volume = weight * reps

        # --- Satzfaktor (Diminishing Returns) ---
        set_factor = 1 + max(1 - (i - 1) * 0.05, 0.5)

        # --- Intensität ---
        if weight >= 1.5 * bodyweight:
            intensity_factor = 1.5
        elif weight >= bodyweight:
            intensity_factor = 1.2
        elif weight < 0.5 * bodyweight:
            intensity_factor = 0.8
        else:
            intensity_factor = 1.0

        # --- XP-Berechnung ---
        xp_set = (volume / 10) * set_factor * intensity_factor
        total_xp += xp_set

        # --- Stärke-Änderung (optional grob) ---
        strength_change += int((volume / bodyweight) * 0.1)

    if action == "add":
        user_stats.attr_strength = (user_stats.attr_strength or 0) + strength_change
    elif action == "deduct":
        user_stats.attr_strength = max(0, (user_stats.attr_strength or 0) - strength_change)

    db.session.commit()
    return int(total_xp)



def calculate_xp_and_endurance(user_id: int, cardio_data: dict, action="add"):
    """
    Berechnet XP + Attribut-Änderungen für Cardio.
    cardio_data = {
        "type": "Laufen" / "Schwimmen" / "Spielsport",
        "duration": Minuten,
        "distance": km (falls vorhanden)
    }
    """

    user_stats = db.session.get(UserStat, user_id)
    if not user_stats:
        return 0

    try:
        duration = float(cardio_data.get("duration", 0) or 0)  # Minuten
        distance = float(cardio_data.get("distance", 0) or 0)  # km
        sport_type = cardio_data.get("type", "").lower()
    except (ValueError, TypeError):
        return 0

    total_xp = 0
    endurance_change = 0
    strength_change = 0
    iq_change = 0

    # --- Lauf-Formel ---
    if sport_type == "laufen":
        speed = (distance / (duration / 60)) if duration > 0 else 0  # km/h
        total_xp += duration * 1                      # 1 XP pro Minute
        total_xp += distance * 10                     # 10 XP pro km
        total_xp *= (1 + min(speed / 20, 0.5))        # Bonus je schneller, max +50%
        endurance_change += int(distance // 2) + int(duration // 30)

    # --- Schwimm-Formel ---
    elif sport_type == "schwimmen":
        speed = (distance / (duration / 60)) if duration > 0 else 0
        total_xp += duration * 2                      # Schwimmen ist intensiver
        total_xp += distance * 15                     # mehr XP pro km
        total_xp *= (1 + min(speed / 8, 0.5))         # Bonus je schneller, max +50%
        endurance_change += int(distance) + int(duration // 20)
        strength_change += int(distance // 2)         # Schwimmen gibt auch Kraft

    # --- Spielsport (Fußball, Basketball etc.) ---
    elif sport_type == "spielsport":
        total_xp += duration * 2                      # XP rein nach Dauer
        endurance_change += int(duration // 20)
        strength_change += int(duration // 40)
        iq_change += int(duration // 30)              # Taktik & Teamplay = "Intelligenz"

    # --- Streak-Bonus ---
    streak_bonus = (user_stats.streak_days or 0) * 0.05
    total_xp *= (1 + streak_bonus)

    # --- Attribute anwenden ---
    if action == "add":
        user_stats.attr_endurance = (user_stats.attr_endurance or 0) + endurance_change
        user_stats.attr_strength = (user_stats.attr_strength or 0) + strength_change
        user_stats.attr_intelligence = (user_stats.attr_intelligence or 0) + iq_change
        user_stats.xp_total = (user_stats.xp_total or 0) + int(total_xp)
    elif action == "deduct":
        user_stats.attr_endurance = max(0, (user_stats.attr_endurance or 0) - endurance_change)
        user_stats.attr_strength = max(0, (user_stats.attr_strength or 0) - strength_change)
        user_stats.attr_intelligence = max(0, (user_stats.attr_intelligence or 0) - iq_change)
        user_stats.xp_total = max(0, (user_stats.xp_total or 0) - int(total_xp))

    db.session.commit()
    return int(total_xp)

def calculate_xp_for_calestenics(user_id:int, sets:list, action="add"):
    
    user_stats = db.session.get(UserStat, user_id)
    user_profile = db.session.get(UserProfile, user_id)
    if not user_stats or not user_profile:
        return 0
    
    total_xp = 0
    strength_change = 0
    endurance_change = 0

    for i, s in enumerate(sets, start=1):
        reps = s.reps or 0
        
        set_factor = 1 + max(1- (i - 1) * 0.05, 0.5)

        if 5 > reps > 0:
            strength_change += 3
        elif 10 > reps >= 5:
            strength_change +=2
            endurance_change +=1
        elif 15 > reps >= 10:
            strength_change += 1
            endurance_change += 2
        elif reps >= 15:
            endurance_change += 3
        else:
            continue

        total_xp += reps * 2 * set_factor

    if action == "add":
        user_stats.xp_total = (user_stats.xp_total or 0) + int(total_xp)
        user_stats.attr_endurance = (user_stats.attr_endurance or 0) + endurance_change 
        user_stats.attr_strength = (user_stats.attr_strength or 0) + strength_change
    elif action == "deduct":
        user_stats.xp_total = max((user_stats.xp_total or 0) - int(total_xp))
        user_stats.attr_endurance = max(0, (user_stats.attr_endurance or 0) - endurance_change)
        user_stats.attr_strength = max(0, (user_stats.attr_strength or 0) - strength_change)
    db.session.commit()
    return int(total_xp)

    
def calculate_level_and_progress(xp_total: int, base_xp: int = 100, growth: float = 1.10):
    """
    Gibt (level, progress, xp_for_next, xp_in_current_level) zurück.
    - base_xp: XP für Level 1 -> 2
    - growth: Multiplikator pro Level (z.B. 1.12)
    - Rundung: jede Stufe auf das nächste Vielfache von 10
    """
    level = 1
    xp_in_level = xp_total

    # XP-Anforderung für nächste Stufe (Level 1 -> 2)
    xp_req_next = base_xp
    # auf Zehner runden (nach oben)
    xp_req_next = int(math.ceil(xp_req_next / 10.0) * 10)

    # so lange wir genug XP für den nächsten Level-Up haben
    while xp_in_level >= xp_req_next:
        xp_in_level -= xp_req_next
        level += 1
        # nächste Stufe berechnen und direkt runden
        xp_req_next = int(math.ceil((xp_req_next * growth) / 10.0) * 10)

    # Fortschritt in aktueller Stufe
    progress = xp_in_level / xp_req_next if xp_req_next > 0 else 0.0
    return level, progress, xp_req_next, xp_in_level

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
def check_restday(user_id: int, date_str = None):
    user_stats = db.session.get(UserStat, user_id)
    if not user_stats:
        return False

    if date_str:
        today = datetime.strptime(date_str, "%Y-%m-%d").date()
    else:
        today = datetime.now(pytz.utc).date()

    streak = user_stats.streak_days
    restday_exists_1 = Workout.query.filter_by(
        user_id=user_id, date=today.strftime("%Y-%m-%d"), exercise='Restday'
    ).first() is not None

    yesterday = today - timedelta(days=1)
    yesterday_str = yesterday.strftime("%Y-%m-%d")
    
    yesterday_restday = Workout.query.filter_by(
        user_id=user_id, date=yesterday_str, exercise='Restday'
    ).first() is not None


    restday_available = streak >= 2 and not restday_exists_1 and not yesterday_restday
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
    # 1. Daten aus der Datenbank abfragen
    leaderboard = db.session.query(
        User.id, UserProfile.name, UserProfile.region, UserProfile.profile_pic, User.username, UserStat.xp_total, UserStat.streak_days
    ).outerjoin(UserStat, User.id == UserStat.user_id) \
     .outerjoin(UserProfile, User.id == UserProfile.user_id) \
     .order_by(UserStat.xp_total.desc()) \
     .limit(10) \
     .all()

    # 2. Daten verarbeiten
    leaderboard_data = []
    for row in leaderboard:
        user_id, name, region, profile_pic, username, xp_total, streak_days = row
        level, _, _, _ = calculate_level_and_progress(xp_total)
        rank = calculate_rank(user_id)
        
        # WICHTIG: Hier get_github_url verwenden!
        profile_pic_url = get_github_url(profile_pic) if profile_pic else get_github_url('default.png')
        
        leaderboard_data.append({
            "name": name,
            "username": username,
            "xp": xp_total,
            "level": level,
            "rank": rank,
            "region": region,
            "profile_pic": profile_pic or 'default.png',
            "profile_pic_url": profile_pic_url,
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

    today = datetime.now(pytz.utc).date().strftime("%Y-%m-%d")
        
    kraft = staerke(user.id)
    ausdauerr = ausdauer(user.id)
    ruhe = check_restday(user.id, today)
    rank = calculate_rank(user.id)

    if request.method == "POST":
            try:
                if not user.profile:
                    user.profile = UserProfile(user_id=user.id)

                gender = request.form.get("gender")
                name = request.form.get("username")
                age = request.form.get("age")
                bodyweight_str = request.form.get("bodyweight")
                height_str = request.form.get("height")
                region = request.form.get("region")
                profile_pic_path = request.form.get("profile_pic_path")

                if name:
                    user.profile.name = name

                if gender:
                    user.profile.gender = gender

                if age:
                    user.profile.age = age

                if bodyweight_str:
                    user.profile.bodyweight = float(bodyweight_str)

                if height_str:
                    user.profile.height = float(height_str) 

                if region:
                    user.profile.region = region 

                if 'profile_pic' in request.files:
                    file = request.files['profile_pic']
                    if file.filename != '':
                        filename = secure_filename(str(user.id) + '_' + file.filename)
                        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                        
                        img = Image.open(file)
                        img.thumbnail((300, 300))
                        img.save(file_path)
                        
                        user.profile.profile_pic = filename
                
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

# --- Profilbild ---
@app.route("/upload_profile_pic", methods=["POST"])
def upload_profile_pic():
    if 'user_id' not in session:
        return jsonify({"success": False, "error": "Nicht angemeldet"}), 401

    if 'profile_pic' not in request.files:
        return jsonify({"success": False, "error": "Keine Datei ausgewählt"}), 400

    file = request.files['profile_pic']
    if file.filename == '':
        return jsonify({"success": False, "error": "Keine Datei ausgewählt"}), 400

    try:
        from PIL import Image
        import io
        
        # Bild verarbeiten
        img = Image.open(file.stream).convert('RGB')
        img.thumbnail((200, 200), Image.Resampling.LANCZOS)
        
        # In Bytes umwandeln
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='JPEG')
        img_data = img_byte_arr.getvalue()
        
        # Benutzerprofil holen
        user_profile = db.session.get(UserProfile, session["user_id"])
        if not user_profile:
            return jsonify({"success": False, "error": "Benutzerprofil nicht gefunden"}), 404
        
        # Neues Bild hochladen
        filename = f"user_{session['user_id']}_{secrets.token_hex(8)}.jpg"
        
        if upload_to_github(img_data, filename):
            # ✅ WICHTIG: Dateinamen in der DB speichern
            user_profile.profile_pic = filename
            db.session.commit()
            
            # ✅ Neue GitHub URL generieren
            new_url = get_github_url(filename)
            
            return jsonify({
                "success": True, 
                "filename": filename,
                "url": new_url
            }), 200
        else:
            return jsonify({"success": False, "error": "Fehler beim Hochladen zu GitHub"}), 500

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": f"Fehler beim Verarbeiten des Bildes: {e}"}), 500


@app.route('/profile_pic/<filename>')
def get_profile_pic(filename):
    """Serve profile pictures from database"""
    if filename == 'default.png':
        return redirect(url_for('static', filename='profile_pics/default.png'))
    
    user_profile = UserProfile.query.filter_by(profile_pic_filename=filename).first()
    if user_profile and user_profile.profile_pic_data:
        return Response(user_profile.profile_pic_data, mimetype='image/jpeg')
    
    # Fallback to default image
    return redirect(url_for('static', filename='profile_pics/default.png'))


# Context Processor um Profildaten global verfügbar zu machen
@app.context_processor
def inject_profile_data():
    if 'user_id' in session:
        user = db.session.get(User, session["user_id"])
        if user and user.profile:
            profile_pic = user.profile.profile_pic
            profile_data = {
                'name': user.profile.name,
                'gender': user.profile.gender,
                'age': user.profile.age,
                'bodyweight': user.profile.bodyweight,
                'height': user.profile.height,
                'profile_pic': profile_pic,
                'profile_pic_url': get_github_url(profile_pic) if profile_pic else get_github_url('default.png')
            }
            return {'current_user_profile': profile_data}
    
    return {'current_user_profile': {
        'profile_pic_url': get_github_url('default.png')
    }}

# --- Logout ---
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# Route zum Abrufen von Workouts für ein bestimmtes Datum
@app.route('/get_workouts_for_date')
def get_workouts_for_date():
    if "user_id" not in session:
        return jsonify({"error": "Nicht angemeldet"}), 401
    
    date = request.args.get('date')
    if not date:
        return jsonify({"error": "Datum fehlt"}), 400
    
    try:
        # Workouts für das angegebene Datum abrufen
        strength_workouts = Workout.query.filter_by(
            user_id=session["user_id"], 
            date=date, 
            type='strength'
        ).all()
        
        cardio_workouts_raw = Workout.query.filter_by(
            user_id=session["user_id"], 
            date=date, 
            type='cardio'
        ).all()
        
        calisthenics_workouts = Workout.query.filter_by(
            user_id=session["user_id"], 
            date=date, 
            type='calistenics'
        ).all()
        
        # Workouts für das Frontend aufbereiten
        strength_data = []
        for workout in strength_workouts:
            strength_data.append({
                'id': workout.id,
                'exercise': workout.exercise,
                'sets': [{'reps': s.reps, 'weight': s.weight} for s in workout.sets]
            })
        
        cardio_data = []
        for workout in cardio_workouts_raw:
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
            
            cardio_data.append(workout_data)
        
        calisthenics_data = []
        for workout in calisthenics_workouts:
            calisthenics_data.append({
                'id': workout.id,
                'exercise': workout.exercise,
                'sets': [{'reps': s.reps, 'weight': s.weight} for s in workout.sets]
            })
        
        return jsonify({
            'strength_workouts': strength_data,
            'cardio_workouts': cardio_data,
            'calisthenics_workouts': calisthenics_data,
            'date': date
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Route zum Hinzufügen von Workouts für ein bestimmtes Datum
@app.route('/add_workout_for_date', methods=['POST'])
def add_workout_for_date():
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401
    
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400
    
    workout_date = data.get("date")
    if not workout_date:
        return jsonify({"error": "Datum fehlt"}), 400
    
    # Je nach Workout-Typ die entsprechende Verarbeitung durchführen
    workout_type = data.get("type")
    
    try:
        if workout_type == "strength":
            # Verarbeitung für Krafttraining
            exercise_name = data.get("exercise_name")
            sets_data = data.get("sets")
            
            if not exercise_name or not isinstance(sets_data, list):
                return jsonify({"error": "Missing data"}), 400

            new_workout = Workout(
                user_id=session["user_id"],
                exercise=exercise_name,
                date=workout_date,
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
            
        elif workout_type == "cardio":
            # Verarbeitung für Cardio
            exercise_type = data.get("exercise_type")
            duration = data.get("duration")
            exercise_name = data.get("exercise_name", exercise_type)
            
            if not exercise_type or not duration:
                return jsonify({"error": "Missing data"}), 400

            cardio_data = {}
            if exercise_type == "Laufen":
                distance = data.get("distance")
                if distance is None:
                    return jsonify({"error": "Distance missing"}), 400
                cardio_data = {"type": exercise_type, "duration": duration, "distance": distance}
            elif exercise_type == "Schwimmen":
                distance = data.get("distance")
                if distance is None:
                    return jsonify({"error": "Distance missing"}), 400
                cardio_data = {"type": exercise_type, "duration": duration, "distance": distance}
            elif exercise_type == "Spielsport":
                sportart = data.get("sportart")
                if sportart is None:
                    return jsonify({"error": "Sport type missing"}), 400
                cardio_data = {"type": exercise_type, "duration": duration, "distance": 0, "sportart": sportart}
                exercise_name = sportart
            else:
                return jsonify({"error": "Invalid workout type"}), 400
            
            xp_gained = calculate_xp_and_endurance(session["user_id"], cardio_data, "add")

            new_workout = Workout(
                user_id=session["user_id"],
                exercise=exercise_name,
                type='cardio',
                date=workout_date,
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
            
        elif workout_type == "calistenics":
            # Verarbeitung für Calisthenics
            exercise_name = data.get("exercise_name")
            sets_data = data.get("sets")
            
            if not exercise_name or not isinstance(sets_data, list):
                return jsonify({"error": "Missing data"}), 400
            
            user_profile = UserProfile.query.filter_by(user_id=session["user_id"]).first()
            bodyweight = user_profile.bodyweight if user_profile else 70

            new_workout = Workout(
                user_id=session["user_id"],
                exercise=exercise_name,
                date=workout_date,
                type='calistenics'
            )
            db.session.add(new_workout)
            db.session.flush()

            for set_data in sets_data:
                new_set = Set(
                    workout_id=new_workout.id,
                    user_id=session["user_id"],
                    reps=set_data.get("reps"),
                    weight=bodyweight
                )
                db.session.add(new_set)
            
            xp_gained = calculate_xp_for_calestenics(session["user_id"], new_workout.sets, "add")
            user_stats = db.session.get(UserStat, session["user_id"])
            if user_stats:
                user_stats.xp_total = (user_stats.xp_total or 0) + xp_gained
            
            update_streak(session["user_id"])
            db.session.commit()
            return jsonify({"message": "Workout added successfully!", "xp_gained": xp_gained}), 200
            
        else:
            return jsonify({"error": "Invalid workout type"}), 400
            
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# --- Fitness Page (Workouts) ---
@app.route('/workout', methods=['GET', 'POST'])
def workout_page():
    if "user_id" not in session:
        return redirect(url_for("login"))


    selected_date = request.args.get('date', datetime.now(pytz.utc).date().strftime("%Y-%m-%d"))
    today = datetime.now(pytz.utc).date().strftime("%Y-%m-%d")
    ruhe = check_restday(session["user_id"], selected_date)

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
            user_id=session["user_id"], date=selected_date, type='strength'
        ).all()
        
        today_cardio_workouts_raw = Workout.query.filter_by(
            user_id=session["user_id"], date=selected_date, type='cardio'
        ).all()

        today_calistenics_workouts = Workout.query.filter_by(
            user_id=session["user_id"], date=selected_date, type='calestenics'
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

    return render_template("workouts.html", today_workouts=today_workouts, today_cardio_workouts=today_cardio_workouts,
                            today_calistenics_workouts=today_calistenics_workouts, ruhe=ruhe, today=selected_date)

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

# --- Calestenics-Workouts ---
@app.route('/cal-workouts', methods=['POST'])
def add_cal_workout():
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401
    
    data = request.get_json()
    if not data or "type" not in data or "sets" not in data:
        return jsonify({"error": "Missing data"}), 400
    
    today = datetime.now(pytz.utc).date().strftime("%Y-%m-%d")
    user_profile = UserProfile.query.filter_by(user_id=session["user_id"]).first()
    bodyweight = user_profile.bodyweight if user_profile else 70  
    exercise_name = data.get("exercise_name")
    sets_data = data.get("sets")
    if not exercise_name or not isinstance(sets_data, list):
        return jsonify({"error": "Missing data"}), 400
    
    try:
        new_workout = Workout(
            user_id=session["user_id"],
            exercise=exercise_name,
            date=today,
            type='calestenics'
        )
        db.session.add(new_workout)
        db.session.flush()

        for set_data in sets_data:
            new_set = Set(
                workout_id=new_workout.id,
                user_id=session["user_id"],
                reps=set_data.get("reps"),
                weight=bodyweight
            )
            db.session.add(new_set)
        
        xp_gained = calculate_xp_for_calestenics(session["user_id"], new_workout.sets, "add")
        user_stats = db.session.get(UserStat, session["user_id"])
        if user_stats:
            user_stats.xp_total = (user_stats.xp_total or 0) + xp_gained
        
        update_streak(session["user_id"])
        db.session.commit()
        return jsonify({"message": "Workout added successfully!", "xp_gained": xp_gained}), 200
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
            elif workout.type == "calestenics":
                xp_to_deduct = calculate_xp_for_calestenics(session["user_id"], sets, "deduct")
            
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
        selected_date = request.form.get("selected_date", datetime.now(pytz.utc).date().strftime("%Y-%m-%d"))
        
        restday_exists = Workout.query.filter_by(
            user_id=session["user_id"], date=selected_date, exercise='Restday'
        ).first() is not None

        if restday_exists:
            flash("Du hast bereits einen Ruhetag für dieses Datum eingetragen.", "error")
            return redirect(url_for("workout_page"))

        # Ruhetag-Prüfung mit der erweiterten Funktion
        if check_restday(session["user_id"], selected_date):
            new_restday = Workout(
                user_id=session["user_id"],
                exercise="Restday",
                date=selected_date,
                type='restday'
            )
            db.session.add(new_restday)
            db.session.commit()
            
            update_streak(session["user_id"])
            flash("Ruhetag eingetragen. Deine Serie wird fortgesetzt.", "success")
        else:
            # Detaillierte Fehlermeldung basierend auf der Ursache
            streak = db.session.get(UserStat, session["user_id"]).streak_days
            
            # Prüfen, ob gestern ein Ruhetag war
            selected_date_obj = datetime.strptime(selected_date, "%Y-%m-%d").date()
            yesterday = selected_date_obj - timedelta(days=1)
            yesterday_str = yesterday.strftime("%Y-%m-%d")
            
            yesterday_restday = Workout.query.filter_by(
                user_id=session["user_id"], date=yesterday_str, exercise='Restday'
            ).first() is not None
            
            if streak < 2:
                flash("Ein Ruhetag ist nur nach mindestens 2 aufeinanderfolgenden Trainingstagen möglich.", "error")
            elif yesterday_restday:
                flash("Du kannst nicht zwei Tage hintereinander einen Ruhetag einlegen.", "error")
            else:
                flash("Ein Ruhetag ist für dieses Datum nicht verfügbar.", "error")

        return redirect(url_for("workout_page"))

    except Exception as e:
        db.session.rollback()
        flash(f"Fehler beim Eintragen des Ruhetags: {str(e)}", "error")
        return redirect(url_for("workout_page"))

# --- Fitness Calendar ---
# In der fitness_kalendar Route:
@app.route('/fitness-kalendar')
def fitness_kalendar():
    if "user_id" not in session:
        return redirect(url_for("login"))

    try:
        workouts = Workout.query.filter_by(user_id=session["user_id"]).order_by(Workout.date.desc()).all()
        
        # Alle Daten des Benutzers abrufen, um Ruhetage zu identifizieren
        all_dates = set()
        workout_dates = set()
        
        # Mindest- und Maximum-Datum bestimmen (letzte 30 Tage)
        today = datetime.now(pytz.utc).date()
        thirty_days_ago = today - timedelta(days=30)
        
        # Alle Workout-Daten sammeln
        for workout in workouts:
            workout_date = datetime.strptime(workout.date, "%Y-%m-%d").date()
            if thirty_days_ago <= workout_date <= today:
                workout_dates.add(workout.date)
        
        # Ruhetage identifizieren (Tage ohne Workout in den letzten 30 Tagen)
        rest_days = {}
        current_date = thirty_days_ago
        while current_date <= today:
            date_str = current_date.strftime("%Y-%m-%d")
            if date_str not in workout_dates:
                # Prüfen, ob es ein Ruhetag ist (kein Workout an diesem Tag)
                rest_day_workout = Workout.query.filter_by(
                    user_id=session["user_id"], 
                    date=date_str, 
                    exercise='Restday'
                ).first()
                
                if rest_day_workout:
                    # Als Ruhetag markieren
                    display_date = current_date.strftime("%d.%m.%Y")
                    rest_days[display_date] = [{
                        "id": rest_day_workout.id,
                        "exercise": "Ruhetag",
                        "type": "restday"
                    }]
            
            current_date += timedelta(days=1)
            
    except Exception as e:
        flash(f"An error occurred: {e}", "error")
        workouts = []
        rest_days = {}

    grouped_workouts = defaultdict(list)
    for workout_item in workouts:
        workout_date = datetime.strptime(workout_item.date, "%Y-%m-%d")
        display_date = workout_date.strftime("%d.%m.%Y")
        
        # Nur Workouts der letzten 30 Tage anzeigen
        if thirty_days_ago <= workout_date.date() <= today:
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
                    workout_data["duration"] = 0
                    workout_data["distance"] = 0
            elif workout_item.type == "calestenics":  
                workout_data["sets"] = [
                    {"reps": s.reps, "weight": s.weight} for s in workout_item.sets
                ]
                workout_data["bodyweight"] = workout_item.sets[0].weight if workout_item.sets else 0
            else:
                workout_data["sets"] = [
                    {"reps": s.reps, "weight": s.weight} for s in workout_item.sets
                ]
                
            grouped_workouts[display_date].append(workout_data)
    
    # Ruhetage zu den Workouts hinzufügen
    for date, rest_workouts in rest_days.items():
        if date in grouped_workouts:
            grouped_workouts[date].extend(rest_workouts)
        else:
            grouped_workouts[date] = rest_workouts
    
    sorted_workouts = sorted(grouped_workouts.items(), key=lambda item: datetime.strptime(item[0], "%d.%m.%Y"), reverse=True)

    return render_template("fitness-kalendar.html", workouts=dict(sorted_workouts))

@app.route("/shop")
def shop():
    if "user_id" not in session:
        return redirect(url_for('login'))

    return render_template("shop.html")

# --- Start App & Prepare DB ---
if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(debug=True)