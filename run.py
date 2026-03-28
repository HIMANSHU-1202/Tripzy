from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = 'tripzy_secret_2024'

# 🔔 Inject notif_count into all templates
@app.context_processor
def inject_notif_count():
    from flask import session
    if 'user_email' in session:
        try:
            conn = get_db()
            count = conn.execute(
                "SELECT COUNT(*) FROM notifications WHERE user_email=? AND is_read=0",
                (session['user_email'],)
            ).fetchone()[0]
            conn.close()
            return dict(notif_count=count)
        except:
            pass
    return dict(notif_count=0)


# 📦 DATABASE CONNECTION
def get_db():
    conn = sqlite3.connect('tripzy.db')
    conn.row_factory = sqlite3.Row
    return conn


# 🧱 CREATE TABLES
def init_db():
    conn = get_db()
    cur = conn.cursor()

    # 🚗 Rides
    cur.execute('''
    CREATE TABLE IF NOT EXISTS rides (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_loc TEXT,
        to_loc TEXT,
        date TEXT,
        time TEXT,
        seats INTEGER,
        price INTEGER,
        music TEXT,
        smoking TEXT,
        luggage TEXT,
        stops TEXT,
        gender TEXT,
        ac TEXT,
        pets TEXT,
        charging TEXT,
        status TEXT DEFAULT 'not_started'
    )
    ''')

    # 🎟️ Bookings
    cur.execute('''
    CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ride_id INTEGER,
        booked_at TEXT,
        rating TEXT
    )
    ''')

    # 💬 Messages
    cur.execute('''
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender TEXT,
        receiver TEXT,
        message TEXT,
        time TEXT,
        ride_id INTEGER
    )
    ''')
    # Safe migration for existing messages table
    try:
        cur.execute("ALTER TABLE messages ADD COLUMN ride_id INTEGER")
    except:
        pass

    # 📞 Emergency Contacts
    cur.execute('''
    CREATE TABLE IF NOT EXISTS emergency_contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name1 TEXT,
        phone1 TEXT,
        name2 TEXT,
        phone2 TEXT
    )
    ''')

    # ✅ Driver Verification
    cur.execute('''
    CREATE TABLE IF NOT EXISTS verification (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        aadhar TEXT DEFAULT 'pending',
        license TEXT DEFAULT 'pending',
        rc TEXT DEFAULT 'pending',
        insurance TEXT DEFAULT 'pending'
    )
    ''')

    # 👤 Users
    cur.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT UNIQUE,
        password TEXT,
        phone TEXT,
        bio TEXT,
        photo TEXT,
        avg_rating REAL DEFAULT 0.0,
        total_ratings INTEGER DEFAULT 0
    )
    ''')

    # ⭐ Reviews
    cur.execute('''
    CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ride_id INTEGER,
        reviewer_email TEXT,
        reviewee_email TEXT,
        reviewer_role TEXT,
        stars INTEGER,
        review_text TEXT,
        created_at TEXT
    )
    ''')

    # 💰 Commission config
    cur.execute('''
    CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    ''')

    # Default commission = 10%
    cur.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('commission_pct', '10')")

    # 🔔 Notifications
    cur.execute('''
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_email TEXT,
        message TEXT,
        is_read INTEGER DEFAULT 0,
        created_at TEXT
    )
    ''')

    # 🚗 My Car
    cur.execute('''
    CREATE TABLE IF NOT EXISTS cars (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        model TEXT,
        color TEXT,
        plate TEXT,
        images TEXT
    )
    ''')

    conn.commit()

    # 🔧 Safe column migrations for existing DBs
    safe_alters = [
        "ALTER TABLE users ADD COLUMN phone TEXT",
        "ALTER TABLE users ADD COLUMN bio TEXT",
        "ALTER TABLE users ADD COLUMN photo TEXT",
        "ALTER TABLE users ADD COLUMN avg_rating REAL DEFAULT 0.0",
        "ALTER TABLE users ADD COLUMN total_ratings INTEGER DEFAULT 0",
    ]
    for sql in safe_alters:
        try:
            cur.execute(sql)
        except:
            pass

    # Default commission
    cur.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('commission_pct', '10')")

    conn.commit()
    conn.close()


init_db()


# ═══════════════════════════════════════════
# 🧠 SMART RIDE STATUS HELPER
# ═══════════════════════════════════════════

def get_smart_status(ride):
    """
    Hybrid logic:
    - If manually set to 'started' or 'completed' → respect that
    - Otherwise auto-calculate from date+time
    """
    manual = ride['status']

    # Manual override takes priority
    if manual in ('started', 'completed'):
        return manual

    try:
        ride_dt = datetime.strptime(
            f"{ride['date']} {ride['time']}", "%Y-%m-%d %H:%M"
        )
    except:
        return manual  # fallback if format is unexpected

    now = datetime.now()

    if now < ride_dt:
        return 'not_started'           # future ride
    elif now < ride_dt + timedelta(hours=3):
        return 'started'               # within 3hr window
    else:
        return 'completed'             # past 3hr window


def enrich_rides(rides):
    """Attach smart_status and countdown to each ride dict."""
    enriched = []
    now = datetime.now()

    for ride in rides:
        r = dict(ride)
        r['smart_status'] = get_smart_status(ride)

        # Countdown string
        try:
            ride_dt = datetime.strptime(
                f"{ride['date']} {ride['time']}", "%Y-%m-%d %H:%M"
            )
            diff = ride_dt - now
            if diff.total_seconds() > 0:
                total_min = int(diff.total_seconds() // 60)
                hrs  = total_min // 60
                mins = total_min % 60
                if hrs > 24:
                    days = hrs // 24
                    r['countdown'] = f"Starts in {days}d {hrs % 24}h"
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
    """Returns (can_book, reason) tuple."""
    smart = get_smart_status(ride)
    if smart == 'completed':
        return False, "This ride has already completed"
    if smart == 'started':
        return False, "This ride has already started"
    if ride['seats'] <= 0:
        return False, "No seats available"
    return True, ""


# 🏠 HOME
@app.route('/')
def index():
    conn = get_db()
    all_rides = conn.execute("SELECT * FROM rides ORDER BY id DESC").fetchall()
    conn.close()
    enriched = enrich_rides(all_rides)
    # Show only upcoming and active rides
    rides = [r for r in enriched if r['smart_status'] in ('not_started', 'started')]
    return render_template('index.html', rides=rides)


# 🚗 POST RIDE
@app.route('/post', methods=['GET', 'POST'])
def post_ride():
    if request.method == 'POST':
        conn = get_db()

        conn.execute('''
        INSERT INTO rides (
            from_loc, to_loc, date, time, seats, price,
            music, smoking, luggage, stops,
            gender, ac, pets, charging, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            request.form['from'],
            request.form['to'],
            request.form['date'],
            request.form['time'],
            request.form['seats'],
            request.form['price'],
            request.form.get('music'),
            request.form.get('smoking'),
            request.form.get('luggage'),
            request.form.get('stops'),
            request.form.get('gender'),
            request.form.get('ac'),
            request.form.get('pets'),
            request.form.get('charging'),
            'not_started'
        ))

        conn.commit()
        conn.close()

        return redirect(url_for('index'))

    return render_template('post_ride.html')


