"""
migrate_passwords.py  —  run ONCE in Render Shell after first deploy
=====================================================================
Hashes any plain-text passwords left over from the SQLite era.

Usage (Render Shell):
    python migrate_passwords.py
"""
import os
import psycopg
from psycopg.rows import dict_row
from werkzeug.security import generate_password_hash

DATABASE_URL = os.environ.get('DATABASE_URL', '')
if not DATABASE_URL:
    raise SystemExit('Set DATABASE_URL before running.')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
    # Ensure columns exist
    with conn.cursor() as cur:
        cur.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT')
        cur.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS password TEXT')
    conn.commit()

    # Find users with plain-text password but no hash
    with conn.cursor() as cur:
        cur.execute('''
            SELECT id, email, password FROM users
            WHERE (password_hash IS NULL OR password_hash = '')
              AND password IS NOT NULL AND password != ''
        ''')
        rows = cur.fetchall()

    print(f'Found {len(rows)} user(s) to migrate.')
    migrated = 0
    with conn.cursor() as cur:
        for row in rows:
            try:
                h = generate_password_hash(row['password'])
                cur.execute('UPDATE users SET password_hash=%s WHERE id=%s', (h, row['id']))
                migrated += 1
                print(f"  ✓ {row['email']}")
            except Exception as e:
                print(f"  ✗ {row['email']}: {e}")
    conn.commit()
    print(f'\n✅ Migrated {migrated}/{len(rows)} passwords.')

    # Clear plain-text passwords for security
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET password='' WHERE password_hash IS NOT NULL AND password_hash!=''")
    conn.commit()
    print('🔒 Plain-text passwords cleared.')
