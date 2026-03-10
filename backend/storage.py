from __future__ import annotations

import math
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "faltas.db"


class SubjectStore:
    def __init__(self) -> None:
        DATA_DIR.mkdir(exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(DB_PATH)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS subjects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    teacher TEXT DEFAULT '',
                    total_classes INTEGER NOT NULL,
                    absences INTEGER NOT NULL,
                    max_absence_percentage REAL NOT NULL DEFAULT 25,
                    notes TEXT DEFAULT '',
                    updated_at TEXT NOT NULL
                )
                """
            )

    def list_subjects(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM subjects ORDER BY name COLLATE NOCASE"
            ).fetchall()

        return [self._serialize(row) for row in rows]

    def create_subject(self, payload: dict[str, Any]) -> dict[str, Any]:
        subject = self._normalize(payload, subject_id=str(uuid.uuid4()))

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO subjects (
                    id, name, teacher, total_classes, absences,
                    max_absence_percentage, notes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    subject["id"],
                    subject["name"],
                    subject["teacher"],
                    subject["total_classes"],
                    subject["absences"],
                    subject["max_absence_percentage"],
                    subject["notes"],
                    subject["updated_at"],
                ),
            )

        return subject

    def update_subject(self, subject_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        existing = self._get_subject(subject_id)
        merged = {
            "name": payload.get("name", existing["name"]),
            "teacher": payload.get("teacher", existing["teacher"]),
            "totalClasses": payload.get("totalClasses", existing["total_classes"]),
            "absences": payload.get("absences", existing["absences"]),
            "maxAbsencePercentage": payload.get(
                "maxAbsencePercentage", existing["max_absence_percentage"]
            ),
            "notes": payload.get("notes", existing["notes"]),
        }
        normalized = self._normalize(merged, subject_id=subject_id)

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE subjects
                SET name = ?, teacher = ?, total_classes = ?, absences = ?,
                    max_absence_percentage = ?, notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    normalized["name"],
                    normalized["teacher"],
                    normalized["total_classes"],
                    normalized["absences"],
                    normalized["max_absence_percentage"],
                    normalized["notes"],
                    normalized["updated_at"],
                    subject_id,
                ),
            )

        return normalized

    def delete_subject(self, subject_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM subjects WHERE id = ?", (subject_id,))
            if cursor.rowcount == 0:
                raise ValueError("Disciplina nao encontrada.")

    def summary(self) -> dict[str, Any]:
        subjects = self.list_subjects()
        total_subjects = len(subjects)
        total_absences = sum(subject["absences"] for subject in subjects)
        total_remaining = sum(max(subject["remainingAbsences"], 0) for subject in subjects)
        risky_subjects = sum(
            1 for subject in subjects if subject["status"] in {"attention", "limit", "exceeded"}
        )

        return {
            "totalSubjects": total_subjects,
            "totalAbsences": total_absences,
            "totalRemainingAbsences": total_remaining,
            "riskySubjects": risky_subjects,
        }

    def _get_subject(self, subject_id: str) -> sqlite3.Row:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM subjects WHERE id = ?", (subject_id,)
            ).fetchone()

        if row is None:
            raise ValueError("Disciplina nao encontrada.")

        return row

    def _normalize(self, payload: dict[str, Any], subject_id: str) -> dict[str, Any]:
        name = str(payload.get("name", "")).strip()
        teacher = str(payload.get("teacher", "")).strip()
        notes = str(payload.get("notes", "")).strip()

        if not name:
            raise ValueError("Nome da disciplina e obrigatorio.")

        total_classes = self._to_int(payload.get("totalClasses", payload.get("total_classes", 0)))
        absences = self._to_int(payload.get("absences", 0))
        max_absence_percentage = self._to_float(
            payload.get(
                "maxAbsencePercentage",
                payload.get("max_absence_percentage", 25),
            )
        )

        if total_classes <= 0:
            raise ValueError("Total de aulas deve ser maior que zero.")
        if absences < 0:
            raise ValueError("Faltas nao podem ser negativas.")
        if max_absence_percentage <= 0 or max_absence_percentage > 100:
            raise ValueError("Percentual maximo deve ficar entre 1 e 100.")

        updated_at = datetime.now(UTC).isoformat()
        allowed_absences = math.floor(total_classes * (max_absence_percentage / 100))
        remaining_absences = allowed_absences - absences

        return {
            "id": subject_id,
            "name": name,
            "teacher": teacher,
            "total_classes": total_classes,
            "absences": absences,
            "max_absence_percentage": max_absence_percentage,
            "notes": notes,
            "updated_at": updated_at,
            "allowedAbsences": allowed_absences,
            "remainingAbsences": remaining_absences,
            "status": self._status_for(absences, allowed_absences, remaining_absences),
        }

    def _serialize(self, row: sqlite3.Row) -> dict[str, Any]:
        allowed_absences = math.floor(
            row["total_classes"] * (row["max_absence_percentage"] / 100)
        )
        remaining_absences = allowed_absences - row["absences"]

        return {
            "id": row["id"],
            "name": row["name"],
            "teacher": row["teacher"],
            "totalClasses": row["total_classes"],
            "absences": row["absences"],
            "maxAbsencePercentage": row["max_absence_percentage"],
            "notes": row["notes"],
            "updatedAt": row["updated_at"],
            "allowedAbsences": allowed_absences,
            "remainingAbsences": remaining_absences,
            "status": self._status_for(row["absences"], allowed_absences, remaining_absences),
        }

    @staticmethod
    def _to_int(value: Any) -> int:
        return int(value)

    @staticmethod
    def _to_float(value: Any) -> float:
        return float(value)

    @staticmethod
    def _status_for(absences: int, allowed_absences: int, remaining_absences: int) -> str:
        if absences > allowed_absences:
            return "exceeded"
        if remaining_absences == 0:
            return "limit"
        if allowed_absences == 0:
            return "attention"
        ratio = absences / allowed_absences
        if ratio >= 0.8:
            return "attention"
        return "healthy"
