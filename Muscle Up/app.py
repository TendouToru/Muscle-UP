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

# Github Configuration (für Backups)
app.config['GITHUB_TOKEN'] = os.environ.get('GITHUB_TOKEN')
app.config['GITHUB_REPO'] = os.environ.get('GITHUB_REPO', 'TendouToru/Muscle-UP')
app.config['GITHUB_BRANCH'] = os.environ.get('GITHUB_BRANCH', 'main')

# --- SQLALCHEMY Database Classes ---
class User(db.Model):
    __tablename__ = 'users'
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
    coins = db.Column(db.Integer, default=0)  # Neu: Virtuelle Währung für Shop
    user = db.relationship('User', back_populates='stats')

class Workout(db.Model):
    __tablename__ = 'workouts'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'))
    exercise = db.Column(db.Text)
    date = db.Column(db.Text)
    type = db.Column(db.Text)  # 'strength', 'cardio', 'calisthenics', 'restday'

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

class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'))
    title = db.Column(db.Text, nullable=False)
    content = db.Column(db.Text, nullable=False)
    type = db.Column(db.Text, default='patchnote')
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Patchnote(db.Model):
    __tablename__ = 'patchnotes'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.Text, nullable=False)
    content = db.Column(db.Text, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class ShopItem(db.Model):
    __tablename__ = 'shop_items'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text)
    price = db.Column(db.Integer, nullable=False)
    effect = db.Column(db.Text)  # z.B. 'xp_boost_50'

# Admin Setup
class MyAdminIndexView(AdminIndexView):
    def is_accessible(self):
        return session.get('is_admin', False)

admin = Admin(app, index_view=MyAdminIndexView())
admin.add_view(ModelView(User, db.session))
admin.add_view(ModelView(UserProfile, db.session))
admin.add_view(ModelView(UserStat, db.session))
admin.add_view(ModelView(Workout, db.session))
admin.add_view(ModelView(Set, db.session))
admin.add_view(ModelView(Notification, db.session))
admin.add_view(ModelView(Patchnote, db.session))
admin.add_view(ModelView(ShopItem, db.session))

# Helper Functions
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def calculate_level(xp):
    level = 1
    while xp >= math.pow(level, 2) * 100:
        xp -= math.pow(level, 2) * 100
        level += 1
    return level, xp, math.pow(level, 2) * 100  # level, remaining_xp, xp_for_next

def get_rank_name(level):
    if level <= 5: return "Anfänger"
    elif level <= 10: return "Gesund"
    elif level <= 15: return "Sportlich"
    elif level <= 20: return "Fit"
    elif level <= 25: return "Sportler"
    elif level <= 30: return "Top-Fit"
    elif level <= 35: return "Athlet"
    elif level <= 40: return "Super-Athlet"
    elif level <= 45: return "Leistungssportler"
    else: return "Sport ist Leben"

def update_streak(user_id, date, is_restday=False):
    user_stats = UserStat.query.filter_by(user_id=user_id).first()
    today = datetime.now(pytz.utc).date()
    yesterday = today - timedelta(days=1)
    day_before = today - timedelta(days=2)
    yesterday_date = yesterday.strftime("%Y-%m-%d")
    day_before_date = day_before.strftime("%Y-%m-%d")

    yesterday_workout = Workout.query.filter_by(user_id=user_id, date=yesterday_date).first()
    day_before_workout = Workout.query.filter_by(user_id=user_id, date=day_before_date).first()
    yesterday_restday = yesterday_workout and yesterday_workout.exercise == 'Restday'

    if yesterday_workout and not yesterday_restday:
        user_stats.streak_days += 1
    elif is_restday and day_before_workout and yesterday_workout and not yesterday_restday:
        pass  # Restday erlaubt, Streak behalten
    else:
        user_stats.streak_days = 1 if not is_restday else 0
    db.session.commit()

def calculate_xp(workout, sets):
    xp = 0
    bonus = 0 if workout.user.stats.streak_days < 3 else workout.user.stats.streak_days * 10
    if workout.type == 'strength':
        volume = sum(s.weight * s.reps for s in sets)
        xp = volume / 10 + bonus
        workout.user.stats.attr_strength += 1
    elif workout.type == 'cardio':
        duration = sets[0].reps if sets else 0
        distance = sets[0].weight if sets else 0
        xp = (duration * 2 + distance * 10) + bonus
        workout.user.stats.attr_endurance += 1
    elif workout.type == 'calisthenics':
        reps_total = sum(s.reps for s in sets)
        xp = reps_total * 1.5 + bonus
        workout.user.stats.attr_endurance += 1
    elif workout.type == 'restday':
        xp = 0
    workout.user.stats.xp_total += int(xp)
    workout.user.stats.coins += 10  # +10 Coins pro Workout
    old_level, _, _ = calculate_level(workout.user.stats.xp_total - int(xp))
    new_level, _, _ = calculate_level(workout.user.stats.xp_total)
    if new_level > old_level:
        notif = Notification(user_id=workout.user_id, title="Level Up!", content=f"Du bist jetzt Level {new_level}!")
        db.session.add(notif)
    db.session.commit()
    return int(xp)