# 🔍 SEARCH PAGE
@app.route('/search')
def search():
    return render_template('search.html')


# 📄 RESULTS PAGE
@app.route('/results', methods=['GET', 'POST'])
def results():
    conn = get_db()
    all_rides = conn.execute("SELECT * FROM rides").fetchall()
    conn.close()

    if request.method == 'GET':
        return render_template('results.html', rides=[])

    f = request.form.get('from', '').lower()
    t = request.form.get('to', '').lower()

    ac       = request.form.get('ac')
    gender   = request.form.get('gender')
    smoking  = request.form.get('smoking')
    pets     = request.form.get('pets')
    charging = request.form.get('charging')
    sort     = request.form.get('sort')

    matched = []
    for ride in all_rides:
        if f in ride['from_loc'].lower() and t in ride['to_loc'].lower():
            if ac      and ride['ac']      != ac:      continue
            if gender  and ride['gender']  != gender:  continue
            if smoking and ride['smoking'] != smoking:  continue
            if pets    and ride['pets']    != pets:     continue
            if charging and ride['charging'] != charging: continue
            matched.append(ride)

    # Enrich and filter out completed
    enriched = enrich_rides(matched)
    results  = [r for r in enriched if r['smart_status'] in ('not_started', 'started')]

    # Sorting
    if sort == "price":
        results.sort(key=lambda x: int(x['price']))
    elif sort == "seats":
        results.sort(key=lambda x: int(x['seats']), reverse=True)
    elif sort == "time":
        results.sort(key=lambda x: x['time'])

    return render_template('results.html', rides=results)


