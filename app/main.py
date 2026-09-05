from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
import sqlite3

from app.db import get_connection, init_db
from app.models import Alert

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield
    
app = FastAPI(lifespan=lifespan)

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.post("/alerts", status_code=201)
def create_alert(alert: Alert):
    conn = get_connection()
    data = (
        alert.id,
        alert.labels.alertname,
        alert.labels.severity,
        alert.labels.service,
        alert.status,
        alert.starts_at,
        alert.model_dump_json(by_alias=True)
    )
    try:
        with conn:
            conn.execute('''
                INSERT INTO alerts (id, alertname, severity, service, status, starts_at, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', data)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Alert with this ID already exists")
    finally:
        conn.close()
    
    return {"message": "Alert created successfully", "alert": alert}

@app.get("/alerts")
def list_alerts(severity: str | None = None, service: str | None = None, status: str | None = None):
    conn = get_connection()
    clauses, params = [], []
    
    if severity:
        clauses.append("severity = ?")
        params.append(severity)

    if service:
        clauses.append("service = ?")
        params.append(service)

    if status:
        clauses.append("status = ?")
        params.append(status)

    query = "SELECT * FROM alerts"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)

    try:
        cursor = conn.execute(query, params)
        alerts = [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        conn.close()

    return {"alerts": alerts}