def init_db():
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        admin_user = User(username='admin', password=hash_password('admin'), is_admin=True)
        db.session.add(admin_user)
        db.session.add(UserProfile(user_id=admin_user.id))
        db.session.add(UserStat(user_id=admin_user.id))
        db.session.commit()
    if not ShopItem.query.first():
        items = [
            ShopItem(name="XP Boost", description="+50 XP", price=100, effect="xp_boost_50"),
            ShopItem(name="Streak Protector", description="Schützt Streak 1x", price=200, effect="streak_protect")
        ]
        db.session.bulk_save_objects(items)
        db.session.commit()

# --- Routes ---
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    leaderboard = User.query.join(UserStat).order_by(UserStat.xp_total.desc()).limit(10).all()
    notifications = Notification.query.filter_by(user_id=session['user_id'], is_read=False).count()
    return render_template('index.html', leaderboard=leaderboard, notifications=notifications)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = hash_password(request.form['password'])
        user = User.query.filter_by(username=username, password=password).first()
        if user:
            session['user_id'] = user.id
            session['username'] = user.username
            session['is_admin'] = user.is_admin
            return redirect(url_for('index'))
        flash('Falsche Anmeldedaten', 'error')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        confirm = request.form['confirm']
        if password != confirm:
            flash('Passwörter stimmen nicht überein', 'error')
            return render_template('register.html')
        if User.query.filter_by(username=username).first():
            flash('Benutzername vergeben', 'error')
            return render_template('register.html')
        hashed = hash_password(password)
        user = User(username=username, password=hashed)
        db.session.add(user)
        db.session.commit()
        db.session.add(UserProfile(user_id=user.id))
        db.session.add(UserStat(user_id=user.id))
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/profile')
def profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    profile = user.profile
    stats = user.stats
    level, xp_remaining, xp_for_next = calculate_level(stats.xp_total)
    rank_name = get_rank_name(level)
    progress = xp_remaining / xp_for_next
    kraft = stats.attr_strength
    today = datetime.now(pytz.utc).date()
    yesterday = today - timedelta(days=1)
    day_before = today - timedelta(days=2)
    yesterday_workout = Workout.query.filter_by(user_id=session['user_id'], date=yesterday.strftime("%Y-%m-%d")).first()
    day_before_workout = Workout.query.filter_by(user_id=session['user_id'], date=day_before.strftime("%Y-%m-%d")).first()
    ruhe = day_before_workout and yesterday_workout and not (yesterday_workout and yesterday_workout.exercise == 'Restday')
    return render_template('profile.html', profile=profile, stats=stats, level=level, rank_name=rank_name,
                           xp_remaining=xp_remaining, xp_for_next=xp_for_next, progress=progress, kraft=kraft, ruhe=ruhe)

@app.route('/user/<username>')
def user_profile(username):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    target_user = User.query.filter_by(username=username).first()
    if not target_user:
        flash('User nicht gefunden', 'error')
        return redirect(url_for('index'))
    profile = target_user.profile
    stats = target_user.stats
    level, xp_remaining, xp_for_next = calculate_level(stats.xp_total)
    rank_name = get_rank_name(level)
    kraft = stats.attr_strength
    recent_workouts = Workout.query.filter_by(user_id=target_user.id).order_by(Workout.date.desc()).limit(5).all()
    for w in recent_workouts:
        w.sets_count = len(w.sets)
        if w.type == 'cardio' and w.sets:
            w.duration = w.sets[0].reps
            w.distance = w.sets[0].weight
    return render_template('user_profile.html', target_user=target_user, profile=profile, stats=stats, level=level,
                           rank_name=rank_name, xp_remaining=xp_remaining, xp_for_next=xp_for_next, kraft=kraft,
                           recent_workouts=recent_workouts)

@app.route('/update_profile', methods=['POST'])
def update_profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    profile = UserProfile.query.filter_by(user_id=session['user_id']).first()
    profile.name = request.form.get('name')
    profile.gender = request.form.get('gender')
    profile.age = int(request.form.get('age', 0))
    profile.bodyweight = float(request.form.get('bodyweight', 0))
    profile.height = float(request.form.get('height', 0))
    profile.region = request.form.get('region')
    if 'profile_pic' in request.files:
        file = request.files['profile_pic']
        if file:
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            profile.profile_pic = filename
    db.session.commit()
    flash('Profil aktualisiert', 'success')
    return redirect(url_for('profile'))