# 📄 RIDE DETAIL + BOOK
@app.route('/ride/<int:id>', methods=['GET', 'POST'])
def ride_detail(id):
    conn = get_db()

    ride = conn.execute(
        "SELECT * FROM rides WHERE id=?",
        (id,)
    ).fetchone()

    if request.method == 'POST':
        can_book, book_error = is_bookable(ride) if ride else (False, "Ride not found")
        if ride and can_book:

            conn.execute(
                "UPDATE rides SET seats = seats - 1 WHERE id=?",
                (id,)
            )

            booked_at = datetime.now().strftime("%d %b, %I:%M %p")

            conn.execute('''
            INSERT INTO bookings (ride_id, booked_at, rating)
            VALUES (?, ?, ?)
            ''', (
                id,
                booked_at,
                None
            ))

            conn.commit()

            # Get the booking ID just created
            booking_id = conn.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]

            # 🔔 Create notification for logged-in user
            from flask import session as sess
            if 'user_email' in sess:
                conn.execute('''
                INSERT INTO notifications (user_email, message, created_at)
                VALUES (?, ?, ?)
                ''', (
                    sess['user_email'],
                    f"🎟 Booking confirmed! {ride['from_loc']} → {ride['to_loc']} on {ride['date']}. Booking ID: #TRP{booking_id}",
                    booked_at
                ))
                conn.commit()

            ride = conn.execute(
                "SELECT * FROM rides WHERE id=?",
                (id,)
            ).fetchone()

            conn.close()

            return render_template(
                'ride_detail.html',
                ride=ride,
                booked=True,
                booking_id=booking_id,
                booked_at=booked_at
            )

        else:
            conn.close()
            ride_e = dict(ride) if ride else {}
            ride_e['smart_status'] = get_smart_status(ride) if ride else 'not_started'
            ride_e['countdown']    = ''
            return render_template(
                'ride_detail.html',
                ride=ride_e,
                error=book_error
            )

    # Load driver info and reviews
    driver = None
    driver_reviews = []
    can_review = False
    if ride:
        driver = conn.execute(
            "SELECT * FROM users LIMIT 1"
        ).fetchone()
        driver_reviews = conn.execute(
            "SELECT * FROM reviews WHERE ride_id=? ORDER BY id DESC", (id,)
        ).fetchall()
        # Can review if completed and logged in
        if 'user_email' in session and ride['status'] == 'completed':
            existing = conn.execute(
                "SELECT id FROM reviews WHERE ride_id=? AND reviewer_email=?",
                (id, session['user_email'])
            ).fetchone()
            can_review = not existing

    conn.close()
    # Enrich with smart status + countdown
    ride_e = dict(ride) if ride else None
    if ride_e:
        ride_e['smart_status'] = get_smart_status(ride)
        # Countdown
        try:
            ride_dt = datetime.strptime(f"{ride['date']} {ride['time']}", "%Y-%m-%d %H:%M")
            diff = ride_dt - datetime.now()
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
        driver=driver, driver_reviews=driver_reviews,
        can_review=can_review, can_book=can_book)


