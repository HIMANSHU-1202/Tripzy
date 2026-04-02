import os
import cloudinary
import cloudinary.uploader
from flask import Flask, render_template, request, redirect, url_for, session
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'tripzy_secret_2024')

# ═══════════════════════════════════════════
# ☁️  CLOUDINARY CONFIG
# ═══════════════════════════════════════════
cloudinary.config(
    cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME', ''),
    api_key=os.environ.get('CLOUDINARY_API_KEY', ''),
    api_secret=os.environ.get('CLOUDINARY_API_SECRET', ''),
)

def upload_to_cloudinary(file, folder='tripzy'):
    """Upload a file object to Cloudinary, return secure URL or ''."""
    try:
        result = cloudinary.uploader.upload(
            file,
            folder=folder,
            resource_type='auto',
        )
        return result.get('secure_url', '')
    except Exception as e:
        print(f'Cloudinary upload error: {e}')
        return ''


# ═══════════════════════════════════════════
# 🗄️  POSTGRESQL CONNECTION
# ═══════════════════════════════════════════
def get_db():
    """Return a psycopg2 connection using DATABASE_URL env var."""
    database_url = os.environ.get('DATABASE_URL', '')

    # Render supplies postgres:// but psycopg2 needs postgresql://
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)

    conn = psycopg2.connect(database_url)
    return conn


def query(sql, params=(), fetchone=False, fetchall=False, commit=False):
    """
    Thin helper so every route doesn't need to manage cursors manually.
    Returns rows as dicts (RealDictCursor), or lastrowid on INSERT.
    """
    conn = get_db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, params)
    result = None
    if fetchone:
        result = cur.fetchone()
    elif fetchall:
        result = cur.fetchall()
    if commit:
        conn.commit()
        # Return last inserted id if available
        try:
            cur.execute('SELECT lastval()')
            result = cur.fetchone()['lastval']
        except Exception:
            result = None
    cur.close()
    conn.close()
    return result