@app.route('/workout_page')
def workout_page():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    today = datetime.now(pytz.utc).date()
    yesterday = today - timedelta(days=1)
    day_before = today - timedelta(days=2)
    yesterday_workout = Workout.query.filter_by(user_id=session['user_id'], date=yesterday.strftime("%Y-%m-%d")).first()
    day_before_workout = Workout.query.filter_by(user_id=session['user_id'], date=day_before.strftime("%Y-%m-%d")).first()
    ruhe = day_before_workout and yesterday_workout and not (yesterday_workout and yesterday_workout.exercise == 'Restday')
    today_workouts = Workout.query.filter_by(user_id=session['user_id'], date=today.strftime("%Y-%m-%d"), type='strength').all()
    today_cardio_workouts = Workout.query.filter_by(user_id=session['user_id'], date=today.strftime("%Y-%m-%d"), type='cardio').all()
    today_calistenics_workouts = Workout.query.filter_by(user_id=session['user_id'], date=today.strftime("%Y-%m-%d"), type='calisthenics').all()
    return render_template('workouts.html', ruhe=ruhe, today_workouts=today_workouts, today_cardio_workouts=today_cardio_workouts, today_calistenics_workouts=today_calistenics_workouts)

@app.route('/add_workout', methods=['POST'])
def add_workout():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    try:
        exercise = request.form['exercise']
        date = request.form['date']
        w_type = request.form['type']
        if not exercise or not date or not w_type:
            flash('Alle Felder ausfüllen', 'error')
            return redirect(url_for('workout_page'))
        if Workout.query.filter_by(user_id=session['user_id'], date=date, exercise=exercise).first():
            flash('Workout bereits eingetragen', 'error')
            return redirect(url_for('workout_page'))
        workout = Workout(user_id=session['user_id'], exercise=exercise, date=date, type=w_type)
        db.session.add(workout)
        db.session.commit()
        sets_data = json.loads(request.form.get('sets', '[]'))
        for s in sets_data:
            new_set = Set(workout_id=workout.id, user_id=session['user_id'], reps=s['reps'], weight=s['weight'])
            db.session.add(new_set)
        db.session.commit()
        xp = calculate_xp(workout, workout.sets)
        update_streak(session['user_id'], date)
        flash(f'Workout hinzugefügt! +{xp} XP', 'success')
    except Exception as e:
        db.session.rollback()
        flash(str(e), 'error')
    return redirect(url_for('workout_page'))

@app.route('/add_restday', methods=['POST'])
def add_restday():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    try:
        date = request.form['date']
        if Workout.query.filter_by(user_id=session['user_id'], date=date).first():
            flash('Datum hat bereits ein Workout', 'error')
            return redirect(url_for('workout_page'))
        today = datetime.now(pytz.utc).date()
        yesterday = today - timedelta(days=1)
        day_before = today - timedelta(days=2)
        yesterday_date = yesterday.strftime("%Y-%m-%d")
        day_before_date = day_before.strftime("%Y-%m-%d")
        yesterday_workout = Workout.query.filter_by(user_id=session['user_id'], date=yesterday_date).first()
        day_before_workout = Workout.query.filter_by(user_id=session['user_id'], date=day_before_date).first()
        yesterday_restday = yesterday_workout and yesterday_workout.exercise == 'Restday'
        if day_before_workout and yesterday_workout and not yesterday_restday:
            workout = Workout(user_id=session['user_id'], exercise='Restday', date=date, type='restday')
            db.session.add(workout)
            db.session.commit()
            update_streak(session['user_id'], date, is_restday=True)
            flash('Ruhetag eingetragen', 'success')
        elif not day_before_workout or not yesterday_workout:
            flash('Ruhetag nur nach mindestens 2 Trainings möglich', 'error')
        elif yesterday_restday:
            flash('Keine zwei Ruhetage in Folge', 'error')
        else:
            flash('Ruhetag nicht verfügbar', 'error')
    except Exception as e:
        db.session.rollback()
        flash(str(e), 'error')
    return redirect(url_for('workout_page'))

