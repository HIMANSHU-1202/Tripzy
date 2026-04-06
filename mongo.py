"""
mongo.py — Tripzy
MongoDB connection for messages and notifications.
Place this file in the same folder as run.py.

Pattern copied from the working Ahira project — uses ServerApi v1,
explicit TLS, and tlsAllowInvalidCertificates for Render compatibility.
"""
import os
from datetime import datetime

# The env var the code reads — set MONGO_URL in Render dashboard
MONGO_URL = os.environ.get(
    'MONGO_URL',
    ''   # empty = disabled, falls back to PostgreSQL
)

_client = None   # cached MongoClient
_db     = None   # cached database handle


def get_client():
    """Return a connected MongoClient, or None if unavailable."""
    global _client
    if _client is not None:
        return _client
    if not MONGO_URL:
        return None
    try:
        from pymongo import MongoClient
        from pymongo.server_api import ServerApi
        client = MongoClient(
            MONGO_URL,
            server_api=ServerApi('1'),
            serverSelectionTimeoutMS=5000,
            tls=True,
            tlsAllowInvalidCertificates=True,
        )
        client.admin.command('ping')   # real auth test
        _client = client
        print('[MongoDB] ✅ Connected successfully')
        return _client
    except Exception as e:
        print(f'[MongoDB] ❌ Connection failed: {e}')
        return None


def get_db():
    """Return the tripzy database, or None if unavailable."""
    global _db
    if _db is not None:
        return _db
    client = get_client()
    if client is None:
        return None
    try:
        # Get the database named in the URL (tripzy) or fall back to 'tripzy'
        _db = client.get_default_database()
    except Exception:
        _db = client['tripzy']
    return _db


def get_collection(name: str):
    """Return a named collection, or None if MongoDB is unavailable."""
    db = get_db()
    if db is None:
        return None
    return db[name]


def is_available() -> bool:
    """True when MongoDB is configured and connected."""
    return get_db() is not None


def reset():
    """Force a reconnect on the next call (useful after env var change)."""
    global _client, _db
    _client = None
    _db     = None


def get_status() -> dict:
    """
    Returns a status dict for the /db-status diagnostic endpoint.
    Always does a fresh connection test.
    """
    reset()
    if not MONGO_URL:
        return {'connected': False, 'error': 'MONGO_URL env var not set', 'collections': []}
    try:
        from pymongo import MongoClient
        from pymongo.server_api import ServerApi
        client = MongoClient(
            MONGO_URL,
            server_api=ServerApi('1'),
            serverSelectionTimeoutMS=5000,
            tls=True,
            tlsAllowInvalidCertificates=True,
        )
        client.admin.command('ping')
        try:
            db   = client.get_default_database()
        except Exception:
            db   = client['tripzy']
        cols = db.list_collection_names()
        return {'connected': True, 'collections': cols, 'db_name': db.name, 'error': None}
    except Exception as e:
        return {'connected': False, 'error': str(e), 'collections': []}


# ── Convenience write helpers ─────────────────────────────────────────────────

def insert_message(sender, receiver, message, time_str, ride_id=None, created_at=None):
    col = get_collection('messages')
    if col is None:
        return None
    try:
        doc = {
            'sender':     sender,
            'receiver':   receiver,
            'message':    message,
            'time':       time_str,
            'ride_id':    ride_id,
            'is_read':    0,
            'created_at': created_at or datetime.now().strftime('%d %b, %I:%M %p'),
        }
        result = col.insert_one(doc)
        return str(result.inserted_id)
    except Exception as e:
        print(f'[MongoDB] insert_message failed: {e}')
        return None


def insert_notification(user_email, message, created_at=None):
    col = get_collection('notifications')
    if col is None:
        return None
    try:
        result = col.insert_one({
            'user_email': user_email,
            'message':    message,
            'is_read':    0,
            'created_at': created_at or datetime.now().strftime('%d %b, %I:%M %p'),
        })
        return str(result.inserted_id)
    except Exception as e:
        print(f'[MongoDB] insert_notification failed: {e}')
        return None


def ensure_indexes():
    """Create indexes for performance. Safe to call multiple times."""
    db = get_db()
    if db is None:
        return
    try:
        db.messages.create_index([('sender', 1), ('receiver', 1)])
        db.messages.create_index([('ride_id', 1)])
        db.messages.create_index([('receiver', 1), ('is_read', 1)])
        db.notifications.create_index([('user_email', 1), ('is_read', 1)])
        print('[MongoDB] ✅ Indexes ensured')
    except Exception as e:
        print(f'[MongoDB] Index creation skipped: {e}')
