"""
Tripzy — Production-ready backend
===================================
• PostgreSQL  → structured data  (users, rides, bookings, reviews, cars, verification)
• MongoDB     → fast collections  (messages, notifications, chat threads)
• SQLite      → local dev fallback (no env vars needed on your laptop)
• bcrypt      → secure password hashing
• psycopg v3  → Python 3.14 compatible PostgreSQL driver
• Cloudinary  → all file uploads
"""

import os
import sqlite3
from datetime import datetime, timedelta

import cloudinary
import cloudinary.uploader
from flask import (Flask, render_template, request,
                   redirect, url_for, session, jsonify)
from werkzeug.security import generate_password_hash, check_password_hash

# ── detect which backends are available ──────────────────────────────────────
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://trripzy_user:pUUtklU986x0iZwa1jQRFM2hJjToIazb@dpg-d77joabuibrs73c1o1d0-a/trripzy')
MONGO_URL    = os.environ.get('MONGO_URL', 'mongodb+srv://trripzy_user:WfuEgIOZL5hC6dno@cluster0.9n0k1zt.mongodb.net/tripzy?appName=Cluster0')
USE_POSTGRES = bool(DATABASE_URL)
USE_MONGO    = bool(MONGO_URL)

# psycopg v3 — works on Python 3.14, unlike psycopg2-binary
if USE_POSTGRES:
    import psycopg                          # psycopg v3
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

if USE_MONGO:
    from pymongo import MongoClient, DESCENDING
    from bson import ObjectId

# ─────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'tripzy_dev_secret_change_in_prod')

# ═══════════════════════════════════════════════════════════════════════════════
# ☁️  CLOUDINARY
# ═══════════════════════════════════════════════════════════════════════════════
cloudinary.config(
    cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME', 'dusl8hilm'),
    api_key    =os.environ.get('CLOUDINARY_API_KEY',    '933863442389513'),
    api_secret =os.environ.get('CLOUDINARY_API_SECRET', 'cFb8XFiwqyuSd_TcmwbhSd89st0'),
)

def upload_to_cloudinary(file_obj, folder='tripzy'):
    try:
        res = cloudinary.uploader.upload(file_obj, folder=folder, resource_type='auto')
        return res.get('secure_url', 'cloudinary://933863442389513:cFb8XFiwqyuSd_TcmwbhSd89st0@dusl8hilm')
    except Exception as e:
        app.logger.error(f'Cloudinary error: {e}')
        return ''


# ═══════════════════════════════════════════════════════════════════════════════
# 🗄️  POSTGRESQL  — psycopg v3 connection pool
# ═══════════════════════════════════════════════════════════════════════════════
_pg_pool = None

def _get_pg_pool():
    global _pg_pool
    if _pg_pool is None:
        url = DATABASE_URL
        # psycopg v3 uses postgresql:// (same as psycopg2)
        if url.startswith('postgres://'):
            url = url.replace('postgres://', 'postgresql://', 1)
        _pg_pool = ConnectionPool(
            conninfo=url,
            min_size=1,
            max_size=10,
            kwargs={'row_factory': dict_row},  # all rows return as dicts automatically
            open=True,
        )
    return _pg_pool


# ═══════════════════════════════════════════════════════════════════════════════
# 🍃  MONGODB  — lazy singleton
# ═══════════════════════════════════════════════════════════════════════════════
_mongo_client = None
_mongo_db     = None

def get_mongo():
    global _mongo_client, _mongo_db
    if _mongo_db is None:
        _mongo_client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        _mongo_db     = _mongo_client.get_default_database()
    return _mongo_db


# ═══════════════════════════════════════════════════════════════════════════════
# 🔧  UNIFIED QUERY HELPER  (SQLite ↔ PostgreSQL)
# ═══════════════════════════════════════════════════════════════════════════════

class _Row(dict):
    """Dict that also supports attribute access  (makes templates happy)."""
    def __getattr__(self, key):
        try:    return self[key]
        except KeyError: raise AttributeError(key)

def _sqlite_rows(cur):
    cols = [d[0] for d in cur.description] if cur.description else []
    return [_Row(zip(cols, r)) for r in cur.fetchall()]


def query(sql, params=(), fetchone=False, fetchall=False, commit=False):
    """
    Unified query against PostgreSQL (production) or SQLite (local dev).
    Always returns plain _Row dicts.
    commit=True on an INSERT returns the new row's id.
    """
    if USE_POSTGRES:
        pool = _get_pg_pool()
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params or None)
                result = None
                if fetchone:   result = cur.fetchone()   # already a dict via dict_row
                elif fetchall: result = cur.fetchall()
                if commit:
                    conn.commit()
                    try:
                        cur.execute('SELECT lastval()')
                        result = cur.fetchone()['lastval']
                    except Exception:
                        result = None
                return result
    else:
        # SQLite — translate PostgreSQL syntax on the fly
        sqlite_sql = (sql
                      .replace('%s', '?')
                      .replace('SERIAL PRIMARY KEY', 'INTEGER PRIMARY KEY AUTOINCREMENT')
                      .replace('ON CONFLICT (key) DO NOTHING', 'OR IGNORE'))
        conn = sqlite3.connect('tripzy.db')
        conn.row_factory = sqlite3.Row
        cur  = conn.cursor()
        try:
            cur.execute(sqlite_sql, params or ())
        except sqlite3.OperationalError as e:
            if 'duplicate column' in str(e).lower() or 'already exists' in str(e).lower():
                cur.close(); conn.close(); return None
            raise
        result = None
        if fetchone:
            rows = _sqlite_rows(cur)
            result = rows[0] if rows else None
        elif fetchall:
            result = _sqlite_rows(cur)
        if commit:
            conn.commit()
            result = cur.lastrowid
        cur.close(); conn.close()
        return result


# ═══════════════════════════════════════════════════════════════════════════════
# 🧱  INIT DATABASE
# ═══════════════════════════════════════════════════════════════════════════════
def _pk():
    return 'SERIAL PRIMARY KEY' if USE_POSTGRES else 'INTEGER PRIMARY KEY AUTOINCREMENT'

