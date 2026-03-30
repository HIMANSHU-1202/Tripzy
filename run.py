from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3
from datetime import datetime, timedelta
import os
import base64

app = Flask(__name__)
app.secret_key = 'tripzy_secret_2024'

# ── FIX 1: Increase max upload size to 16MB ──
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB

# ── Handle payload too large error gracefully ──
@app.errorhandler(413)
def too_large(e):
    return render_template('error.html',
        message="Image too large. Please use an image under 2MB."), 413


# 🔔 Inject notif_count + unread_msgs into all templates
@app.context_processor
def inject_notif_count():
    if 'user_email' in session:
        try:
            conn   = get_db()
            me     = session['user_email']
            notifs = conn.execute(
                "SELECT COUNT(*) FROM notifications WHERE user_email=? AND is_read=0",
                (me,)
            ).fetchone()[0]
            unread_msgs = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE receiver=? AND is_read=0",
                (me,)
            ).fetchone()[0]
            conn.close()
            return dict(notif_count=notifs, unread_msgs=unread_msgs)
        except:
            pass
    return dict(notif_count=0, unread_msgs=0)


# 📦 DATABASE CONNECTION
def get_db():
    conn = sqlite3.connect('tripzy.db')
    conn.row_factory = sqlite3.Row
    return conn


