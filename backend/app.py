from __future__ import annotations

import os
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, jsonify, request, session
from flask_cors import CORS
from flask_session import Session
from redis import Redis

from portal_sync import PortalSyncService
from storage import SubjectStore


load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
redis_url = os.getenv("REDIS_URL", "").strip()

if redis_url:
    app.config["SESSION_TYPE"] = "redis"
    app.config["SESSION_REDIS"] = Redis.from_url(redis_url)
else:
    app.config["SESSION_TYPE"] = "filesystem"

app.config["SESSION_PERMANENT"] = False
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
Session(app)

frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
CORS(app, supports_credentials=True, origins=[frontend_url])

store = SubjectStore()
sync_service = PortalSyncService()


@app.errorhandler(ValueError)
def handle_value_error(error: ValueError):
    return jsonify({"message": str(error)}), 400


@app.get("/api/health")
def health_check():
    return jsonify({"status": "ok"})


@app.get("/api/session")
def session_status():
    portal_session = session.get("portal_session")
    return jsonify(
        {
            "authenticated": bool(portal_session),
            "matricula": portal_session.get("matricula") if portal_session else None,
        }
    )


@app.post("/api/login")
def portal_login():
    payload = request.get_json(silent=True) or {}
    result = sync_service.login(payload)
    if result.get("status") == "success":
        session.permanent = bool(payload.get("rememberMe"))
        session["portal_session"] = result["portalSession"]
    return jsonify({k: v for k, v in result.items() if k != "portalSession"}), result.get(
        "status_code", 200
    )


@app.post("/api/logout")
def portal_logout():
    session.pop("portal_session", None)
    return jsonify({"status": "success", "message": "Sessao encerrada."})


@app.get("/api/subjects")
def list_subjects():
    return jsonify({"subjects": store.list_subjects(), "summary": store.summary()})


@app.post("/api/subjects")
def create_subject():
    payload = request.get_json(silent=True) or {}
    created = store.create_subject(payload)
    return jsonify(created), 201


@app.put("/api/subjects/<subject_id>")
def update_subject(subject_id: str):
    payload = request.get_json(silent=True) or {}
    updated = store.update_subject(subject_id, payload)
    return jsonify(updated)


@app.delete("/api/subjects/<subject_id>")
def delete_subject(subject_id: str):
    store.delete_subject(subject_id)
    return ("", 204)


@app.post("/api/sync")
def sync_from_portal():
    result = sync_service.fetch_periods(session.get("portal_session", {}))
    return jsonify(result), result.get("status_code", 200)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