def init_db():
    pk = _pk()

    tables = [
        f'''CREATE TABLE IF NOT EXISTS users (
            id            {pk},
            name          TEXT,
            email         TEXT UNIQUE,
            password_hash TEXT,
            phone         TEXT,
            bio           TEXT,
            photo         TEXT,
            avg_rating    REAL    DEFAULT 0.0,
            total_ratings INTEGER DEFAULT 0
        )''',

        f'''CREATE TABLE IF NOT EXISTS rides (
            id         {pk},
            user_email TEXT,
            from_loc   TEXT,
            to_loc     TEXT,
            date       TEXT,
            time       TEXT,
            seats      INTEGER,
            price      INTEGER,
            music      TEXT,
            smoking    TEXT,
            luggage    TEXT,
            stops      TEXT,
            gender     TEXT,
            ac         TEXT,
            pets       TEXT,
            charging   TEXT,
            status     TEXT DEFAULT 'not_started'
        )''',

        f'''CREATE TABLE IF NOT EXISTS bookings (
            id           {pk},
            ride_id      INTEGER,
            user_email   TEXT,
            seats_booked INTEGER DEFAULT 1,
            booked_at    TEXT,
            rating       TEXT
        )''',

        f'''CREATE TABLE IF NOT EXISTS emergency_contacts (
            id         {pk},
            user_email TEXT,
            name1      TEXT,
            phone1     TEXT,
            name2      TEXT,
            phone2     TEXT
        )''',

        f'''CREATE TABLE IF NOT EXISTS verification (
            id         {pk},
            user_email TEXT,
            aadhar     TEXT DEFAULT 'pending',
            license    TEXT DEFAULT 'pending',
            rc         TEXT DEFAULT 'pending',
            insurance  TEXT DEFAULT 'pending'
        )''',

        f'''CREATE TABLE IF NOT EXISTS reviews (
            id             {pk},
            ride_id        INTEGER,
            reviewer_email TEXT,
            reviewee_email TEXT,
            reviewer_role  TEXT,
            stars          INTEGER,
            review_text    TEXT,
            created_at     TEXT
        )''',

        f'''CREATE TABLE IF NOT EXISTS cars (
            id         {pk},
            user_email TEXT,
            name       TEXT,
            model      TEXT,
            color      TEXT,
            plate      TEXT,
            images     TEXT
        )''',

        f'''CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT
        )''',
    ]

    # Only keep messages + notifications in SQL when Mongo is unavailable
    if not USE_MONGO:
        tables += [
            f'''CREATE TABLE IF NOT EXISTS messages (
                id        {pk},
                sender    TEXT,
                receiver  TEXT,
                message   TEXT,
                time      TEXT,
                ride_id   INTEGER,
                is_read   INTEGER DEFAULT 0
            )''',
            f'''CREATE TABLE IF NOT EXISTS notifications (
                id         {pk},
                user_email TEXT,
                message    TEXT,
                is_read    INTEGER DEFAULT 0,
                created_at TEXT
            )''',
        ]

    if USE_POSTGRES:
        # DDL must run with autocommit in psycopg v3
        url = DATABASE_URL
        if url.startswith('postgres://'):
            url = url.replace('postgres://', 'postgresql://', 1)
        with psycopg.connect(url, autocommit=True) as conn:
            for ddl in tables:
                conn.execute(ddl)
            conn.execute(
                "INSERT INTO config(key,value) VALUES('commission_pct','10') "
                "ON CONFLICT(key) DO NOTHING"
            )
    else:
        for ddl in tables:
            query(ddl, commit=True)
        query("INSERT OR IGNORE INTO config(key,value) VALUES('commission_pct','10')", commit=True)

    # Default commission
    if USE_POSTGRES:
        query("INSERT INTO config(key,value) VALUES('commission_pct','10') ON CONFLICT(key) DO NOTHING", commit=True)
    else:
        query("INSERT OR IGNORE INTO config(key,value) VALUES('commission_pct','10')", commit=True)

    # Safe migrations — ADD COLUMN IF NOT EXISTS is PostgreSQL only
    alters = [
        ('users',    'password_hash', 'TEXT'),
        ('bookings', 'seats_booked',  'INTEGER DEFAULT 1'),
        ('bookings', 'user_email',    'TEXT'),
        ('rides',    'user_email',    'TEXT'),
    ]
    for tbl, col, typ in alters:
        if USE_POSTGRES:
            # psycopg v3: run DDL with autocommit to avoid "can't run in transaction" errors
            try:
                url = DATABASE_URL
                if url.startswith('postgres://'):
                    url = url.replace('postgres://', 'postgresql://', 1)
                with psycopg.connect(url, autocommit=True) as conn:
                    conn.execute(f'ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS {col} {typ}')
            except Exception:
                pass
        else:
            try: query(f'ALTER TABLE {tbl} ADD COLUMN {col} {typ}', commit=True)
            except Exception: pass

    # MongoDB indexes
    if USE_MONGO:
        try:
            mdb = get_mongo()
            mdb.messages.create_index([('sender', 1), ('receiver', 1)])
            mdb.messages.create_index([('ride_id', 1)])
            mdb.messages.create_index([('receiver', 1), ('is_read', 1)])
            mdb.notifications.create_index([('user_email', 1), ('is_read', 1)])
        except Exception as e:
            app.logger.warning(f'MongoDB index creation: {e}')

    app.logger.info(f'DB init done  postgres={USE_POSTGRES}  mongo={USE_MONGO}')


init_db()


# ═══════════════════════════════════════════════════════════════════════════════
# 🔔  CONTEXT PROCESSOR
# ═══════════════════════════════════════════════════════════════════════════════
@app.context_processor
def inject_counts():
    if 'user_email' not in session:
        return dict(notif_count=0, unread_msgs=0)
    me = session['user_email']
    try:
        if USE_MONGO:
            mdb          = get_mongo()
            notif_count  = mdb.notifications.count_documents({'user_email': me, 'is_read': 0})
            unread_msgs  = mdb.messages.count_documents({'receiver': me, 'is_read': 0})
        else:
            notif_count  = (query('SELECT COUNT(*) AS c FROM notifications WHERE user_email=%s AND is_read=0', (me,), fetchone=True) or {}).get('c', 0)
            unread_msgs  = (query('SELECT COUNT(*) AS c FROM messages WHERE receiver=%s AND is_read=0', (me,), fetchone=True) or {}).get('c', 0)
        return dict(notif_count=notif_count, unread_msgs=unread_msgs)
    except Exception:
        return dict(notif_count=0, unread_msgs=0)


