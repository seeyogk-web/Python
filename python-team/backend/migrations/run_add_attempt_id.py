"""
Runner script to execute the migration SQL that adds and backfills attempt_id.
Run from the backend directory (python -m migrations.run_add_attempt_id) or directly.
This script uses the project's `get_db_connection()` in `config` to connect.
"""
import os
import sys

try:
    from config import get_db_connection
except Exception:
    # In case of path differences, try adjusting import path
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from config import get_db_connection

SQL_FILE = os.path.join(os.path.dirname(__file__), '001_add_attempt_id.sql')


def run():
    if not os.path.exists(SQL_FILE):
        print('Migration SQL file not found:', SQL_FILE)
        return 1

    sql = open(SQL_FILE, 'r', encoding='utf-8').read()

    conn = None
    cur = None
    try:
        conn = get_db_connection()
        # allow multi-statement execution
        conn.autocommit = True
        cur = conn.cursor()
        print('Executing migration SQL...')
        cur.execute(sql)
        print('Migration executed successfully.')
        return 0
    except Exception as e:
        print('Migration failed:', e)
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return 2
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == '__main__':
    sys.exit(run())
