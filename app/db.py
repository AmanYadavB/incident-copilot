import sqlite3
import pathlib

def get_connection():
    sqlite_connection = sqlite3.connect(pathlib.Path(__file__).parent.parent / "data" / "incidents.db")
    sqlite_connection.row_factory = sqlite3.Row
    return sqlite_connection

def init_db():
    conn = get_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS alerts (
            id TEXT PRIMARY KEY NOT NULL,
            alertname TEXT,
            severity TEXT,
            service TEXT,
            status TEXT,
            starts_at TEXT,
            raw_json TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()