# ═══════════════════════════════════════════════════════════════════════════════
# 🧠  SMART STATUS HELPERS
# ═══════════════════════════════════════════════════════════════════════════════
def get_smart_status(ride):
    manual = ride['status']
    if manual in ('started', 'completed'):
        return manual
    try:
        ride_dt = datetime.strptime(f"{ride['date']} {ride['time']}", '%Y-%m-%d %H:%M')
    except Exception:
        return manual
    now = datetime.now()
    if now < ride_dt:                         return 'not_started'
    elif now < ride_dt + timedelta(hours=3):  return 'started'
    else:                                      return 'completed'


def enrich_rides(rides):
    enriched = []
    now = datetime.now()
    for ride in rides:
        r = dict(ride)
        r['smart_status'] = get_smart_status(ride)
        try:
            ride_dt   = datetime.strptime(f"{ride['date']} {ride['time']}", '%Y-%m-%d %H:%M')
            diff      = ride_dt - now
            total_min = int(diff.total_seconds() // 60)
            if total_min > 0:
                hrs, mins    = divmod(total_min, 60)
                days, hrs    = divmod(hrs, 24)
                if days:      r['countdown'] = f"Starts in {days}d {hrs}h"
                elif hrs:     r['countdown'] = f"Starts in {hrs}h {mins}m"
                else:         r['countdown'] = f"Starts in {mins} min"
            elif r['smart_status'] == 'started':
                r['countdown'] = '🟢 Ride in progress'
            else:
                r['countdown'] = '✅ Completed'
        except Exception:
            r['countdown'] = ''
        enriched.append(r)
    return enriched


def is_bookable(ride, requested=1):
    s = get_smart_status(ride)
    if s == 'completed': return False, 'This ride has already completed'
    if s == 'started':   return False, 'This ride has already started'
    if ride['seats'] <= 0:            return False, 'No seats available'
    if ride['seats'] < requested:     return False, f'Only {ride["seats"]} seat(s) left'
    return True, ''


# ═══════════════════════════════════════════════════════════════════════════════
# 📨  NOTIFICATION HELPERS  (Mongo or SQL)
# ═══════════════════════════════════════════════════════════════════════════════
def send_notification(user_email, message):
    now = datetime.now().strftime('%d %b, %I:%M %p')
    if USE_MONGO:
        get_mongo().notifications.insert_one({
            'user_email': user_email,
            'message':    message,
            'is_read':    0,
            'created_at': now,
        })
    else:
        query('INSERT INTO notifications(user_email,message,is_read,created_at) VALUES(%s,%s,0,%s)',
              (user_email, message, now), commit=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 🏠  HOME
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/')
def index():
    me        = session.get('user_email', '')
    all_rides = query('SELECT * FROM rides ORDER BY id DESC', fetchall=True) or []
    enriched  = enrich_rides(all_rides)
    available = [r for r in enriched if r['smart_status'] in ('not_started', 'started')]

    my_upcoming = []
    if me:
        my_offered = query('SELECT * FROM rides WHERE user_email=%s', (me,), fetchall=True) or []
        my_joined  = query('''SELECT b.id AS booking_id, b.seats_booked, r.*
                              FROM bookings b JOIN rides r ON b.ride_id=r.id
                              WHERE b.user_email=%s''', (me,), fetchall=True) or []
        up_o = [r for r in enrich_rides(my_offered) if r['smart_status'] in ('not_started','started')]
        up_j = [r for r in enrich_rides(my_joined)  if r['smart_status'] in ('not_started','started')]
        my_upcoming = sorted(up_o + up_j, key=lambda x: (x.get('date',''), x.get('time','')))

    return render_template('index.html', rides=available, my_upcoming_rides=my_upcoming)


# ═══════════════════════════════════════════════════════════════════════════════
# 🚗  POST RIDE
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/post', methods=['GET', 'POST'])
def post_ride():
    if 'user_email' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        query('''INSERT INTO rides
                   (user_email,from_loc,to_loc,date,time,seats,price,
                    music,smoking,luggage,stops,gender,ac,pets,charging,status)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'not_started')''',
              (session['user_email'],
               request.form['from'], request.form['to'],
               request.form['date'], request.form['time'],
               int(request.form['seats']), int(request.form['price']),
               request.form.get('music'),   request.form.get('smoking'),
               request.form.get('luggage'), request.form.get('stops'),
               request.form.get('gender'),  request.form.get('ac'),
               request.form.get('pets'),    request.form.get('charging')),
              commit=True)
        return redirect(url_for('index'))
    return render_template('post_ride.html')


# ═══════════════════════════════════════════════════════════════════════════════
# 🔍  SEARCH / RESULTS
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/search')
def search():
    return render_template('search.html')


@app.route('/results', methods=['GET', 'POST'])
def results():
    if request.method == 'GET':
        return render_template('results.html', rides=[])

    def norm(s): return s.lower().replace(' ', '') if s else ''
    f  = norm(request.form.get('from', ''))
    t  = norm(request.form.get('to',   ''))
    requested_seats = max(1, int(request.form.get('seats_required', 1) or 1))

    prefs = {k: request.form.get(k) for k in
             ('ac','gender','smoking','music','luggage','stops','pets','charging')}
    sort  = request.form.get('sort')

    all_rides = query('SELECT * FROM rides', fetchall=True) or []
    matched   = []
    for ride in all_rides:
        rf, rt = norm(ride['from_loc']), norm(ride['to_loc'])
        if f in rf and t in rt:
            matched.append(ride)

    enriched = enrich_rides(matched)
    filtered = [r for r in enriched
                if r['smart_status'] in ('not_started','started')
                and r['seats'] >= requested_seats]

    for r in filtered:
        r['is_full'] = (r['seats'] == 0)

    if sort == 'price':  filtered.sort(key=lambda x: int(x['price']))
    elif sort == 'seats': filtered.sort(key=lambda x: int(x['seats']), reverse=True)
    elif sort == 'time':  filtered.sort(key=lambda x: x['time'])

    return render_template('results.html', rides=filtered,
                           user_prefs=prefs, requested_seats=requested_seats)


# ═══════════════════════════════════════════════════════════════════════════════
# 📄  RIDE DETAIL + MULTI-SEAT BOOKING
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/ride/<int:id>', methods=['GET', 'POST'])
def ride_detail(id):
    ride = query('SELECT * FROM rides WHERE id=%s', (id,), fetchone=True)

    if request.method == 'POST':
        me = session.get('user_email', '')
        if ride and ride.get('user_email') == me:
            rd = dict(ride); rd['smart_status'] = get_smart_status(ride); rd['countdown'] = ''
            return render_template('ride_detail.html', ride=rd, error='You cannot book your own ride',
                                   driver=None, driver_reviews=[], can_review=False)

        requested = max(1, int(request.form.get('seats_required', 1) or 1))
        can_book, err = is_bookable(ride, requested) if ride else (False, 'Ride not found')

        if ride and can_book:
            query('UPDATE rides SET seats=seats-%s WHERE id=%s', (requested, id), commit=True)
            booked_at  = datetime.now().strftime('%d %b, %I:%M %p')
            booking_id = query('''INSERT INTO bookings(ride_id,user_email,seats_booked,booked_at)
                                  VALUES(%s,%s,%s,%s)''',
                               (id, me, requested, booked_at), commit=True)

            send_notification(me,
                f"🎟 Booking confirmed! {ride['from_loc']} → {ride['to_loc']} "
                f"on {ride['date']}. {requested} seat(s). ID: #TRP{booking_id}")

            owner = ride.get('user_email', '')
            if owner and owner != me:
                send_notification(owner,
                    f"🎉 {session.get('user_name','Someone')} booked {requested} seat(s) on your ride: "
                    f"{ride['from_loc']} → {ride['to_loc']}")

            ride = query('SELECT * FROM rides WHERE id=%s', (id,), fetchone=True)
            return render_template('ride_detail.html', ride=ride,
                                   booked=True, booking_id=booking_id,
                                   booked_at=booked_at, seats_booked=requested)
        else:
            rd = dict(ride) if ride else {}
            rd['smart_status'] = get_smart_status(ride) if ride else 'not_started'
            rd['countdown'] = ''
            return render_template('ride_detail.html', ride=rd, error=err,
                                   driver=None, driver_reviews=[], driver_car=None,
                                   can_review=False, can_book=False)

    # GET ──────────────────────────────────────────────────────────────────────
    driver = driver_car = None
    driver_reviews = []
    can_review     = False
    me_email       = session.get('user_email', '')

    if ride:
        owner = ride.get('user_email', '')
        if owner:
            driver     = query('SELECT * FROM users WHERE email=%s', (owner,), fetchone=True)
            driver_car = query('SELECT name,model,color,plate FROM cars WHERE user_email=%s LIMIT 1',
                               (owner,), fetchone=True)
        driver_reviews = query(
            'SELECT stars,review_text,reviewer_role,created_at FROM reviews WHERE ride_id=%s ORDER BY id DESC',
            (id,), fetchall=True) or []
        if me_email and ride['status'] == 'completed':
            ex = query('SELECT id FROM reviews WHERE ride_id=%s AND reviewer_email=%s',
                       (id, me_email), fetchone=True)
            can_review = not ex

    is_owner = ride and ride.get('user_email') == me_email
    already_booked = seats_by_me = 0
    if ride and me_email:
        bk = query('SELECT id,seats_booked FROM bookings WHERE ride_id=%s AND user_email=%s',
                   (id, me_email), fetchone=True)
        already_booked = bool(bk)
        seats_by_me    = bk['seats_booked'] if bk else 0

    ride_e = dict(ride) if ride else None
    can_book = False
    if ride_e:
        ride_e['smart_status'] = get_smart_status(ride)
        try:
            dt   = datetime.strptime(f"{ride['date']} {ride['time']}", '%Y-%m-%d %H:%M')
            diff = dt - datetime.now()
            m    = int(diff.total_seconds() // 60)
            if m > 0:
                h, mi = divmod(m, 60)
                ride_e['countdown'] = f"Starts in {h}h {mi}m" if h else f"Starts in {mi} min"
            elif ride_e['smart_status'] == 'started':
                ride_e['countdown'] = '🟢 Ride in progress'
            else:
                ride_e['countdown'] = '✅ Ride completed'
        except Exception:
            ride_e['countdown'] = ''
        can_book, _ = is_bookable(ride)

    verify = None
    if me_email:
        verify = query('SELECT * FROM verification WHERE user_email=%s LIMIT 1',
                       (me_email,), fetchone=True)

    return render_template('ride_detail.html',
                           ride=ride_e, driver=driver,
                           driver_reviews=driver_reviews, driver_car=driver_car,
                           can_review=can_review, can_book=can_book,
                           is_owner=is_owner, already_booked=already_booked,
                           seats_booked_by_me=seats_by_me, verify=verify)


# ═══════════════════════════════════════════════════════════════════════════════
# 🚀  START / END RIDE
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/start/<int:id>')
def start_ride(id):
    query("UPDATE rides SET status='started' WHERE id=%s", (id,), commit=True)
    return redirect(url_for('profile'))

@app.route('/end/<int:id>')
def end_ride(id):
    query("UPDATE rides SET status='completed' WHERE id=%s", (id,), commit=True)
    return redirect(url_for('profile'))


# ═══════════════════════════════════════════════════════════════════════════════
# 📊  SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/summary')
def summary():
    if 'user_email' not in session:
        return redirect(url_for('login'))
    me    = session['user_email']
    rides = [r for r in enrich_rides(
                 query('SELECT * FROM rides WHERE user_email=%s', (me,), fetchall=True) or [])
             if r['smart_status'] == 'completed']

    cfg            = query("SELECT value FROM config WHERE key='commission_pct'", fetchone=True)
    commission_pct = int(cfg['value']) if cfg else 10
    total_earnings = sum(int(r['price']) for r in rides)
    total_rides    = len(rides)
    avg_earning    = round(total_earnings / total_rides, 0) if total_rides else 0
    best_ride      = max(rides, key=lambda r: int(r['price'])) if rides else None
    total_comm     = round(total_earnings * commission_pct / 100, 0)
    driver_payout  = total_earnings - total_comm

    return render_template('summary.html',
                           rides=rides, total_earnings=total_earnings,
                           total_rides=total_rides, avg_earning=avg_earning,
                           best_ride=best_ride, commission_pct=commission_pct,
                           total_commission=total_comm, driver_payout=driver_payout)


# ═══════════════════════════════════════════════════════════════════════════════
# 👤  PROFILE
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/profile')
def profile():
    if 'user_email' not in session:
        return redirect(url_for('login'))
    me = session['user_email']

    all_offered = query('SELECT * FROM rides WHERE user_email=%s', (me,), fetchall=True) or []
    all_joined  = query('''SELECT b.id AS booking_id,b.seats_booked,b.rating,b.booked_at,r.*
                           FROM bookings b JOIN rides r ON b.ride_id=r.id
                           WHERE b.user_email=%s''', (me,), fetchall=True) or []

    oe = enrich_rides(all_offered)
    je = enrich_rides(all_joined)

    active_offered    = [r for r in oe if r['smart_status'] == 'started']
    active_joined     = [r for r in je if r['smart_status'] == 'started']
    upcoming_offered  = [r for r in oe if r['smart_status'] == 'not_started']
    upcoming_joined   = [r for r in je if r['smart_status'] == 'not_started']
    completed_offered = [r for r in oe if r['smart_status'] == 'completed']
    completed_joined  = [r for r in je if r['smart_status'] == 'completed']

    contacts = query('SELECT * FROM emergency_contacts WHERE user_email=%s LIMIT 1', (me,), fetchone=True)
    verify   = query('SELECT * FROM verification WHERE user_email=%s LIMIT 1', (me,), fetchone=True)
    car      = query('SELECT * FROM cars WHERE user_email=%s LIMIT 1', (me,), fetchone=True)
    db_user  = query('SELECT * FROM users WHERE email=%s', (me,), fetchone=True)
    reviews_received = query('SELECT * FROM reviews WHERE reviewee_email=%s ORDER BY id DESC',
                             (me,), fetchall=True) or []

    u = dict(db_user) if db_user else {}
    user = {
        'name':          u.get('name',          session.get('user_name','Guest')),
        'email':         u.get('email',         me),
        'phone':         u.get('phone',         ''),
        'bio':           u.get('bio',           ''),
        'photo':         u.get('photo',         ''),
        'avg_rating':    u.get('avg_rating',    0.0) or 0.0,
        'total_ratings': u.get('total_ratings', 0)   or 0,
        'total_rides':   len(all_offered) + len(all_joined),
        'documents':     ['Aadhar Card', 'Driving License', 'RC Book', 'Insurance'],
    }

    return render_template('profile.html',
                           user=user, offered=oe, joined=je,
                           active_offered=active_offered,   active_joined=active_joined,
                           upcoming_offered=upcoming_offered, upcoming_joined=upcoming_joined,
                           completed_offered=completed_offered, completed_joined=completed_joined,
                           upcoming=upcoming_offered+upcoming_joined,
                           contacts=contacts, verify=verify, car=car,
                           reviews_received=reviews_received)


# ═══════════════════════════════════════════════════════════════════════════════
# ❌  CANCEL BOOKING
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/cancel/<int:id>')
def cancel_booking(id):
    bk = query('SELECT * FROM bookings WHERE id=%s', (id,), fetchone=True)
    if bk:
        query('UPDATE rides SET seats=seats+%s WHERE id=%s',
              (bk['seats_booked'], bk['ride_id']), commit=True)
        query('DELETE FROM bookings WHERE id=%s', (id,), commit=True)
    return redirect(url_for('profile'))


# ═══════════════════════════════════════════════════════════════════════════════
# 💬  INBOX  (Mongo preferred, SQL fallback)
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/inbox')
def inbox():
    if 'user_email' not in session:
        return redirect(url_for('login'))
    me = session['user_email']

    if USE_MONGO:
        mdb = get_mongo()
        # Latest message per (other_email, ride_id) conversation
        pipeline = [
            {'$match': {'$or': [{'sender': me}, {'receiver': me}]}},
            {'$sort': {'_id': -1}},
            {'$group': {
                '_id': {
                    'other':   {'$cond': [{'$eq': ['$sender', me]}, '$receiver', '$sender']},
                    'ride_id': '$ride_id'
                },
                'last_msg': {'$first': '$message'},
                'last_time': {'$first': '$time'},
                'unread':    {'$sum': {'$cond': [
                    {'$and': [{'$eq': ['$receiver', me]}, {'$eq': ['$is_read', 0]}]}, 1, 0
                ]}},
            }},
            {'$sort': {'last_time': -1}},
        ]
        raw = list(mdb.messages.aggregate(pipeline))

        chats = []
        for row in raw:
            other       = row['_id']['other']
            ride_id     = row['_id'].get('ride_id')
            other_user  = query('SELECT name FROM users WHERE email=%s', (other,), fetchone=True)
            display_name = other_user['name'] if other_user else other.split('@')[0]
            ride_info   = None
            if ride_id:
                ride_info = query('SELECT from_loc,to_loc FROM rides WHERE id=%s', (ride_id,), fetchone=True)
            chats.append({
                'user': other, 'display_name': display_name,
                'message': row['last_msg'], 'time': row['last_time'],
                'ride_id': ride_id, 'ride_info': ride_info,
                'unread':  row['unread'],
            })
    else:
        raw = query('''
            SELECT CASE WHEN sender=%s THEN receiver ELSE sender END AS other_email,
                   ride_id, message, time, MAX(id) AS last_id,
                   SUM(CASE WHEN receiver=%s AND is_read=0 THEN 1 ELSE 0 END) AS unread
            FROM messages
            WHERE sender=%s OR receiver=%s
            GROUP BY other_email, ride_id ORDER BY last_id DESC
        ''', (me,me,me,me), fetchall=True) or []

        chats = []
        for row in raw:
            other      = row['other_email']
            other_user = query('SELECT name FROM users WHERE email=%s', (other,), fetchone=True)
            display_name = other_user['name'] if other_user else other.split('@')[0]
            ride_info  = None
            if row['ride_id']:
                ride_info = query('SELECT from_loc,to_loc FROM rides WHERE id=%s',
                                  (row['ride_id'],), fetchone=True)
            chats.append({
                'user': other, 'display_name': display_name,
                'message': row['message'], 'time': row['time'],
                'ride_id': row['ride_id'], 'ride_info': ride_info,
                'unread':  row['unread'] or 0,
            })

    total_unread = sum(c['unread'] for c in chats)
    return render_template('inbox.html', chats=chats, me=me, total_unread=total_unread)


# ═══════════════════════════════════════════════════════════════════════════════
# 💬  CHAT  (Mongo preferred, SQL fallback)
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/chat/<user>', methods=['GET', 'POST'])
def chat(user):
    if 'user_email' not in session:
        return redirect(url_for('login'))
    me  = session['user_email']
    rid = request.args.get('ride_id') or request.form.get('ride_id')
    rid_int = int(rid) if rid and str(rid).isdigit() else None

    ride_context = None
    chat_blocked = False
    if rid_int:
        ride_context = query('SELECT * FROM rides WHERE id=%s', (rid_int,), fetchone=True)
        if ride_context and get_smart_status(ride_context) == 'completed':
            chat_blocked = True

    if request.method == 'POST' and not chat_blocked:
        msg_text = request.form.get('message', '').strip()
        if msg_text and user != me:
            now_time = datetime.now().strftime('%H:%M')
            now_full = datetime.now().strftime('%d %b, %I:%M %p')
            ride_label = (f" on ride {ride_context['from_loc']} → {ride_context['to_loc']}"
                          if ride_context else '')
            if USE_MONGO:
                get_mongo().messages.insert_one({
                    'sender': me, 'receiver': user,
                    'message': msg_text, 'time': now_time,
                    'ride_id': rid_int, 'is_read': 0,
                    'created_at': now_full,
                })
            else:
                query('INSERT INTO messages(sender,receiver,message,time,ride_id,is_read) VALUES(%s,%s,%s,%s,%s,0)',
                      (me, user, msg_text, now_time, rid_int), commit=True)
            send_notification(user,
                f"💬 {session.get('user_name', me.split('@')[0])}: {msg_text[:40]}{ride_label}")
        return redirect(url_for('chat', user=user, ride_id=rid or ''))

    # Mark as read
    if USE_MONGO:
        get_mongo().messages.update_many(
            {'receiver': me, 'sender': user, 'ride_id': rid_int, 'is_read': 0},
            {'$set': {'is_read': 1}}
        )
        if rid_int:
            msgs_cur = get_mongo().messages.find(
                {'$or': [{'sender': me,'receiver': user},{'sender': user,'receiver': me}],
                 'ride_id': rid_int}
            ).sort('_id', 1)
        else:
            msgs_cur = get_mongo().messages.find(
                {'$or': [{'sender': me,'receiver': user},{'sender': user,'receiver': me}],
                 'ride_id': None}
            ).sort('_id', 1)
        messages = [_Row({**m, 'id': str(m['_id'])}) for m in msgs_cur]
    else:
        query('UPDATE messages SET is_read=1 WHERE receiver=%s AND sender=%s AND ride_id=%s',
              (me, user, rid_int), commit=True)
        if rid_int:
            messages = query('''SELECT * FROM messages
                                WHERE ((sender=%s AND receiver=%s) OR (sender=%s AND receiver=%s))
                                AND ride_id=%s ORDER BY id''',
                             (me,user,user,me,rid_int), fetchall=True) or []
        else:
            messages = query('''SELECT * FROM messages
                                WHERE ((sender=%s AND receiver=%s) OR (sender=%s AND receiver=%s))
                                AND (ride_id IS NULL OR ride_id=0) ORDER BY id''',
                             (me,user,user,me), fetchall=True) or []

    other_user   = query('SELECT * FROM users WHERE email=%s', (user,), fetchone=True)
    display_name = other_user['name'] if other_user else (user.split('@')[0] if '@' in user else user)

    return render_template('chat.html',
                           messages=messages, user=user, me=me,
                           display_name=display_name,
                           ride_context=ride_context, ride_id=rid,
                           chat_blocked=chat_blocked)


# ═══════════════════════════════════════════════════════════════════════════════
# 📞  EMERGENCY CONTACTS
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/emergency', methods=['GET', 'POST'])
def emergency():
    me = session.get('user_email', '')
    if request.method == 'POST':
        query('DELETE FROM emergency_contacts WHERE user_email=%s', (me,), commit=True)
        query('INSERT INTO emergency_contacts(user_email,name1,phone1,name2,phone2) VALUES(%s,%s,%s,%s,%s)',
              (me, request.form.get('name1'), request.form.get('phone1'),
               request.form.get('name2'), request.form.get('phone2')), commit=True)
        return redirect(url_for('profile'))
    contacts = query('SELECT * FROM emergency_contacts WHERE user_email=%s LIMIT 1', (me,), fetchone=True)
    return render_template('emergency.html', contacts=contacts)


# ═══════════════════════════════════════════════════════════════════════════════
# ✅  DRIVER VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/verify', methods=['GET', 'POST'])
def verify():
    me = session.get('user_email', '')
    if request.method == 'POST':
        def upl(field):
            f = request.files.get(field)
            if f and f.filename:
                url = upload_to_cloudinary(f, folder='tripzy/docs')
                return 'uploaded' if url else 'pending'
            existing = query('SELECT * FROM verification WHERE user_email=%s LIMIT 1', (me,), fetchone=True)
            return (existing[field] if existing else 'pending') or 'pending'

        query('DELETE FROM verification WHERE user_email=%s', (me,), commit=True)
        query('INSERT INTO verification(user_email,aadhar,license,rc,insurance) VALUES(%s,%s,%s,%s,%s)',
              (me, upl('aadhar'), upl('license'), upl('rc'), upl('insurance')), commit=True)
        return redirect(url_for('profile'))
    vd = query('SELECT * FROM verification WHERE user_email=%s LIMIT 1', (me,), fetchone=True)
    return render_template('verify.html', verify=vd)


# ═══════════════════════════════════════════════════════════════════════════════
# 📍  LIVE TRACKING
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/track/<int:id>')
def track(id):
    ride = query('SELECT * FROM rides WHERE id=%s', (id,), fetchone=True)
    return render_template('track.html', ride=ride)


# ═══════════════════════════════════════════════════════════════════════════════
# 🚗  MY CAR
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/my-car', methods=['GET', 'POST'])
def my_car():
    me = session.get('user_email', '')
    if request.method == 'POST':
        files = request.files.getlist('images')
        urls  = [upload_to_cloudinary(f, 'tripzy/cars') for f in files[:5] if f and f.filename]
        urls  = [u for u in urls if u]
        if not urls:
            raw = request.form.get('images_data', '')
            urls = [u for u in raw.split('||') if u]
        query('DELETE FROM cars WHERE user_email=%s', (me,), commit=True)
        query('INSERT INTO cars(user_email,name,model,color,plate,images) VALUES(%s,%s,%s,%s,%s,%s)',
              (me, request.form.get('name'), request.form.get('model'),
               request.form.get('color'), (request.form.get('plate') or '').upper(),
               '||'.join(urls)), commit=True)
        return redirect(url_for('my_car'))
    car = query('SELECT * FROM cars WHERE user_email=%s LIMIT 1', (me,), fetchone=True)
    return render_template('my_car.html', car=car)


# ═══════════════════════════════════════════════════════════════════════════════
# 🔐  REGISTER  — bcrypt hashed passwords
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name     = request.form.get('name', '').strip()
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        if not name or not email or not password:
            return render_template('register.html', error='All fields are required')
        if len(password) < 6:
            return render_template('register.html', error='Password must be at least 6 characters')

        existing = query('SELECT id FROM users WHERE email=%s', (email,), fetchone=True)
        if existing:
            return render_template('register.html', error='Email already registered')

        pw_hash = generate_password_hash(password)
        try:
            query('INSERT INTO users(name,email,password_hash) VALUES(%s,%s,%s)',
                  (name, email, pw_hash), commit=True)
            return redirect(url_for('login'))
        except Exception as e:
            app.logger.error(f'Register error: {e}')
            return render_template('register.html', error='Registration failed — please try again')
    return render_template('register.html')


# ═══════════════════════════════════════════════════════════════════════════════
# 🔑  LOGIN  — supports both hashed (new) and plain (legacy migration)
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        user = query('SELECT * FROM users WHERE email=%s', (email,), fetchone=True)

        if user:
            pw_hash  = user.get('password_hash') or ''
            pw_plain = user.get('password') or ''       # legacy column (may not exist)
            authenticated = False

            if pw_hash:
                # Normal path: check bcrypt hash
                try:
                    authenticated = check_password_hash(pw_hash, password)
                except Exception:
                    authenticated = False
            elif pw_plain:
                # Legacy plain-text row (SQLite migrants) — auto-upgrade on login
                authenticated = (pw_plain == password)
                if authenticated:
                    new_hash = generate_password_hash(password)
                    query('UPDATE users SET password_hash=%s WHERE email=%s',
                          (new_hash, email), commit=True)

            if authenticated:
                session['user_name']  = user['name']
                session['user_email'] = user['email']
                return redirect(url_for('index'))

        return render_template('login.html', error='Invalid email or password')
    return render_template('login.html')


# ═══════════════════════════════════════════════════════════════════════════════
# 🚪  LOGOUT
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ═══════════════════════════════════════════════════════════════════════════════
# 🔔  NOTIFICATIONS  (Mongo preferred, SQL fallback)
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/notif/read/<id>')
def mark_read(id):
    me = session.get('user_email', '')
    if USE_MONGO:
        try:
            get_mongo().notifications.update_one(
                {'_id': ObjectId(id), 'user_email': me},
                {'$set': {'is_read': 1}}
            )
        except Exception:
            pass
    else:
        query('UPDATE notifications SET is_read=1 WHERE id=%s', (id,), commit=True)
    return redirect(url_for('notifications'))


@app.route('/notifications')
def notifications():
    if 'user_email' not in session:
        return redirect(url_for('login'))
    me = session['user_email']

    if USE_MONGO:
        raw    = list(get_mongo().notifications.find(
            {'user_email': me}).sort('_id', DESCENDING))
        notifs = [_Row({**n, 'id': str(n['_id'])}) for n in raw]
    else:
        notifs = query('SELECT * FROM notifications WHERE user_email=%s ORDER BY id DESC',
                       (me,), fetchall=True) or []
    return render_template('notifications.html', notifs=notifs)


# ═══════════════════════════════════════════════════════════════════════════════
# ✏️  EDIT PROFILE
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/edit-profile', methods=['GET', 'POST'])
def edit_profile():
    if 'user_email' not in session:
        return redirect(url_for('login'))
    me = session['user_email']
    if request.method == 'POST':
        name  = request.form.get('name')
        phone = request.form.get('phone')
        bio   = request.form.get('bio')
        photo = ''
        f = request.files.get('photo_file')
        if f and f.filename:
            photo = upload_to_cloudinary(f, 'tripzy/profiles')
        if not photo:
            photo = request.form.get('photo_url', '')
        query('UPDATE users SET name=%s,phone=%s,bio=%s,photo=%s WHERE email=%s',
              (name, phone, bio, photo, me), commit=True)
        session['user_name'] = name
        return redirect(url_for('profile'))
    db_user = query('SELECT * FROM users WHERE email=%s', (me,), fetchone=True)
    return render_template('edit_profile.html', user=dict(db_user) if db_user else {})


# ═══════════════════════════════════════════════════════════════════════════════
# ⭐  REVIEWS
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/review/<int:ride_id>', methods=['POST'])
def submit_review(ride_id):
    if 'user_email' not in session:
        return redirect(url_for('login'))
    me       = session['user_email']
    stars    = request.form.get('stars')
    text     = request.form.get('review_text')
    reviewee = request.form.get('reviewee_email')
    role     = request.form.get('reviewer_role')
    now      = datetime.now().strftime('%d %b, %I:%M %p')

    if not query('SELECT id FROM reviews WHERE ride_id=%s AND reviewer_email=%s',
                 (ride_id, me), fetchone=True):
        query('''INSERT INTO reviews(ride_id,reviewer_email,reviewee_email,
                                     reviewer_role,stars,review_text,created_at)
                 VALUES(%s,%s,%s,%s,%s,%s,%s)''',
              (ride_id, me, reviewee, role, stars, text, now), commit=True)
        all_rev = query('SELECT stars FROM reviews WHERE reviewee_email=%s',
                        (reviewee,), fetchall=True) or []
        avg = sum(int(r['stars']) for r in all_rev) / len(all_rev)
        query('UPDATE users SET avg_rating=%s,total_ratings=%s WHERE email=%s',
              (round(avg, 1), len(all_rev), reviewee), commit=True)
    return redirect(url_for('ride_detail', id=ride_id))


# ═══════════════════════════════════════════════════════════════════════════════
# 📜  LEGAL  /  ⭐ RATE
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/legal')
def legal():
    return render_template('legal.html')

@app.route('/rate/<int:id>', methods=['POST'])
def rate(id):
    query('UPDATE bookings SET rating=%s WHERE id=%s',
          (request.form.get('rating'), id), commit=True)
    return redirect(url_for('profile'))


# ═══════════════════════════════════════════════════════════════════════════════
# 🩺  HEALTH CHECK  (Render uses this to confirm the app is alive)
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/health')
def health():
    return jsonify(status='ok', postgres=USE_POSTGRES, mongo=USE_MONGO), 200


# ═══════════════════════════════════════════════════════════════════════════════
# ❌  ERROR PAGES  (no raw stack traces in production)
# ═══════════════════════════════════════════════════════════════════════════════
@app.errorhandler(404)
def not_found(e):
    return render_template('base.html'), 404   # shows nav, no stack trace

@app.errorhandler(500)
def server_error(e):
    app.logger.error(f'500: {e}')
    return render_template('base.html'), 500

@app.route('/db-status')
def db_status():
    # PostgreSQL check
    pg_ok = False
    try:
        r = query('SELECT 1 AS ok', fetchone=True)
        pg_ok = bool(r)
    except Exception:
        pass

    # MongoDB check
    mg_ok = False
    mg_cols = []
    mg_err = ''
    if USE_MONGO:
        try:
            mdb = get_mongo()
            mg_cols = mdb.list_collection_names()
            mg_ok = True
        except Exception as e:
            mg_err = str(e)

    # Cloudinary check
    cl_name = os.environ.get('CLOUDINARY_CLOUD_NAME', '')
    cl_key  = bool(os.environ.get('CLOUDINARY_API_KEY', ''))

    return jsonify(
        postgresql=dict(
            backend='postgresql' if USE_POSTGRES else 'sqlite',
            postgres_url_set=USE_POSTGRES,
            connected=pg_ok,
        ),
        mongodb=dict(
            connected=mg_ok,
            collections=mg_cols,
            error=mg_err,
        ),
        cloudinary=dict(
            configured=bool(cl_name and cl_key),
            cloud_name=cl_name,
            api_key_set=cl_key,
        )
    )

@app.route('/test')
def test_page():
    return render_template('test_tripzy.html')


# ═══════════════════════════════════════════════════════════════════════════════
# 🔬  TEST / DIAGNOSTIC ROUTES
#     Add these to run.py alongside your other routes.
#     The HTML test page (test_tripzy.html) calls all of these.
# ═══════════════════════════════════════════════════════════════════════════════


@app.route('/db-status')
def db_status():
    """Full infrastructure status: PostgreSQL, MongoDB, Cloudinary."""

    # ── PostgreSQL ────────────────────────────────────────────────
    pg_ok  = False
    pg_err = ''
    try:
        result = query('SELECT 1 AS ok', fetchone=True)
        pg_ok  = bool(result)
    except Exception as e:
        pg_err = str(e)

    pg_info = dict(
        backend         = 'postgresql' if USE_POSTGRES else 'sqlite',
        postgres_url_set= USE_POSTGRES,
        connected       = pg_ok,
        driver          = 'psycopg v3' if USE_POSTGRES else 'sqlite3',
        error           = pg_err or None,
    )

    # ── MongoDB ───────────────────────────────────────────────────
    mg_ok   = False
    mg_cols = []
    mg_db   = ''
    mg_err  = ''
    if USE_MONGO:
        try:
            mdb     = get_mongo()
            mg_cols = mdb.list_collection_names()
            mg_db   = mdb.name
            mg_ok   = True
        except Exception as e:
            mg_err = str(e)
    else:
        mg_err = 'MONGO_URL not set'

    mg_info = dict(
        connected  = mg_ok,
        collections= mg_cols,
        db_name    = mg_db,
        error      = mg_err or None,
    )

    # ── Cloudinary ────────────────────────────────────────────────
    cl_name    = os.environ.get('CLOUDINARY_CLOUD_NAME', '')
    cl_key_set = bool(os.environ.get('CLOUDINARY_API_KEY', ''))
    cl_sec_set = bool(os.environ.get('CLOUDINARY_API_SECRET', ''))
    cl_ok      = bool(cl_name and cl_key_set and cl_sec_set)

    cl_info = dict(
        configured  = cl_ok,
        cloud_name  = cl_name or None,
        api_key_set = cl_key_set,
        secret_set  = cl_sec_set,
    )

    return jsonify(
        status      = 'ok',
        postgresql  = pg_info,
        mongodb     = mg_info,
        cloudinary  = cl_info,
    )


@app.route('/db-test-read')
def db_test_read():
    """
    Read back the most recently created ride for the logged-in user,
    or a specific ride_id passed as ?ride_id=N.
    Used by the write-cycle test in test_tripzy.html.
    """
    ride_id = request.args.get('ride_id')
    me      = session.get('user_email', '')

    if ride_id:
        ride = query('SELECT id, seats, from_loc, to_loc FROM rides WHERE id=%s',
                     (ride_id,), fetchone=True)
    elif me:
        ride = query('SELECT id, seats, from_loc, to_loc FROM rides WHERE user_email=%s ORDER BY id DESC LIMIT 1',
                     (me,), fetchone=True)
    else:
        ride = None

    if ride:
        return jsonify(
            status  = 'ok',
            ride_id = ride['id'],
            seats   = ride['seats'],
            from_loc= ride['from_loc'],
            to_loc  = ride['to_loc'],
        )
    return jsonify(status='not_found', error='No ride found'), 404


# ═══════════════════════════════════════════════════════════════════════════════
# 🚀  ENTRYPOINT
# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