@app.route('/delete_workout/<int:workout_id>')
def delete_workout(workout_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    workout = Workout.query.get(workout_id)
    if workout and workout.user_id == session['user_id']:
        db.session.delete(workout)
        db.session.commit()
        flash('Workout gelöscht', 'success')
    return redirect(url_for('workout_page'))

@app.route('/fitness-kalendar')
def fitness_kalendar():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    try:
        workouts = Workout.query.filter_by(user_id=session['user_id']).order_by(Workout.date.desc()).all()
        all_dates = set()
        workout_dates = set()
        today = datetime.now(pytz.utc).date()
        thirty_days_ago = today - timedelta(days=30)
        for workout in workouts:
            workout_date = datetime.strptime(workout.date, "%Y-%m-%d").date()
            if thirty_days_ago <= workout_date <= today:
                workout_dates.add(workout.date)
        rest_days = {}
        current_date = thirty_days_ago
        while current_date <= today:
            date_str = current_date.strftime("%Y-%m-%d")
            if date_str not in workout_dates:
                rest_day_workout = Workout.query.filter_by(user_id=session['user_id'], date=date_str, exercise='Restday').first()
                if rest_day_workout:
                    display_date = current_date.strftime("%d.%m.%Y")
                    rest_days[display_date] = [{"id": rest_day_workout.id, "exercise": "Ruhetag", "type": "restday"}]
            current_date += timedelta(days=1)
    except Exception as e:
        flash(str(e), 'error')
        workouts = []
        rest_days = {}
    grouped_workouts = defaultdict(list)
    for workout_item in workouts:
        workout_date = datetime.strptime(workout_item.date, "%Y-%m-%d")
        display_date = workout_date.strftime("%d.%m.%Y")
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
            elif workout_item.type == "calisthenics":
                workout_data["sets"] = [{"reps": s.reps, "weight": s.weight} for s in workout_item.sets]
                workout_data["bodyweight"] = workout_item.sets[0].weight if workout_item.sets else 0
            else:
                workout_data["sets"] = [{"reps": s.reps, "weight": s.weight} for s in workout_item.sets]
            grouped_workouts[display_date].append(workout_data)
    for date, rest_workouts in rest_days.items():
        if date in grouped_workouts:
            grouped_workouts[date].extend(rest_workouts)
        else:
            grouped_workouts[date] = rest_workouts
    sorted_workouts = sorted(grouped_workouts.items(), key=lambda item: datetime.strptime(item[0], "%d.%m.%Y"), reverse=True)
    return render_template("fitness-kalendar.html", workouts=dict(sorted_workouts))

@app.route('/shop')
def shop():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    items = ShopItem.query.all()
    user_stats = UserStat.query.filter_by(user_id=session['user_id']).first()
    return render_template('shop.html', items=items, coins=user_stats.coins)

@app.route('/buy_item/<int:item_id>')
def buy_item(item_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    item = ShopItem.query.get(item_id)
    stats = UserStat.query.filter_by(user_id=session['user_id']).first()
    if item and stats.coins >= item.price:
        stats.coins -= item.price
        if item.effect == 'xp_boost_50':
            stats.xp_total += 50
        elif item.effect == 'streak_protect':
            pass  # Implementiere Streak-Schutz
        db.session.commit()
        flash(f'{item.name} gekauft!', 'success')
    else:
        flash('Nicht genug Coins', 'error')
    return redirect(url_for('shop'))

@app.route('/info')
def info():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    patchnotes = Patchnote.query.order_by(Patchnote.created_at.desc()).all()
    admin = session.get('is_admin', False)
    return render_template('info.html', patchnotes=patchnotes, admin=admin)

@app.route('/add_patchnote', methods=['POST'])
def add_patchnote():
    if 'user_id' not in session or not session['is_admin']:
        return redirect(url_for('login'))
    title = request.form['title']
    content = request.form['content']
    patch = Patchnote(title=title, content=content, user_id=session['user_id'])
    db.session.add(patch)
    db.session.commit()
    users = User.query.all()
    for u in users:
        notif = Notification(user_id=u.id, title=title, content=content)
        db.session.add(notif)
    db.session.commit()
    flash('Patchnote veröffentlicht', 'success')
    return redirect(url_for('info'))

@app.route('/backup_db')
def backup_db():
    if not session.get('is_admin'):
        return 'Unauthorized'
    g = Github(app.config['GITHUB_TOKEN'])
    repo = g.get_repo(app.config['GITHUB_REPO'])
    with open('test.db', 'rb') as f:
        content = f.read()
    repo.create_file("backup.db", "DB Backup", content, branch=app.config['GITHUB_BRANCH'])
    return 'Backup successful'

# Jinja Filters
@app.template_filter('xpformat')
def xpformat(value):
    return f"{value:,} XP"

@app.template_filter('dateformat')
def dateformat(value):
    return datetime.strptime(value, "%Y-%m-%d").strftime("%d.%m.%Y")

if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(debug=True)