# 🚀 START RIDE
@app.route('/start/<int:id>')
def start_ride(id):
    conn = get_db()
    conn.execute("UPDATE rides SET status='started' WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('profile'))


# 🛑 END RIDE
@app.route('/end/<int:id>')
def end_ride(id):
    conn = get_db()
    conn.execute("UPDATE rides SET status='completed' WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('profile'))


# 📊 SUMMARY
@app.route('/summary')
def summary():
    conn = get_db()
    all_rides = conn.execute("SELECT * FROM rides").fetchall()
    rides = [r for r in enrich_rides(all_rides) if r['smart_status'] == 'completed']

    # Commission %
    try:
        cfg = conn.execute("SELECT value FROM config WHERE key='commission_pct'").fetchone()
        commission_pct = int(cfg['value']) if cfg else 10
    except:
        commission_pct = 10

    # 💰 Earnings calculations
    total_earnings     = sum(int(r['price']) for r in rides)
    total_rides        = len(rides)
    avg_earning        = round(total_earnings / total_rides, 0) if total_rides > 0 else 0
    best_ride          = max(rides, key=lambda r: int(r['price'])) if rides else None
    total_commission   = round(total_earnings * commission_pct / 100, 0)
    driver_payout      = total_earnings - total_commission

    conn.close()
    return render_template('summary.html',
        rides=rides,
        total_earnings=total_earnings,
        total_rides=total_rides,
        avg_earning=avg_earning,
        best_ride=best_ride,
        commission_pct=commission_pct,
        total_commission=total_commission,
        driver_payout=driver_payout
    )


# 👤 PROFILE
@app.route('/profile')
def profile():
    conn = get_db()

    # ALL rides for stats count
    all_offered = conn.execute("SELECT * FROM rides").fetchall()
    all_joined  = conn.execute('''
        SELECT bookings.id as booking_id, rides.*, bookings.booked_at, bookings.rating
        FROM bookings JOIN rides ON bookings.ride_id = rides.id
    ''').fetchall()

    # Enrich with smart status
    all_offered_e = enrich_rides(all_offered)
    all_joined_e  = enrich_rides(all_joined)

    # Filtered for display sections using smart_status
    active_offered   = [r for r in all_offered_e if r['smart_status'] == 'started']
    active_joined    = [r for r in all_joined_e  if r['smart_status'] == 'started']
    upcoming_offered = [r for r in all_offered_e if r['smart_status'] == 'not_started']
    upcoming_joined  = [r for r in all_joined_e  if r['smart_status'] == 'not_started']
    upcoming         = upcoming_offered + upcoming_joined

    contacts = conn.execute("SELECT * FROM emergency_contacts LIMIT 1").fetchone()
    verify   = conn.execute("SELECT * FROM verification LIMIT 1").fetchone()
    car      = conn.execute("SELECT * FROM cars LIMIT 1").fetchone()

    # Load user from DB if logged in
    db_user = None
    reviews_received = []
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

    # Safe user dict — handles missing columns gracefully
    if db_user:
        db_user_dict = dict(db_user)
    else:
        db_user_dict = {}

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

    return render_template(
        'profile.html',
        user=user,
        offered=all_offered_e,
        joined=all_joined_e,
        active_offered=active_offered,
        active_joined=active_joined,
        upcoming=upcoming,
        contacts=contacts,
        verify=verify,
        car=car,
        reviews_received=reviews_received
    )


# ❌ CANCEL BOOKING
@app.route('/cancel/<int:id>')
def cancel_booking(id):
    conn = get_db()

    booking = conn.execute(
        "SELECT * FROM bookings WHERE id=?",
        (id,)
    ).fetchone()

    if booking:
        conn.execute(
            "UPDATE rides SET seats = seats + 1 WHERE id=?",
            (booking['ride_id'],)
        )
        conn.execute("DELETE FROM bookings WHERE id=?", (id,))
        conn.commit()

    conn.close()
    return redirect(url_for('profile'))


# ⭐ RATE
@app.route('/rate/<int:id>', methods=['POST'])
def rate(id):
    conn = get_db()
    rating = request.form.get('rating')
    conn.execute("UPDATE bookings SET rating=? WHERE id=?", (rating, id))
    conn.commit()
    conn.close()
    return redirect(url_for('profile'))


# 💬 INBOX
@app.route('/inbox')
def inbox():
    conn = get_db()
    me = session.get('user_name', 'You')

    messages = conn.execute(
        "SELECT * FROM messages ORDER BY id DESC"
    ).fetchall()

    chats = []
    seen  = set()

    for msg in messages:
        other = msg['receiver'] if msg['sender'] == me else msg['sender']
        if other not in seen:
            # Get ride info if available
            ride_info = None
            if msg['ride_id']:
                ride_info = conn.execute(
                    "SELECT from_loc, to_loc FROM rides WHERE id=?",
                    (msg['ride_id'],)
                ).fetchone()
            chats.append({
                "user":      other,
                "message":   msg['message'],
                "time":      msg['time'],
                "ride_id":   msg['ride_id'],
                "ride_info": ride_info
            })
            seen.add(other)

    conn.close()
    return render_template('inbox.html', chats=chats, me=me)


# 💬 CHAT
@app.route('/chat/<user>', methods=['GET', 'POST'])
def chat(user):
    conn  = get_db()
    me    = session.get('user_name', 'You')
    rid   = request.args.get('ride_id') or request.form.get('ride_id')

    if request.method == 'POST':
        msg_text = request.form.get('message', '').strip()
        if msg_text:
            conn.execute('''
            INSERT INTO messages (sender, receiver, message, time, ride_id)
            VALUES (?, ?, ?, ?, ?)
            ''', (
                me,
                user,
                msg_text,
                datetime.now().strftime("%H:%M"),
                rid
            ))
            conn.commit()
        return redirect(url_for('chat', user=user, ride_id=rid))

    messages = conn.execute('''
    SELECT * FROM messages
    WHERE (sender=? AND receiver=?) OR (sender=? AND receiver=?)
    ORDER BY id
    ''', (me, user, user, me)).fetchall()

    # Get ride context if provided
    ride_context = None
    if rid:
        ride_context = conn.execute(
            "SELECT * FROM rides WHERE id=?", (rid,)
        ).fetchone()

    conn.close()
    return render_template('chat.html',
        messages=messages, user=user, me=me, ride_context=ride_context, ride_id=rid)


# 📞 EMERGENCY CONTACTS
@app.route('/emergency', methods=['GET', 'POST'])
def emergency():
    conn = get_db()

    if request.method == 'POST':
        conn.execute("DELETE FROM emergency_contacts")
        conn.execute('''
        INSERT INTO emergency_contacts (name1, phone1, name2, phone2)
        VALUES (?, ?, ?, ?)
        ''', (
            request.form.get('name1'),
            request.form.get('phone1'),
            request.form.get('name2'),
            request.form.get('phone2')
        ))
        conn.commit()
        conn.close()
        return redirect(url_for('profile'))

    contacts = conn.execute(
        "SELECT * FROM emergency_contacts LIMIT 1"
    ).fetchone()
    conn.close()
    return render_template('emergency.html', contacts=contacts)


# ✅ DRIVER VERIFICATION
@app.route('/verify', methods=['GET', 'POST'])
def verify():
    conn = get_db()

    if request.method == 'POST':
        conn.execute("DELETE FROM verification")
        conn.execute('''
        INSERT INTO verification (aadhar, license, rc, insurance)
        VALUES (?, ?, ?, ?)
        ''', (
            'uploaded' if request.form.get('aadhar') else 'pending',
            'uploaded' if request.form.get('license') else 'pending',
            'uploaded' if request.form.get('rc') else 'pending',
            'uploaded' if request.form.get('insurance') else 'pending',
        ))
        conn.commit()
        conn.close()
        return redirect(url_for('profile'))

    verify_data = conn.execute(
        "SELECT * FROM verification LIMIT 1"
    ).fetchone()
    conn.close()
    return render_template('verify.html', verify=verify_data)


# 📍 LIVE TRACKING PAGE
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

    if request.method == 'POST':
        # Get base64 images data (joined by ||)
        images_data = request.form.get('images_data', '')

        conn.execute("DELETE FROM cars")
        conn.execute('''
        INSERT INTO cars (name, model, color, plate, images)
        VALUES (?, ?, ?, ?, ?)
        ''', (
            request.form.get('name'),
            request.form.get('model'),
            request.form.get('color'),
            request.form.get('plate').upper(),
            images_data,
        ))
        conn.commit()
        conn.close()
        return redirect(url_for('my_car'))

    car = conn.execute("SELECT * FROM cars LIMIT 1").fetchone()
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
            conn.execute(
                "INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
                (name, email, pwd)
            )
            conn.commit()
            conn.close()
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
            "SELECT * FROM users WHERE email=? AND password=?",
            (email, pwd)
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


# 🔔 MARK NOTIFICATION READ
@app.route('/notif/read/<int:id>')
def mark_read(id):
    conn = get_db()
    conn.execute("UPDATE notifications SET is_read=1 WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('notifications'))


# 🔔 NOTIFICATIONS PAGE
@app.route('/notifications')
def notifications():
    if 'user_email' not in session:
        return redirect(url_for('login'))
    conn = get_db()
    notifs = conn.execute(
        "SELECT * FROM notifications WHERE user_email=? ORDER BY id DESC",
        (session['user_email'],)
    ).fetchall()
    conn.close()
    return render_template('notifications.html', notifs=notifs)


# ✏️ EDIT PROFILE
@app.route('/edit-profile', methods=['GET', 'POST'])
def edit_profile():
    if 'user_email' not in session:
        return redirect(url_for('login'))

    conn = get_db()

    if request.method == 'POST':
        name  = request.form.get('name')
        phone = request.form.get('phone')
        bio   = request.form.get('bio')
        photo_url = request.form.get('photo_url', '')

        # Handle file upload
        photo_data = ''
        file = request.files.get('photo_file')
        if file and file.filename:
            import base64
            photo_data = 'data:' + file.content_type + ';base64,' + base64.b64encode(file.read()).decode()
        elif photo_url:
            photo_data = photo_url

        conn.execute('''
            UPDATE users SET name=?, phone=?, bio=?, photo=?
            WHERE email=?
        ''', (name, phone, bio, photo_data, session['user_email']))
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

    # Check not already reviewed
    existing = conn.execute(
        "SELECT id FROM reviews WHERE ride_id=? AND reviewer_email=?",
        (ride_id, session['user_email'])
    ).fetchone()

    if not existing:
        conn.execute('''
        INSERT INTO reviews (ride_id, reviewer_email, reviewee_email, reviewer_role, stars, review_text, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (ride_id, session['user_email'], reviewee, role, stars, review_text, now))

        # Update reviewee avg rating
        all_reviews = conn.execute(
            "SELECT stars FROM reviews WHERE reviewee_email=?", (reviewee,)
        ).fetchall()
        avg = sum(int(r['stars']) for r in all_reviews) / len(all_reviews)
        conn.execute(
            "UPDATE users SET avg_rating=?, total_ratings=? WHERE email=?",
            (round(avg, 1), len(all_reviews), reviewee)
        )
        conn.commit()

    conn.close()
    return redirect(url_for('ride_detail', id=ride_id))


# 📜 LEGAL
@app.route('/legal')
def legal():
    return render_template('legal.html')


if __name__ == '__main__':
    app.run(debug=True)