# ═══════════════════════════════════════════
# 🧱  INIT DATABASE  (PostgreSQL DDL)
# ═══════════════════════════════════════════
def init_db():
    conn = get_db()
    cur  = conn.cursor()

    # ── Users ──────────────────────────────
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id            SERIAL PRIMARY KEY,
            name          TEXT,
            email         TEXT UNIQUE,
            password      TEXT,
            phone         TEXT,
            bio           TEXT,
            photo         TEXT,
            avg_rating    REAL    DEFAULT 0.0,
            total_ratings INTEGER DEFAULT 0
        )
    ''')

    # ── Rides ──────────────────────────────
    cur.execute('''
        CREATE TABLE IF NOT EXISTS rides (
            id         SERIAL PRIMARY KEY,
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
        )
    ''')

    # ── Bookings ───────────────────────────
    cur.execute('''
        CREATE TABLE IF NOT EXISTS bookings (
            id           SERIAL PRIMARY KEY,
            ride_id      INTEGER,
            user_email   TEXT,
            seats_booked INTEGER DEFAULT 1,
            booked_at    TEXT,
            rating       TEXT
        )
    ''')

    # ── Messages ───────────────────────────
    cur.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id        SERIAL PRIMARY KEY,
            sender    TEXT,
            receiver  TEXT,
            message   TEXT,
            time      TEXT,
            ride_id   INTEGER,
            is_read   INTEGER DEFAULT 0
        )
    ''')

    # ── Emergency contacts ─────────────────
    cur.execute('''
        CREATE TABLE IF NOT EXISTS emergency_contacts (
            id         SERIAL PRIMARY KEY,
            user_email TEXT,
            name1      TEXT,
            phone1     TEXT,
            name2      TEXT,
            phone2     TEXT
        )
    ''')

    # ── Verification ───────────────────────
    cur.execute('''
        CREATE TABLE IF NOT EXISTS verification (
            id         SERIAL PRIMARY KEY,
            user_email TEXT,
            aadhar     TEXT DEFAULT 'pending',
            license    TEXT DEFAULT 'pending',
            rc         TEXT DEFAULT 'pending',
            insurance  TEXT DEFAULT 'pending'
        )
    ''')

    # ── Reviews ────────────────────────────
    cur.execute('''
        CREATE TABLE IF NOT EXISTS reviews (
            id             SERIAL PRIMARY KEY,
            ride_id        INTEGER,
            reviewer_email TEXT,
            reviewee_email TEXT,
            reviewer_role  TEXT,
            stars          INTEGER,
            review_text    TEXT,
            created_at     TEXT
        )
    ''')

    # ── Notifications ──────────────────────
    cur.execute('''
        CREATE TABLE IF NOT EXISTS notifications (
            id         SERIAL PRIMARY KEY,
            user_email TEXT,
            message    TEXT,
            is_read    INTEGER DEFAULT 0,
            created_at TEXT
        )
    ''')

    # ── Cars ───────────────────────────────
    cur.execute('''
        CREATE TABLE IF NOT EXISTS cars (
            id         SERIAL PRIMARY KEY,
            user_email TEXT,
            name       TEXT,
            model      TEXT,
            color      TEXT,
            plate      TEXT,
            images     TEXT
        )
    ''')

    # ── Config ─────────────────────────────
    cur.execute('''
        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    cur.execute('''
        INSERT INTO config (key, value)
        VALUES ('commission_pct', '10')
        ON CONFLICT (key) DO NOTHING
    ''')

    # ── Safe column migrations for existing deployments ──
    safe_alters = [
        "ALTER TABLE bookings  ADD COLUMN IF NOT EXISTS seats_booked INTEGER DEFAULT 1",
        "ALTER TABLE bookings  ADD COLUMN IF NOT EXISTS user_email   TEXT",
        "ALTER TABLE rides     ADD COLUMN IF NOT EXISTS user_email   TEXT",
        "ALTER TABLE messages  ADD COLUMN IF NOT EXISTS ride_id      INTEGER",
        "ALTER TABLE messages  ADD COLUMN IF NOT EXISTS is_read      INTEGER DEFAULT 0",
    ]
    for sql in safe_alters:
        try:
            cur.execute(sql)
        except Exception:
            conn.rollback()

    conn.commit()
    cur.close()
    conn.close()


init_db()


# ═══════════════════════════════════════════
# 🔔  CONTEXT PROCESSOR
# ═══════════════════════════════════════════
@app.context_processor
def inject_counts():
    if 'user_email' not in session:
        return dict(notif_count=0, unread_msgs=0)
    try:
        me = session['user_email']
        notif_count = query(
            'SELECT COUNT(*) AS c FROM notifications WHERE user_email=%s AND is_read=0',
            (me,), fetchone=True
        )['c']
        unread_msgs = query(
            'SELECT COUNT(*) AS c FROM messages WHERE receiver=%s AND is_read=0',
            (me,), fetchone=True
        )['c']
        return dict(notif_count=notif_count, unread_msgs=unread_msgs)
    except Exception:
        return dict(notif_count=0, unread_msgs=0)


# ═══════════════════════════════════════════
# 🧠  SMART STATUS + ENRICHMENT HELPERS
# ═══════════════════════════════════════════
def get_smart_status(ride):
    manual = ride['status']
    if manual in ('started', 'completed'):
        return manual
    try:
        ride_dt = datetime.strptime(f"{ride['date']} {ride['time']}", '%Y-%m-%d %H:%M')
    except Exception:
        return manual
    now = datetime.now()
    if now < ride_dt:
        return 'not_started'
    elif now < ride_dt + timedelta(hours=3):
        return 'started'
    else:
        return 'completed'


def enrich_rides(rides):
    enriched = []
    now = datetime.now()
    for ride in rides:
        r = dict(ride)
        r['smart_status'] = get_smart_status(ride)
        try:
            ride_dt = datetime.strptime(f"{ride['date']} {ride['time']}", '%Y-%m-%d %H:%M')
            diff    = ride_dt - now
            total_min = int(diff.total_seconds() // 60)
            if total_min > 0:
                hrs  = total_min // 60
                mins = total_min % 60
                if hrs > 24:
                    r['countdown'] = f"Starts in {hrs//24}d {hrs%24}h"
                elif hrs > 0:
                    r['countdown'] = f"Starts in {hrs}h {mins}m"
                else:
                    r['countdown'] = f"Starts in {mins} min"
            elif r['smart_status'] == 'started':
                r['countdown'] = '🟢 Ride in progress'
            else:
                r['countdown'] = '✅ Completed'
        except Exception:
            r['countdown'] = ''
        enriched.append(r)
    return enriched


def is_bookable(ride, requested_seats=1):
    smart = get_smart_status(ride)
    if smart == 'completed':
        return False, 'This ride has already completed'
    if smart == 'started':
        return False, 'This ride has already started'
    if ride['seats'] <= 0:
        return False, 'No seats available'
    if ride['seats'] < requested_seats:
        return False, f'Only {ride["seats"]} seat(s) left — you requested {requested_seats}'
    return True, ''


# ═══════════════════════════════════════════
# 🏠  HOME
# ═══════════════════════════════════════════
@app.route('/')
def index():
    me = session.get('user_email', '')

    all_rides = query('SELECT * FROM rides ORDER BY id DESC', fetchall=True) or []
    enriched  = enrich_rides(all_rides)
    available = [r for r in enriched if r['smart_status'] in ('not_started', 'started')]

    my_upcoming = []
    if me:
        my_offered = query('SELECT * FROM rides WHERE user_email=%s', (me,), fetchall=True) or []
        my_joined  = query('''
            SELECT b.id AS booking_id, b.seats_booked, r.*
            FROM bookings b JOIN rides r ON b.ride_id = r.id
            WHERE b.user_email=%s
        ''', (me,), fetchall=True) or []

        offered_e = enrich_rides(my_offered)
        joined_e  = enrich_rides(my_joined)

        my_upcoming  = [r for r in offered_e if r['smart_status'] in ('not_started', 'started')]
        my_upcoming += [r for r in joined_e  if r['smart_status'] in ('not_started', 'started')]
        my_upcoming.sort(key=lambda x: (x.get('date',''), x.get('time','')))

    return render_template('index.html', rides=available, my_upcoming_rides=my_upcoming)


# ═══════════════════════════════════════════
# 🚗  POST RIDE
# ═══════════════════════════════════════════
@app.route('/post', methods=['GET', 'POST'])
def post_ride():
    if request.method == 'POST':
        query('''
            INSERT INTO rides
              (user_email, from_loc, to_loc, date, time, seats, price,
               music, smoking, luggage, stops, gender, ac, pets, charging, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'not_started')
        ''', (
            session.get('user_email', ''),
            request.form['from'], request.form['to'],
            request.form['date'], request.form['time'],
            int(request.form['seats']), int(request.form['price']),
            request.form.get('music'),  request.form.get('smoking'),
            request.form.get('luggage'), request.form.get('stops'),
            request.form.get('gender'),  request.form.get('ac'),
            request.form.get('pets'),    request.form.get('charging'),
        ), commit=True)
        return redirect(url_for('index'))
    return render_template('post_ride.html')


# ═══════════════════════════════════════════
# 🔍  SEARCH + RESULTS
# ═══════════════════════════════════════════
@app.route('/search')
def search():
    return render_template('search.html')


@app.route('/results', methods=['GET', 'POST'])
def results():
    if request.method == 'GET':
        return render_template('results.html', rides=[])

    def normalize(s):
        return s.lower().replace(' ', '') if s else ''

    f  = normalize(request.form.get('from', ''))
    t  = normalize(request.form.get('to',   ''))
    requested_seats = int(request.form.get('seats_required', 1) or 1)

    ac       = request.form.get('ac')
    gender   = request.form.get('gender')
    smoking  = request.form.get('smoking')
    music    = request.form.get('music')
    luggage  = request.form.get('luggage')
    stops    = request.form.get('stops')
    pets     = request.form.get('pets')
    charging = request.form.get('charging')
    sort     = request.form.get('sort')

    user_prefs = dict(ac=ac, gender=gender, smoking=smoking, music=music,
                      luggage=luggage, stops=stops, pets=pets, charging=charging)

    all_rides = query('SELECT * FROM rides', fetchall=True) or []

    matched = []
    for ride in all_rides:
        rf = normalize(ride['from_loc'])
        rt = normalize(ride['to_loc'])
        if f in rf and t in rt:
            matched.append(ride)
        elif f and t and f in ride['from_loc'].lower() and t in ride['to_loc'].lower():
            if ride not in matched:
                matched.append(ride)

    enriched = enrich_rides(matched)

    # Only show rides with enough seats and not completed
    filtered = [
        r for r in enriched
        if r['smart_status'] in ('not_started', 'started')
        and r['seats'] >= requested_seats
    ]

    # Mark full rides instead of hiding (bonus feature)
    for r in filtered:
        r['is_full'] = (r['seats'] == 0)

    if sort == 'price':
        filtered.sort(key=lambda x: int(x['price']))
    elif sort == 'seats':
        filtered.sort(key=lambda x: int(x['seats']), reverse=True)
    elif sort == 'time':
        filtered.sort(key=lambda x: x['time'])

    return render_template('results.html', rides=filtered,
                           user_prefs=user_prefs,
                           requested_seats=requested_seats)


# ═══════════════════════════════════════════
# 📄  RIDE DETAIL + MULTI-SEAT BOOKING
# ═══════════════════════════════════════════
@app.route('/ride/<int:id>', methods=['GET', 'POST'])
def ride_detail(id):
    ride = query('SELECT * FROM rides WHERE id=%s', (id,), fetchone=True)

    if request.method == 'POST':
        me_email = session.get('user_email', '')

        # Owner cannot book own ride
        if ride and ride.get('user_email') == me_email:
            ride_e = dict(ride)
            ride_e['smart_status'] = get_smart_status(ride)
            ride_e['countdown'] = ''
            return render_template('ride_detail.html', ride=ride_e,
                                   error='You cannot book your own ride',
                                   driver=None, driver_reviews=[], can_review=False)

        # Read requested seats from form (default 1)
        requested_seats = int(request.form.get('seats_required', 1) or 1)
        can_book, book_error = is_bookable(ride, requested_seats) if ride else (False, 'Ride not found')

        if ride and can_book:
            # Reduce seats atomically
            query('UPDATE rides SET seats = seats - %s WHERE id=%s',
                  (requested_seats, id), commit=True)

            booked_at  = datetime.now().strftime('%d %b, %I:%M %p')
            booking_id = query('''
                INSERT INTO bookings (ride_id, user_email, seats_booked, booked_at)
                VALUES (%s, %s, %s, %s)
            ''', (id, me_email, requested_seats, booked_at), commit=True)

            # Notify booker
            if me_email:
                query('''INSERT INTO notifications (user_email, message, created_at)
                         VALUES (%s,%s,%s)''',
                      (me_email,
                       f"🎟 Booking confirmed! {ride['from_loc']} → {ride['to_loc']} "
                       f"on {ride['date']}. {requested_seats} seat(s). ID: #TRP{booking_id}",
                       booked_at), commit=True)

            # Notify driver
            owner = ride.get('user_email', '')
            if owner and owner != me_email:
                booker_name = session.get('user_name', me_email.split('@')[0])
                query('''INSERT INTO notifications (user_email, message, created_at)
                         VALUES (%s,%s,%s)''',
                      (owner,
                       f"🎉 {booker_name} booked {requested_seats} seat(s) on your ride: "
                       f"{ride['from_loc']} → {ride['to_loc']}",
                       booked_at), commit=True)

            ride = query('SELECT * FROM rides WHERE id=%s', (id,), fetchone=True)
            return render_template('ride_detail.html', ride=ride,
                                   booked=True, booking_id=booking_id,
                                   booked_at=booked_at,
                                   seats_booked=requested_seats)
        else:
            ride_e = dict(ride) if ride else {}
            ride_e['smart_status'] = get_smart_status(ride) if ride else 'not_started'
            ride_e['countdown'] = ''
            return render_template('ride_detail.html', ride=ride_e,
                                   error=book_error, driver=None,
                                   driver_reviews=[], driver_car=None,
                                   can_review=False, can_book=False)

    # ── GET ──────────────────────────────────
    driver        = None
    driver_reviews = []
    driver_car    = None
    can_review    = False

    if ride:
        owner_email = ride.get('user_email', '')
        if owner_email:
            driver     = query('SELECT * FROM users WHERE email=%s', (owner_email,), fetchone=True)
            driver_car = query('SELECT name,model,color,plate FROM cars WHERE user_email=%s LIMIT 1',
                               (owner_email,), fetchone=True)
        driver_reviews = query(
            'SELECT stars,review_text,reviewer_role,created_at FROM reviews WHERE ride_id=%s ORDER BY id DESC',
            (id,), fetchall=True) or []

        me = session.get('user_email', '')
        if me and ride['status'] == 'completed':
            existing = query('SELECT id FROM reviews WHERE ride_id=%s AND reviewer_email=%s',
                             (id, me), fetchone=True)
            can_review = not existing

    me_email      = session.get('user_email', '')
    is_owner      = ride and ride.get('user_email') == me_email
    already_booked = False
    seats_booked_by_me = 0

    if ride and me_email:
        bk = query('SELECT id, seats_booked FROM bookings WHERE ride_id=%s AND user_email=%s',
                   (id, me_email), fetchone=True)
        already_booked     = bk is not None
        seats_booked_by_me = bk['seats_booked'] if bk else 0

    ride_e = dict(ride) if ride else None
    if ride_e:
        ride_e['smart_status'] = get_smart_status(ride)
        try:
            ride_dt   = datetime.strptime(f"{ride['date']} {ride['time']}", '%Y-%m-%d %H:%M')
            diff      = ride_dt - datetime.now()
            total_min = int(diff.total_seconds() // 60)
            if total_min > 0:
                hrs  = total_min // 60
                mins = total_min % 60
                ride_e['countdown'] = f"Starts in {hrs}h {mins}m" if hrs > 0 else f"Starts in {mins} min"
            elif ride_e['smart_status'] == 'started':
                ride_e['countdown'] = '🟢 Ride in progress'
            else:
                ride_e['countdown'] = '✅ Ride completed'
        except Exception:
            ride_e['countdown'] = ''
        can_book, _ = is_bookable(ride)
    else:
        can_book = False

    return render_template('ride_detail.html',
                           ride=ride_e,
                           driver=driver, driver_reviews=driver_reviews,
                           driver_car=driver_car,
                           can_review=can_review, can_book=can_book,
                           is_owner=is_owner, already_booked=already_booked,
                           seats_booked_by_me=seats_booked_by_me,
                           verify=None)


# ═══════════════════════════════════════════
# 🚀  START / END RIDE
# ═══════════════════════════════════════════
@app.route('/start/<int:id>')
def start_ride(id):
    query("UPDATE rides SET status='started' WHERE id=%s", (id,), commit=True)
    return redirect(url_for('profile'))


@app.route('/end/<int:id>')
def end_ride(id):
    query("UPDATE rides SET status='completed' WHERE id=%s", (id,), commit=True)
    return redirect(url_for('profile'))


# ═══════════════════════════════════════════
# 📊  SUMMARY
# ═══════════════════════════════════════════
@app.route('/summary')
def summary():
    me = session.get('user_email', '')
    all_rides = query('SELECT * FROM rides WHERE user_email=%s', (me,), fetchall=True) or []
    rides     = [r for r in enrich_rides(all_rides) if r['smart_status'] == 'completed']

    cfg = query("SELECT value FROM config WHERE key='commission_pct'", fetchone=True)
    commission_pct = int(cfg['value']) if cfg else 10

    total_earnings   = sum(int(r['price']) for r in rides)
    total_rides      = len(rides)
    avg_earning      = round(total_earnings / total_rides, 0) if total_rides > 0 else 0
    best_ride        = max(rides, key=lambda r: int(r['price'])) if rides else None
    total_commission = round(total_earnings * commission_pct / 100, 0)
    driver_payout    = total_earnings - total_commission

    return render_template('summary.html',
                           rides=rides, total_earnings=total_earnings,
                           total_rides=total_rides, avg_earning=avg_earning,
                           best_ride=best_ride, commission_pct=commission_pct,
                           total_commission=total_commission, driver_payout=driver_payout)


# ═══════════════════════════════════════════
# 👤  PROFILE
# ═══════════════════════════════════════════
@app.route('/profile')
def profile():
    if 'user_email' not in session:
        return redirect(url_for('login'))

    me = session['user_email']

    all_offered = query('SELECT * FROM rides WHERE user_email=%s', (me,), fetchall=True) or []
    all_joined  = query('''
        SELECT b.id AS booking_id, b.seats_booked, b.rating, b.booked_at, r.*
        FROM bookings b JOIN rides r ON b.ride_id = r.id
        WHERE b.user_email=%s
    ''', (me,), fetchall=True) or []

    offered_e = enrich_rides(all_offered)
    joined_e  = enrich_rides(all_joined)

    active_offered    = [r for r in offered_e if r['smart_status'] == 'started']
    active_joined     = [r for r in joined_e  if r['smart_status'] == 'started']
    upcoming_offered  = [r for r in offered_e if r['smart_status'] == 'not_started']
    upcoming_joined   = [r for r in joined_e  if r['smart_status'] == 'not_started']
    completed_offered = [r for r in offered_e if r['smart_status'] == 'completed']
    completed_joined  = [r for r in joined_e  if r['smart_status'] == 'completed']

    contacts = query('SELECT * FROM emergency_contacts WHERE user_email=%s LIMIT 1',
                     (me,), fetchone=True)
    verify   = query('SELECT * FROM verification WHERE user_email=%s LIMIT 1',
                     (me,), fetchone=True)
    car      = query('SELECT * FROM cars WHERE user_email=%s LIMIT 1',
                     (me,), fetchone=True)

    db_user = query('SELECT * FROM users WHERE email=%s', (me,), fetchone=True)
    reviews_received = query(
        'SELECT * FROM reviews WHERE reviewee_email=%s ORDER BY id DESC',
        (me,), fetchall=True) or []

    db_user_dict = dict(db_user) if db_user else {}
    user = {
        'name':          db_user_dict.get('name',          session.get('user_name', 'Guest')),
        'email':         db_user_dict.get('email',         me),
        'phone':         db_user_dict.get('phone',         ''),
        'bio':           db_user_dict.get('bio',           ''),
        'photo':         db_user_dict.get('photo',         ''),
        'avg_rating':    db_user_dict.get('avg_rating',    0.0) or 0.0,
        'total_ratings': db_user_dict.get('total_ratings', 0)   or 0,
        'total_rides':   len(all_offered) + len(all_joined),
        'documents':     ['Aadhar Card', 'Driving License', 'RC Book', 'Insurance'],
    }

    return render_template('profile.html',
                           user=user,
                           offered=offered_e, joined=joined_e,
                           active_offered=active_offered,   active_joined=active_joined,
                           upcoming_offered=upcoming_offered, upcoming_joined=upcoming_joined,
                           completed_offered=completed_offered, completed_joined=completed_joined,
                           upcoming=upcoming_offered + upcoming_joined,
                           contacts=contacts, verify=verify, car=car,
                           reviews_received=reviews_received)


# ═══════════════════════════════════════════
# ❌  CANCEL BOOKING
# ═══════════════════════════════════════════
@app.route('/cancel/<int:id>')
def cancel_booking(id):
    booking = query('SELECT * FROM bookings WHERE id=%s', (id,), fetchone=True)
    if booking:
        query('UPDATE rides SET seats = seats + %s WHERE id=%s',
              (booking['seats_booked'], booking['ride_id']), commit=True)
        query('DELETE FROM bookings WHERE id=%s', (id,), commit=True)
    return redirect(url_for('profile'))


# ═══════════════════════════════════════════
# ⭐  RATE
# ═══════════════════════════════════════════
@app.route('/rate/<int:id>', methods=['POST'])
def rate(id):
    query('UPDATE bookings SET rating=%s WHERE id=%s',
          (request.form.get('rating'), id), commit=True)
    return redirect(url_for('profile'))


# ═══════════════════════════════════════════
# 💬  INBOX
# ═══════════════════════════════════════════
@app.route('/inbox')
def inbox():
    if 'user_email' not in session:
        return redirect(url_for('login'))

    me = session['user_email']
    raw = query('''
        SELECT
            CASE WHEN sender=%s THEN receiver ELSE sender END AS other_email,
            ride_id,
            message,
            time,
            MAX(id) AS last_id,
            SUM(CASE WHEN receiver=%s AND is_read=0 THEN 1 ELSE 0 END) AS unread
        FROM messages
        WHERE sender=%s OR receiver=%s
        GROUP BY other_email, ride_id
        ORDER BY last_id DESC
    ''', (me, me, me, me), fetchall=True) or []

    chats = []
    for row in raw:
        other     = row['other_email']
        other_user = query('SELECT name FROM users WHERE email=%s', (other,), fetchone=True)
        display_name = other_user['name'] if other_user else other.split('@')[0]
        ride_info = None
        if row['ride_id']:
            ride_info = query('SELECT from_loc, to_loc FROM rides WHERE id=%s',
                              (row['ride_id'],), fetchone=True)
        chats.append({
            'user': other, 'display_name': display_name,
            'message': row['message'], 'time': row['time'],
            'ride_id': row['ride_id'], 'ride_info': ride_info,
            'unread': row['unread'] or 0,
        })

    total_unread = sum(c['unread'] for c in chats)
    return render_template('inbox.html', chats=chats, me=me, total_unread=total_unread)


# ═══════════════════════════════════════════
# 💬  CHAT
# ═══════════════════════════════════════════
@app.route('/chat/<user>', methods=['GET', 'POST'])
def chat(user):
    if 'user_email' not in session:
        return redirect(url_for('login'))

    me  = session['user_email']
    rid = request.args.get('ride_id') or request.form.get('ride_id')

    ride_context = None
    chat_blocked = False
    if rid:
        ride_context = query('SELECT * FROM rides WHERE id=%s', (rid,), fetchone=True)
        if ride_context and get_smart_status(ride_context) == 'completed':
            chat_blocked = True

    if request.method == 'POST' and not chat_blocked:
        msg_text = request.form.get('message', '').strip()
        if msg_text and user != me:
            now_time = datetime.now().strftime('%H:%M')
            now_full = datetime.now().strftime('%d %b, %I:%M %p')
            query('''INSERT INTO messages (sender,receiver,message,time,ride_id,is_read)
                     VALUES (%s,%s,%s,%s,%s,0)''',
                  (me, user, msg_text, now_time, rid), commit=True)
            sender_name = session.get('user_name', me.split('@')[0])
            ride_label  = (f" on ride {ride_context['from_loc']} → {ride_context['to_loc']}"
                           if ride_context else '')
            query('''INSERT INTO notifications (user_email,message,is_read,created_at)
                     VALUES (%s,%s,0,%s)''',
                  (user, f"💬 {sender_name}: {msg_text[:40]}{ride_label}", now_full),
                  commit=True)
        return redirect(url_for('chat', user=user, ride_id=rid or ''))

    # Mark as read
    query('''UPDATE messages SET is_read=1
             WHERE receiver=%s AND sender=%s AND (ride_id=%s OR (ride_id IS NULL AND %s IS NULL))''',
          (me, user, rid, rid), commit=True)

    if rid:
        messages = query('''
            SELECT * FROM messages
            WHERE ((sender=%s AND receiver=%s) OR (sender=%s AND receiver=%s))
            AND ride_id=%s ORDER BY id
        ''', (me, user, user, me, rid), fetchall=True) or []
    else:
        messages = query('''
            SELECT * FROM messages
            WHERE ((sender=%s AND receiver=%s) OR (sender=%s AND receiver=%s))
            AND (ride_id IS NULL OR ride_id::text='')
            ORDER BY id
        ''', (me, user, user, me), fetchall=True) or []

    other_user   = query('SELECT * FROM users WHERE email=%s', (user,), fetchone=True)
    display_name = other_user['name'] if other_user else (user.split('@')[0] if '@' in user else user)

    return render_template('chat.html',
                           messages=messages, user=user, me=me,
                           display_name=display_name,
                           ride_context=ride_context, ride_id=rid,
                           chat_blocked=chat_blocked)


# ═══════════════════════════════════════════
# 📞  EMERGENCY CONTACTS
# ═══════════════════════════════════════════
@app.route('/emergency', methods=['GET', 'POST'])
def emergency():
    me = session.get('user_email', '')
    if request.method == 'POST':
        query('DELETE FROM emergency_contacts WHERE user_email=%s', (me,), commit=True)
        query('''INSERT INTO emergency_contacts (user_email,name1,phone1,name2,phone2)
                 VALUES (%s,%s,%s,%s,%s)''',
              (me, request.form.get('name1'), request.form.get('phone1'),
               request.form.get('name2'), request.form.get('phone2')), commit=True)
        return redirect(url_for('profile'))
    contacts = query('SELECT * FROM emergency_contacts WHERE user_email=%s LIMIT 1',
                     (me,), fetchone=True)
    return render_template('emergency.html', contacts=contacts)


# ═══════════════════════════════════════════
# ✅  DRIVER VERIFICATION  (Cloudinary upload)
# ═══════════════════════════════════════════
@app.route('/verify', methods=['GET', 'POST'])
def verify():
    me = session.get('user_email', '')
    if request.method == 'POST':
        existing = query('SELECT * FROM verification WHERE user_email=%s LIMIT 1',
                         (me,), fetchone=True)

        def upload_doc(field):
            f = request.files.get(field)
            if f and f.filename:
                url = upload_to_cloudinary(f, folder='tripzy/docs')
                return ('uploaded', url) if url else ('pending', '')
            # Keep existing URL if no new file uploaded
            if existing:
                return existing[field], existing.get(field + '_url', '')
            return 'pending', ''

        aadhar_s,   aadhar_url   = upload_doc('aadhar')
        license_s,  license_url  = upload_doc('license')
        rc_s,       rc_url       = upload_doc('rc')
        insurance_s, ins_url     = upload_doc('insurance')

        query('DELETE FROM verification WHERE user_email=%s', (me,), commit=True)
        query('''INSERT INTO verification
                   (user_email, aadhar, license, rc, insurance)
                 VALUES (%s,%s,%s,%s,%s)''',
              (me, aadhar_s, license_s, rc_s, insurance_s), commit=True)
        return redirect(url_for('profile'))

    verify_data = query('SELECT * FROM verification WHERE user_email=%s LIMIT 1',
                        (me,), fetchone=True)
    return render_template('verify.html', verify=verify_data)


# ═══════════════════════════════════════════
# 📍  LIVE TRACKING
# ═══════════════════════════════════════════
@app.route('/track/<int:id>')
def track(id):
    ride = query('SELECT * FROM rides WHERE id=%s', (id,), fetchone=True)
    return render_template('track.html', ride=ride)


# ═══════════════════════════════════════════
# 🚗  MY CAR  (Cloudinary upload)
# ═══════════════════════════════════════════
@app.route('/my-car', methods=['GET', 'POST'])
def my_car():
    me = session.get('user_email', '')
    if request.method == 'POST':
        # Handle multiple image uploads to Cloudinary
        files        = request.files.getlist('images')
        image_urls   = []
        for f in files[:5]:
            if f and f.filename:
                url = upload_to_cloudinary(f, folder='tripzy/cars')
                if url:
                    image_urls.append(url)

        # Fallback: base64 data from hidden field (existing behaviour)
        if not image_urls:
            images_data = request.form.get('images_data', '')
            image_urls  = [u for u in images_data.split('||') if u]

        query('DELETE FROM cars WHERE user_email=%s', (me,), commit=True)
        query('''INSERT INTO cars (user_email, name, model, color, plate, images)
                 VALUES (%s,%s,%s,%s,%s,%s)''',
              (me,
               request.form.get('name'), request.form.get('model'),
               request.form.get('color'), (request.form.get('plate') or '').upper(),
               '||'.join(image_urls)), commit=True)
        return redirect(url_for('my_car'))

    car = query('SELECT * FROM cars WHERE user_email=%s LIMIT 1', (me,), fetchone=True)
    return render_template('my_car.html', car=car)


# ═══════════════════════════════════════════
# 🔐  REGISTER / LOGIN / LOGOUT
# ═══════════════════════════════════════════
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        try:
            query('''INSERT INTO users (name,email,password) VALUES (%s,%s,%s)''',
                  (request.form['name'], request.form['email'], request.form['password']),
                  commit=True)
            return redirect(url_for('login'))
        except psycopg2.errors.UniqueViolation:
            return render_template('register.html', error='Email already registered')
        except Exception:
            return render_template('register.html', error='Registration failed')
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = query('SELECT * FROM users WHERE email=%s AND password=%s',
                     (request.form['email'], request.form['password']), fetchone=True)
        if user:
            session['user_name']  = user['name']
            session['user_email'] = user['email']
            return redirect(url_for('index'))
        return render_template('login.html', error='Invalid email or password')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ═══════════════════════════════════════════
# 🔔  NOTIFICATIONS
# ═══════════════════════════════════════════
@app.route('/notif/read/<int:id>')
def mark_read(id):
    query('UPDATE notifications SET is_read=1 WHERE id=%s', (id,), commit=True)
    return redirect(url_for('notifications'))


@app.route('/notifications')
def notifications():
    if 'user_email' not in session:
        return redirect(url_for('login'))
    notifs = query('SELECT * FROM notifications WHERE user_email=%s ORDER BY id DESC',
                   (session['user_email'],), fetchall=True) or []
    return render_template('notifications.html', notifs=notifs)


# ═══════════════════════════════════════════
# ✏️  EDIT PROFILE  (Cloudinary upload)
# ═══════════════════════════════════════════
@app.route('/edit-profile', methods=['GET', 'POST'])
def edit_profile():
    if 'user_email' not in session:
        return redirect(url_for('login'))
    me = session['user_email']

    if request.method == 'POST':
        name      = request.form.get('name')
        phone     = request.form.get('phone')
        bio       = request.form.get('bio')
        photo_url = request.form.get('photo_url', '')

        # Try Cloudinary upload first
        photo_data = ''
        file = request.files.get('photo_file')
        if file and file.filename:
            photo_data = upload_to_cloudinary(file, folder='tripzy/profiles')
        if not photo_data and photo_url:
            photo_data = photo_url

        query('UPDATE users SET name=%s, phone=%s, bio=%s, photo=%s WHERE email=%s',
              (name, phone, bio, photo_data, me), commit=True)
        session['user_name'] = name
        return redirect(url_for('profile'))

    db_user = query('SELECT * FROM users WHERE email=%s', (me,), fetchone=True)
    user = dict(db_user) if db_user else {}
    return render_template('edit_profile.html', user=user)


# ═══════════════════════════════════════════
# ⭐  SUBMIT REVIEW
# ═══════════════════════════════════════════
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

    existing = query('SELECT id FROM reviews WHERE ride_id=%s AND reviewer_email=%s',
                     (ride_id, me), fetchone=True)
    if not existing:
        query('''INSERT INTO reviews
                   (ride_id,reviewer_email,reviewee_email,reviewer_role,stars,review_text,created_at)
                 VALUES (%s,%s,%s,%s,%s,%s,%s)''',
              (ride_id, me, reviewee, role, stars, text, now), commit=True)

        all_rev = query('SELECT stars FROM reviews WHERE reviewee_email=%s',
                        (reviewee,), fetchall=True) or []
        avg = sum(int(r['stars']) for r in all_rev) / len(all_rev)
        query('UPDATE users SET avg_rating=%s, total_ratings=%s WHERE email=%s',
              (round(avg, 1), len(all_rev), reviewee), commit=True)

    return redirect(url_for('ride_detail', id=ride_id))


# ═══════════════════════════════════════════
# 📜  LEGAL
# ═══════════════════════════════════════════
@app.route('/legal')
def legal():
    return render_template('legal.html')


# ═══════════════════════════════════════════
# 🚀  ENTRYPOINT
# ═══════════════════════════════════════════
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
