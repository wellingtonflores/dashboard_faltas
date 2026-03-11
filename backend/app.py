from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory, session
from flask_cors import CORS
from flask_session import Session
from redis import Redis

from portal_sync import PortalSyncService
from storage import SubjectStore


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIST_DIR = BASE_DIR / "frontend" / "dist"

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
app.config["SESSION_COOKIE_SAMESITE"] = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
Session(app)

frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
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


@app.put("/api/annotations")
def save_subject_annotation():
    portal_session = session.get("portal_session") or {}
    matricula = str(portal_session.get("matricula", "")).strip()
    if not matricula:
        return jsonify({"message": "Faca login no portal antes de salvar anotacoes."}), 401

    payload = request.get_json(silent=True) or {}
    annotation = store.upsert_annotation(matricula, payload)
    return jsonify(annotation)


@app.get("/api/settings")
def get_user_settings():
    portal_session = session.get("portal_session") or {}
    matricula = str(portal_session.get("matricula", "")).strip()
    if not matricula:
        return jsonify({"message": "Faca login no portal antes de carregar configuracoes."}), 401

    return jsonify(store.get_settings(matricula))


@app.put("/api/settings")
def update_user_settings():
    portal_session = session.get("portal_session") or {}
    matricula = str(portal_session.get("matricula", "")).strip()
    if not matricula:
        return jsonify({"message": "Faca login no portal antes de salvar configuracoes."}), 401

    payload = request.get_json(silent=True) or {}
    settings = store.update_settings(matricula, payload)
    return jsonify(settings)


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
    portal_session = session.get("portal_session", {})
    result = sync_service.fetch_periods(portal_session)
    if result.get("status") == "success":
        matricula = str(portal_session.get("matricula", "")).strip()
        merged = store.merge_periods_with_annotations(
            result.get("periods", []),
            matricula,
        )
        result["periods"] = merged["periods"]
        result["settings"] = merged["settings"]
    return jsonify(result), result.get("status_code", 200)


@app.get("/")
def serve_frontend_index():
    return _serve_frontend_path("index.html")


@app.get("/<path:path>")
def serve_frontend(path: str):
    return _serve_frontend_path(path)


def _serve_frontend_path(path: str):
    if not FRONTEND_DIST_DIR.exists():
        return (
            jsonify(
                {
                    "message": "Frontend build nao encontrado. Rode `npm run build` em `frontend`.",
                }
            ),
            503,
        )

    target = FRONTEND_DIST_DIR / path
    if target.is_file():
        return send_from_directory(FRONTEND_DIST_DIR, path)

    return send_from_directory(FRONTEND_DIST_DIR, "index.html")


if __name__ == "__main__":
    app.run(debug=True, port=5000)