# ── FIX 1 helper: compress + resize image to stay under limits ──
def compress_image_b64(file_storage, max_kb=400):
    """
    Read an uploaded file, compress it with Pillow if available,
    and return a base64 data-URI string.
    Falls back to raw base64 if Pillow is not installed.
    """
    raw = file_storage.read()
    mime = file_storage.content_type or 'image/jpeg'

    try:
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(raw))

        # Convert RGBA/P to RGB so JPEG encoding works
        if img.mode in ('RGBA', 'P', 'LA'):
            bg = Image.new('RGB', img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
            img = bg
        elif img.mode != 'RGB':
            img = img.convert('RGB')

        # Downscale if wider than 800px
        max_dim = 800
        w, h = img.size
        if w > max_dim or h > max_dim:
            ratio = min(max_dim / w, max_dim / h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

        # Compress iteratively until under max_kb
        quality = 85
        buf = io.BytesIO()
        while quality >= 30:
            buf.seek(0)
            buf.truncate()
            img.save(buf, format='JPEG', quality=quality, optimize=True)
            if buf.tell() <= max_kb * 1024:
                break
            quality -= 10

        b64 = base64.b64encode(buf.getvalue()).decode()
        return f'data:image/jpeg;base64,{b64}'

    except ImportError:
        # Pillow not installed — just encode raw bytes
        b64 = base64.b64encode(raw).decode()
        return f'data:{mime};base64,{b64}'


# 🧱 CREATE TABLES
def init_db():
    conn = get_db()
    cur  = conn.cursor()

    cur.execute('''CREATE TABLE IF NOT EXISTS rides (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_loc TEXT, to_loc TEXT, date TEXT, time TEXT,
        seats INTEGER, price INTEGER,
        music TEXT, smoking TEXT, luggage TEXT, stops TEXT,
        gender TEXT, ac TEXT, pets TEXT, charging TEXT,
        status TEXT DEFAULT 'not_started'
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ride_id INTEGER, booked_at TEXT, rating TEXT
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender TEXT, receiver TEXT, message TEXT, time TEXT,
        ride_id INTEGER, is_read INTEGER DEFAULT 0
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS chats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ride_id INTEGER, user1_email TEXT, user2_email TEXT,
        created_at TEXT, is_archived INTEGER DEFAULT 0
    )''')

    safe_chat_alters = [
        "ALTER TABLE messages ADD COLUMN ride_id INTEGER",
        "ALTER TABLE messages ADD COLUMN is_read INTEGER DEFAULT 0",
        "ALTER TABLE messages ADD COLUMN chat_id INTEGER",
    ]
    for sql in safe_chat_alters:
        try: cur.execute(sql)
        except: pass

    cur.execute('''CREATE TABLE IF NOT EXISTS emergency_contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name1 TEXT, phone1 TEXT, name2 TEXT, phone2 TEXT
    )''')

    # ── FIX 2: verification now stores actual file data ──
    cur.execute('''CREATE TABLE IF NOT EXISTS verification (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        aadhar TEXT DEFAULT 'pending',
        license TEXT DEFAULT 'pending',
        rc TEXT DEFAULT 'pending',
        insurance TEXT DEFAULT 'pending'
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, email TEXT UNIQUE, password TEXT,
        phone TEXT, bio TEXT, photo TEXT,
        avg_rating REAL DEFAULT 0.0, total_ratings INTEGER DEFAULT 0
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ride_id INTEGER, reviewer_email TEXT, reviewee_email TEXT,
        reviewer_role TEXT, stars INTEGER, review_text TEXT, created_at TEXT
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY, value TEXT
    )''')
    cur.execute("INSERT OR IGNORE INTO config (key,value) VALUES ('commission_pct','10')")

    cur.execute('''CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_email TEXT, message TEXT,
        is_read INTEGER DEFAULT 0, created_at TEXT
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS cars (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, model TEXT, color TEXT, plate TEXT, images TEXT
    )''')

    conn.commit()

    safe_alters = [
        "ALTER TABLE users ADD COLUMN phone TEXT",
        "ALTER TABLE users ADD COLUMN bio TEXT",
        "ALTER TABLE users ADD COLUMN photo TEXT",
        "ALTER TABLE users ADD COLUMN avg_rating REAL DEFAULT 0.0",
        "ALTER TABLE users ADD COLUMN total_ratings INTEGER DEFAULT 0",
        "ALTER TABLE rides ADD COLUMN user_email TEXT",
        "ALTER TABLE bookings ADD COLUMN user_email TEXT",
        "ALTER TABLE emergency_contacts ADD COLUMN user_email TEXT",
        "ALTER TABLE verification ADD COLUMN user_email TEXT",
        "ALTER TABLE cars ADD COLUMN user_email TEXT",
        # FIX 2: add file-data columns to verification
        "ALTER TABLE verification ADD COLUMN aadhar_data TEXT",
        "ALTER TABLE verification ADD COLUMN license_data TEXT",
        "ALTER TABLE verification ADD COLUMN rc_data TEXT",
        "ALTER TABLE verification ADD COLUMN insurance_data TEXT",
    ]
    for sql in safe_alters:
        try: cur.execute(sql)
        except: pass

    conn.commit()
    conn.close()


init_db()


# ═══════════════════════════════════════════
# 🧠 SMART RIDE STATUS HELPER
# ═══════════════════════════════════════════

def get_smart_status(ride):
    manual = ride['status']
    if manual in ('started', 'completed'):
        return manual
    try:
        ride_dt = datetime.strptime(f"{ride['date']} {ride['time']}", "%Y-%m-%d %H:%M")
    except:
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
            ride_dt  = datetime.strptime(f"{ride['date']} {ride['time']}", "%Y-%m-%d %H:%M")
            diff     = ride_dt - now
            total_min = int(diff.total_seconds() // 60)
            if total_min > 0:
                hrs  = total_min // 60
                mins = total_min % 60
                if hrs > 24:
                    days = hrs // 24
                    r['countdown'] = f"Starts in {days}d {hrs%24}h"
                elif hrs > 0:
                    r['countdown'] = f"Starts in {hrs}h {mins}m"
                else:
                    r['countdown'] = f"Starts in {mins} min"
            elif r['smart_status'] == 'started':
                r['countdown'] = "🟢 Ride in progress"
            else:
                r['countdown'] = "✅ Completed"
        except:
            r['countdown'] = ""
        enriched.append(r)
    return enriched


def is_bookable(ride):
    smart = get_smart_status(ride)
    if smart == 'completed': return False, "This ride has already completed"
    if smart == 'started':   return False, "This ride has already started"
    if ride['seats'] <= 0:   return False, "No seats available"
    return True, ""


# 🏠 HOME
@app.route('/')
def index():
    conn = get_db()
    me   = session.get('user_email', '')
    all_rides     = conn.execute("SELECT * FROM rides ORDER BY id DESC").fetchall()
    enriched_all  = enrich_rides(all_rides)

    rides = [
        r for r in enriched_all
        if r['smart_status'] in ('not_started', 'started')
        and r.get('user_email', '') != me
    ]

    my_upcoming_rides = []
    if me:
        my_offered = [
            {**r, 'role': 'driver'}
            for r in enriched_all
            if r.get('user_email', '') == me
            and r['smart_status'] in ('not_started', 'started')
        ]
        my_bookings = conn.execute('''
            SELECT rides.*, bookings.id as booking_id
            FROM bookings JOIN rides ON bookings.ride_id = rides.id
            WHERE bookings.user_email = ?
        ''', (me,)).fetchall()
        my_joined_enriched = enrich_rides(my_bookings)
        my_joined = [
            {**r, 'role': 'passenger'}
            for r in my_joined_enriched
            if r['smart_status'] in ('not_started', 'started')
        ]
        combined = my_offered + my_joined
        try:
            combined.sort(key=lambda r: f"{r['date']} {r['time']}")
        except:
            pass
        my_upcoming_rides = combined[:5]

    conn.close()
    return render_template('index.html', rides=rides, my_upcoming_rides=my_upcoming_rides)


# 🚗 POST RIDE
@app.route('/post', methods=['GET', 'POST'])
def post_ride():
    if request.method == 'POST':
        conn = get_db()
        conn.execute('''INSERT INTO rides (
            from_loc, to_loc, date, time, seats, price,
            music, smoking, luggage, stops,
            gender, ac, pets, charging, status, user_email
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
            request.form['from'], request.form['to'],
            request.form['date'], request.form['time'],
            request.form['seats'], request.form['price'],
            request.form.get('music'), request.form.get('smoking'),
            request.form.get('luggage'), request.form.get('stops'),
            request.form.get('gender'), request.form.get('ac'),
            request.form.get('pets'), request.form.get('charging'),
            'not_started', session.get('user_email', '')
        ))
        conn.commit()
        conn.close()
        return redirect(url_for('index'))
    return render_template('post_ride.html')


# 🔍 SEARCH
@app.route('/search')
def search():
    return render_template('search.html')


# 📄 RESULTS
@app.route('/results', methods=['GET', 'POST'])
def results():
    conn      = get_db()
    all_rides = conn.execute("SELECT * FROM rides").fetchall()
    conn.close()

    if request.method == 'GET':
        return render_template('results.html', rides=[])

    def normalize(s):
        return s.lower().replace(' ', '') if s else ''

    f        = normalize(request.form.get('from', ''))
    t        = normalize(request.form.get('to', ''))
    ac       = request.form.get('ac')
    gender   = request.form.get('gender')
    smoking  = request.form.get('smoking')
    music    = request.form.get('music')
    luggage  = request.form.get('luggage')
    stops    = request.form.get('stops')
    pets     = request.form.get('pets')
    charging = request.form.get('charging')
    sort     = request.form.get('sort')

    user_prefs = {
        'ac': ac, 'gender': gender, 'smoking': smoking,
        'music': music, 'luggage': luggage, 'stops': stops,
        'pets': pets, 'charging': charging
    }

    matched = []
    for ride in all_rides:
        ride_from = normalize(ride['from_loc'])
        ride_to   = normalize(ride['to_loc'])
        if (f in ride_from and t in ride_to) or \
           (f and t and f in ride['from_loc'].lower() and t in ride['to_loc'].lower()):
            matched.append(ride)

    enriched = enrich_rides(matched)
    results  = [r for r in enriched if r['smart_status'] in ('not_started', 'started')]

    if sort == "price":   results.sort(key=lambda x: int(x['price']))
    elif sort == "seats": results.sort(key=lambda x: int(x['seats']), reverse=True)
    elif sort == "time":  results.sort(key=lambda x: x['time'])

    return render_template('results.html', rides=results, user_prefs=user_prefs)


# 📄 RIDE DETAIL + BOOK
@app.route('/ride/<int:id>', methods=['GET', 'POST'])
def ride_detail(id):
    conn = get_db()
    ride = conn.execute("SELECT * FROM rides WHERE id=?", (id,)).fetchone()

    if request.method == 'POST':
        me_email = session.get('user_email', '')
        if ride and dict(ride).get('user_email') == me_email:
            ride_dict = dict(ride)
            ride_dict['smart_status'] = get_smart_status(ride)
            ride_dict['countdown']    = ''
            conn.close()
            return render_template('ride_detail.html', ride=ride_dict,
                error="You cannot book your own ride",
                driver=None, driver_reviews=[], can_review=False, can_book=False)

        can_book, book_error = is_bookable(ride) if ride else (False, "Ride not found")
        if ride and can_book:
            conn.execute("UPDATE rides SET seats=seats-1 WHERE id=?", (id,))
            booked_at = datetime.now().strftime("%d %b, %I:%M %p")
            conn.execute('''INSERT INTO bookings (ride_id,booked_at,rating,user_email)
                VALUES (?,?,?,?)''', (id, booked_at, None, session.get('user_email','')))
            conn.commit()
            booking_id   = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            booker_email = session.get('user_email', '')
            booker_name  = session.get('user_name', 'Someone')
            if booker_email:
                conn.execute('''INSERT INTO notifications (user_email,message,created_at)
                    VALUES (?,?,?)''', (
                    booker_email,
                    f"🎟 Booking confirmed! {ride['from_loc']} → {ride['to_loc']} on {ride['date']}. ID: #TRP{booking_id}",
                    booked_at))
                conn.commit()
            ride_owner = dict(ride).get('user_email', '')
            if ride_owner and ride_owner != booker_email:
                conn.execute('''INSERT INTO notifications (user_email,message,created_at)
                    VALUES (?,?,?)''', (
                    ride_owner,
                    f"🎉 {booker_name} booked a seat on your ride: {ride['from_loc']} → {ride['to_loc']}",
                    booked_at))
                conn.commit()
            ride = conn.execute("SELECT * FROM rides WHERE id=?", (id,)).fetchone()
            conn.close()
            return render_template('ride_detail.html', ride=ride,
                booked=True, booking_id=booking_id, booked_at=booked_at)
        else:
            ride_dict = dict(ride) if ride else {}
            ride_dict['smart_status'] = get_smart_status(ride) if ride else 'not_started'
            ride_dict['countdown']    = ''
            conn.close()
            return render_template('ride_detail.html', ride=ride_dict,
                error=book_error, driver=None, driver_reviews=[],
                driver_car=None, can_review=False, can_book=False)

    driver = driver_car = None
    driver_reviews = []
    can_review     = False
    if ride:
        ride_user_email = dict(ride).get('user_email', '')
        if ride_user_email:
            driver = conn.execute("SELECT * FROM users WHERE email=?", (ride_user_email,)).fetchone()
        driver_reviews = conn.execute(
            "SELECT stars,review_text,reviewer_role,created_at FROM reviews WHERE ride_id=? ORDER BY id DESC", (id,)
        ).fetchall()
        driver_car = conn.execute(
            "SELECT name,model,color,plate FROM cars WHERE user_email=? LIMIT 1", (ride_user_email,)
        ).fetchone() if ride_user_email else None
        if 'user_email' in session and ride['status'] == 'completed':
            existing = conn.execute(
                "SELECT id FROM reviews WHERE ride_id=? AND reviewer_email=?",
                (id, session['user_email'])
            ).fetchone()
            can_review = not existing

    me_email       = session.get('user_email', '')
    is_owner       = False
    already_booked = False
    if ride:
        is_owner = dict(ride).get('user_email', '') == me_email
        already_booked = conn.execute(
            "SELECT id FROM bookings WHERE ride_id=? AND user_email=?", (id, me_email)
        ).fetchone() is not None

    conn.close()
    ride_e = dict(ride) if ride else None
    if ride_e:
        ride_e['smart_status'] = get_smart_status(ride)
        try:
            ride_dt   = datetime.strptime(f"{ride['date']} {ride['time']}", "%Y-%m-%d %H:%M")
            diff      = ride_dt - datetime.now()
            total_min = int(diff.total_seconds() // 60)
            if total_min > 0:
                hrs  = total_min // 60
                mins = total_min % 60
                ride_e['countdown'] = f"Starts in {hrs}h {mins}m" if hrs > 0 else f"Starts in {mins} min"
            elif ride_e['smart_status'] == 'started':
                ride_e['countdown'] = "🟢 Ride in progress"
            else:
                ride_e['countdown'] = "✅ Ride completed"
        except:
            ride_e['countdown'] = ""
        can_book, _ = is_bookable(ride)
    else:
        can_book = False

    return render_template('ride_detail.html', ride=ride_e,
        driver=driver, driver_reviews=driver_reviews, driver_car=driver_car,
        can_review=can_review, can_book=can_book,
        is_owner=is_owner, already_booked=already_booked)


# 🚀 START / END RIDE
@app.route('/start/<int:id>')
def start_ride(id):
    conn = get_db()
    conn.execute("UPDATE rides SET status='started' WHERE id=?", (id,))
    conn.commit(); conn.close()
    return redirect(url_for('profile'))

@app.route('/end/<int:id>')
def end_ride(id):
    conn = get_db()
    conn.execute("UPDATE rides SET status='completed' WHERE id=?", (id,))
    conn.commit(); conn.close()
    return redirect(url_for('profile'))


# 📊 SUMMARY
@app.route('/summary')
def summary():
    conn  = get_db()
    all_rides = conn.execute("SELECT * FROM rides").fetchall()
    rides     = [r for r in enrich_rides(all_rides) if r['smart_status'] == 'completed']
    try:
        cfg = conn.execute("SELECT value FROM config WHERE key='commission_pct'").fetchone()
        commission_pct = int(cfg['value']) if cfg else 10
    except:
        commission_pct = 10
    total_earnings   = sum(int(r['price']) for r in rides)
    total_rides      = len(rides)
    avg_earning      = round(total_earnings / total_rides, 0) if total_rides > 0 else 0
    best_ride        = max(rides, key=lambda r: int(r['price'])) if rides else None
    total_commission = round(total_earnings * commission_pct / 100, 0)
    driver_payout    = total_earnings - total_commission
    conn.close()
    return render_template('summary.html',
        rides=rides, total_earnings=total_earnings, total_rides=total_rides,
        avg_earning=avg_earning, best_ride=best_ride,
        commission_pct=commission_pct,
        total_commission=total_commission, driver_payout=driver_payout)


# 👤 PROFILE
@app.route('/profile')
def profile():
    conn = get_db()
    me   = session.get('user_email', '')

    all_offered = conn.execute("SELECT * FROM rides WHERE user_email=?", (me,)).fetchall()
    all_joined  = conn.execute('''
        SELECT bookings.id as booking_id, rides.*, bookings.booked_at, bookings.rating
        FROM bookings JOIN rides ON bookings.ride_id=rides.id
        WHERE bookings.user_email=?
    ''', (me,)).fetchall()

    all_offered_e = enrich_rides(all_offered)
    all_joined_e  = enrich_rides(all_joined)

    active_offered    = [r for r in all_offered_e if r['smart_status'] == 'started']
    active_joined     = [r for r in all_joined_e  if r['smart_status'] == 'started']
    upcoming_offered  = [r for r in all_offered_e if r['smart_status'] == 'not_started']
    upcoming_joined   = [r for r in all_joined_e  if r['smart_status'] == 'not_started']
    completed_offered = [r for r in all_offered_e if r['smart_status'] == 'completed']
    completed_joined  = [r for r in all_joined_e  if r['smart_status'] == 'completed']
    upcoming          = upcoming_offered + upcoming_joined

    contacts = conn.execute(
        "SELECT * FROM emergency_contacts WHERE user_email=? LIMIT 1", (me,)
    ).fetchone()
    verify   = conn.execute(
        "SELECT * FROM verification WHERE user_email=? LIMIT 1", (me,)
    ).fetchone()
    car      = conn.execute(
        "SELECT * FROM cars WHERE user_email=? LIMIT 1", (me,)
    ).fetchone()

    db_user = reviews_received = None
    if 'user_email' in session:
        db_user = conn.execute(
            "SELECT * FROM users WHERE email=?", (session['user_email'],)
        ).fetchone()
        try:
            reviews_received = conn.execute(
                "SELECT * FROM reviews WHERE reviewee_email=? ORDER BY id DESC",
                (session['user_email'],)
            ).fetchall()
        except:
            reviews_received = []

    db_user_dict = dict(db_user) if db_user else {}
    user = {
        "name":          db_user_dict.get('name', session.get('user_name', 'Guest')),
        "email":         db_user_dict.get('email', session.get('user_email', '')),
        "phone":         db_user_dict.get('phone', ''),
        "bio":           db_user_dict.get('bio', ''),
        "photo":         db_user_dict.get('photo', ''),
        "avg_rating":    db_user_dict.get('avg_rating', 0.0) or 0.0,
        "total_ratings": db_user_dict.get('total_ratings', 0) or 0,
        "total_rides":   len(all_offered) + len(all_joined),
        "documents":     ["Aadhar Card", "Driving License", "RC Book", "Insurance"]
    }
    conn.close()
    return render_template('profile.html', user=user,
        offered=all_offered_e, joined=all_joined_e,
        active_offered=active_offered, active_joined=active_joined,
        upcoming_offered=upcoming_offered, upcoming_joined=upcoming_joined,
        completed_offered=completed_offered, completed_joined=completed_joined,
        upcoming=upcoming, contacts=contacts, verify=verify, car=car,
        reviews_received=reviews_received or [])


# ❌ CANCEL BOOKING
@app.route('/cancel/<int:id>')
def cancel_booking(id):
    conn    = get_db()
    booking = conn.execute("SELECT * FROM bookings WHERE id=?", (id,)).fetchone()
    if booking:
        conn.execute("UPDATE rides SET seats=seats+1 WHERE id=?", (booking['ride_id'],))
        conn.execute("DELETE FROM bookings WHERE id=?", (id,))
        conn.commit()
    conn.close()
    return redirect(url_for('profile'))


# ⭐ RATE
@app.route('/rate/<int:id>', methods=['POST'])
def rate(id):
    conn = get_db()
    conn.execute("UPDATE bookings SET rating=? WHERE id=?", (request.form.get('rating'), id))
    conn.commit(); conn.close()
    return redirect(url_for('profile'))


# 💬 INBOX
@app.route('/inbox')
def inbox():
    if 'user_email' not in session:
        return redirect(url_for('login'))
    conn = get_db()
    me   = session.get('user_email', '')
    raw  = conn.execute('''
        SELECT
            CASE WHEN sender=? THEN receiver ELSE sender END AS other_email,
            ride_id, message, time,
            MAX(id) as last_id,
            SUM(CASE WHEN receiver=? AND is_read=0 THEN 1 ELSE 0 END) as unread
        FROM messages
        WHERE sender=? OR receiver=?
        GROUP BY other_email, ride_id
        ORDER BY last_id DESC
    ''', (me, me, me, me)).fetchall()

    chats = []
    for row in raw:
        other_email  = row['other_email']
        other_user   = conn.execute("SELECT name FROM users WHERE email=?", (other_email,)).fetchone()
        display_name = other_user['name'] if other_user else other_email.split('@')[0]
        ride_info    = None
        if row['ride_id']:
            ride_info = conn.execute(
                "SELECT from_loc,to_loc FROM rides WHERE id=?", (row['ride_id'],)
            ).fetchone()
        chats.append({
            'user': other_email, 'display_name': display_name,
            'message': row['message'], 'time': row['time'],
            'ride_id': row['ride_id'], 'ride_info': ride_info,
            'unread': row['unread'] or 0
        })

    total_unread = sum(c['unread'] for c in chats)
    conn.close()
    return render_template('inbox.html', chats=chats, me=me, total_unread=total_unread)


# 💬 CHAT
@app.route('/chat/<user>', methods=['GET', 'POST'])
def chat(user):
    if 'user_email' not in session:
        return redirect(url_for('login'))
    conn = get_db()
    me   = session.get('user_email', '')
    rid  = request.args.get('ride_id') or request.form.get('ride_id')

    ride_context = chat_blocked = None
    chat_blocked = False
    if rid:
        ride_context = conn.execute("SELECT * FROM rides WHERE id=?", (rid,)).fetchone()
        if ride_context and get_smart_status(ride_context) == 'completed':
            chat_blocked = True

    if request.method == 'POST' and not chat_blocked:
        msg_text = request.form.get('message', '').strip()
        if msg_text and user != me:
            now_time = datetime.now().strftime("%H:%M")
            now_full = datetime.now().strftime("%d %b, %I:%M %p")
            conn.execute('''INSERT INTO messages (sender,receiver,message,time,ride_id,is_read)
                VALUES (?,?,?,?,?,0)''', (me, user, msg_text, now_time, rid))
            conn.commit()
            if user and user != me:
                sender_name = session.get('user_name', me.split('@')[0])
                ride_label  = f" on ride {ride_context['from_loc']} → {ride_context['to_loc']}" if ride_context else ""
                conn.execute('''INSERT INTO notifications (user_email,message,is_read,created_at)
                    VALUES (?,?,0,?)''', (
                    user,
                    f"💬 {sender_name}: {msg_text[:40]}{ride_label}",
                    now_full))
                conn.commit()
        return redirect(url_for('chat', user=user, ride_id=rid or ''))

    conn.execute('''UPDATE messages SET is_read=1
        WHERE receiver=? AND sender=? AND (ride_id=? OR (ride_id IS NULL AND ? IS NULL))
    ''', (me, user, rid, rid))
    conn.commit()

    if rid:
        messages = conn.execute('''SELECT * FROM messages
            WHERE ((sender=? AND receiver=?) OR (sender=? AND receiver=?))
            AND ride_id=? ORDER BY id''', (me, user, user, me, rid)).fetchall()
    else:
        messages = conn.execute('''SELECT * FROM messages
            WHERE ((sender=? AND receiver=?) OR (sender=? AND receiver=?))
            AND (ride_id IS NULL OR ride_id='') ORDER BY id''', (me, user, user, me)).fetchall()

    other_user   = conn.execute("SELECT * FROM users WHERE email=?", (user,)).fetchone()
    display_name = other_user['name'] if other_user else (user.split('@')[0] if '@' in user else user)
    conn.close()
    return render_template('chat.html', messages=messages, user=user, me=me,
        display_name=display_name, ride_context=ride_context,
        ride_id=rid, chat_blocked=chat_blocked)


# ── FIX 3: Emergency Contacts — robust upsert ──
@app.route('/emergency', methods=['GET', 'POST'])
def emergency():
    if 'user_email' not in session:
        return redirect(url_for('login'))

    conn = get_db()
    me   = session['user_email']

    if request.method == 'POST':
        name1  = request.form.get('name1', '').strip()
        phone1 = request.form.get('phone1', '').strip()
        name2  = request.form.get('name2', '').strip()
        phone2 = request.form.get('phone2', '').strip()

        existing = conn.execute(
            "SELECT id FROM emergency_contacts WHERE user_email=?", (me,)
        ).fetchone()

        if existing:
            conn.execute('''UPDATE emergency_contacts
                SET name1=?, phone1=?, name2=?, phone2=?
                WHERE user_email=?''', (name1, phone1, name2, phone2, me))
        else:
            conn.execute('''INSERT INTO emergency_contacts
                (name1, phone1, name2, phone2, user_email)
                VALUES (?,?,?,?,?)''', (name1, phone1, name2, phone2, me))

        conn.commit()
        conn.close()
        return redirect(url_for('profile'))

    contacts = conn.execute(
        "SELECT * FROM emergency_contacts WHERE user_email=? LIMIT 1", (me,)
    ).fetchone()
    conn.close()
    return render_template('emergency.html', contacts=contacts)


# ── FIX 2: Driver Verification — save actual file data per user ──
@app.route('/verify', methods=['GET', 'POST'])
def verify():
    if 'user_email' not in session:
        return redirect(url_for('login'))

    conn = get_db()
    me   = session['user_email']

    if request.method == 'POST':
        existing = conn.execute(
            "SELECT id FROM verification WHERE user_email=?", (me,)
        ).fetchone()

        # Read current state so we don't overwrite already-uploaded docs
        current = conn.execute(
            "SELECT * FROM verification WHERE user_email=? LIMIT 1", (me,)
        ).fetchone()
        current = dict(current) if current else {}

        def process_doc(field_name, current_status_key, current_data_key):
            """Return (status_str, data_str) for one document field."""
            file = request.files.get(field_name)
            if file and file.filename:
                data = compress_image_b64(file, max_kb=300)
                return 'uploaded', data
            # No new file — keep existing
            return (
                current.get(current_status_key, 'pending'),
                current.get(current_data_key, '')
            )

        aadhar_status,  aadhar_data  = process_doc('aadhar',    'aadhar',    'aadhar_data')
        license_status, license_data = process_doc('license',   'license',   'license_data')
        rc_status,      rc_data      = process_doc('rc',        'rc',        'rc_data')
        ins_status,     ins_data     = process_doc('insurance', 'insurance', 'insurance_data')

        if existing:
            conn.execute('''UPDATE verification
                SET aadhar=?, aadhar_data=?,
                    license=?, license_data=?,
                    rc=?, rc_data=?,
                    insurance=?, insurance_data=?
                WHERE user_email=?''',
                (aadhar_status, aadhar_data,
                 license_status, license_data,
                 rc_status, rc_data,
                 ins_status, ins_data,
                 me))
        else:
            conn.execute('''INSERT INTO verification
                (aadhar, aadhar_data, license, license_data,
                 rc, rc_data, insurance, insurance_data, user_email)
                VALUES (?,?,?,?,?,?,?,?,?)''',
                (aadhar_status, aadhar_data,
                 license_status, license_data,
                 rc_status, rc_data,
                 ins_status, ins_data,
                 me))

        conn.commit()
        conn.close()
        return redirect(url_for('profile'))

    verify_data = conn.execute(
        "SELECT * FROM verification WHERE user_email=? LIMIT 1", (me,)
    ).fetchone()
    conn.close()
    return render_template('verify.html', verify=verify_data)


# 📍 LIVE TRACKING
@app.route('/track/<int:id>')
def track(id):
    conn = get_db()
    ride = conn.execute("SELECT * FROM rides WHERE id=?", (id,)).fetchone()
    conn.close()
    return render_template('track.html', ride=ride)


# 🚗 MY CAR
@app.route('/my-car', methods=['GET', 'POST'])
def my_car():
    conn = get_db()
    me   = session.get('user_email', '')

    if request.method == 'POST':
        images_data = request.form.get('images_data', '')
        conn.execute("DELETE FROM cars WHERE user_email=?", (me,))
        conn.execute('''INSERT INTO cars (name,model,color,plate,images,user_email)
            VALUES (?,?,?,?,?,?)''', (
            request.form.get('name'), request.form.get('model'),
            request.form.get('color'), request.form.get('plate', '').upper(),
            images_data, me))
        conn.commit(); conn.close()
        return redirect(url_for('my_car'))

    car = conn.execute("SELECT * FROM cars WHERE user_email=? LIMIT 1", (me,)).fetchone()
    conn.close()
    return render_template('my_car.html', car=car)


# 🔐 REGISTER
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name  = request.form.get('name')
        email = request.form.get('email')
        pwd   = request.form.get('password')
        conn  = get_db()
        try:
            conn.execute("INSERT INTO users (name,email,password) VALUES (?,?,?)", (name, email, pwd))
            conn.commit(); conn.close()
            return redirect(url_for('login'))
        except:
            conn.close()
            return render_template('register.html', error="Email already registered")
    return render_template('register.html')


# 🔑 LOGIN
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        pwd   = request.form.get('password')
        conn  = get_db()
        user  = conn.execute(
            "SELECT * FROM users WHERE email=? AND password=?", (email, pwd)
        ).fetchone()
        conn.close()
        if user:
            session['user_name']  = user['name']
            session['user_email'] = user['email']
            return redirect(url_for('index'))
        return render_template('login.html', error="Invalid email or password")
    return render_template('login.html')


# 🚪 LOGOUT
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# 🔔 NOTIFICATIONS
@app.route('/notif/read/<int:id>')
def mark_read(id):
    conn = get_db()
    conn.execute("UPDATE notifications SET is_read=1 WHERE id=?", (id,))
    conn.commit(); conn.close()
    return redirect(url_for('notifications'))

@app.route('/notifications')
def notifications():
    if 'user_email' not in session:
        return redirect(url_for('login'))
    conn   = get_db()
    notifs = conn.execute(
        "SELECT * FROM notifications WHERE user_email=? ORDER BY id DESC",
        (session['user_email'],)
    ).fetchall()
    conn.close()
    return render_template('notifications.html', notifs=notifs)


# ── FIX 1: Edit profile with image compression ──
@app.route('/edit-profile', methods=['GET', 'POST'])
def edit_profile():
    if 'user_email' not in session:
        return redirect(url_for('login'))
    conn = get_db()

    if request.method == 'POST':
        name      = request.form.get('name')
        phone     = request.form.get('phone')
        bio       = request.form.get('bio')
        photo_url = request.form.get('photo_url', '').strip()
        photo_data = ''

        file = request.files.get('photo_file')
        if file and file.filename:
            # Compress before storing
            photo_data = compress_image_b64(file, max_kb=300)
        elif photo_url:
            photo_data = photo_url

        conn.execute('''UPDATE users SET name=?,phone=?,bio=?,photo=? WHERE email=?''',
            (name, phone, bio, photo_data, session['user_email']))
        conn.commit()
        session['user_name'] = name
        conn.close()
        return redirect(url_for('profile'))

    db_user = conn.execute(
        "SELECT * FROM users WHERE email=?", (session['user_email'],)
    ).fetchone()
    user = dict(db_user) if db_user else {}
    conn.close()
    return render_template('edit_profile.html', user=user)


# ⭐ SUBMIT REVIEW
@app.route('/review/<int:ride_id>', methods=['POST'])
def submit_review(ride_id):
    if 'user_email' not in session:
        return redirect(url_for('login'))
    stars       = request.form.get('stars')
    review_text = request.form.get('review_text')
    reviewee    = request.form.get('reviewee_email')
    role        = request.form.get('reviewer_role')
    conn = get_db()
    now  = datetime.now().strftime("%d %b, %I:%M %p")
    existing = conn.execute(
        "SELECT id FROM reviews WHERE ride_id=? AND reviewer_email=?",
        (ride_id, session['user_email'])
    ).fetchone()
    if not existing:
        conn.execute('''INSERT INTO reviews
            (ride_id,reviewer_email,reviewee_email,reviewer_role,stars,review_text,created_at)
            VALUES (?,?,?,?,?,?,?)''',
            (ride_id, session['user_email'], reviewee, role, stars, review_text, now))
        all_reviews = conn.execute(
            "SELECT stars FROM reviews WHERE reviewee_email=?", (reviewee,)
        ).fetchall()
        avg = sum(int(r['stars']) for r in all_reviews) / len(all_reviews)
        conn.execute("UPDATE users SET avg_rating=?,total_ratings=? WHERE email=?",
            (round(avg,1), len(all_reviews), reviewee))
        conn.commit()
    conn.close()
    return redirect(url_for('ride_detail', id=ride_id))


# 📜 LEGAL
@app.route('/legal')
def legal():
    return render_template('legal.html')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
