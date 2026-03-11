from __future__ import annotations

import json
import math
import re
import sqlite3
import unicodedata
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "faltas.db"
DEFAULT_ABSENCE_PERCENTAGE = 0.25
CLASS_PERIOD_MINUTES = 50
DEFAULT_SUBJECT_HOURS = {
    "praticas em audiologia basica ii": 15,
    "linguagem do adulto e idoso": 60,
    "leitura e escrita": 60,
    "informatica em saude": 30,
    "audiologia ii": 45,
}


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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS subject_annotations (
                    matricula TEXT NOT NULL,
                    period_key TEXT NOT NULL,
                    subject_name TEXT NOT NULL,
                    manual_absences INTEGER,
                    max_absences INTEGER,
                    grade_entries TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (matricula, period_key, subject_name)
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

    def upsert_annotation(self, matricula: str, payload: dict[str, Any]) -> dict[str, Any]:
        annotation = self._normalize_annotation(matricula, payload)

        with self._connect() as connection:
            if self._annotation_is_empty(annotation):
                connection.execute(
                    """
                    DELETE FROM subject_annotations
                    WHERE matricula = ? AND period_key = ? AND subject_name = ?
                    """,
                    (
                        annotation["matricula"],
                        annotation["periodKey"],
                        annotation["subjectName"],
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO subject_annotations (
                        matricula, period_key, subject_name, manual_absences,
                        max_absences, grade_entries, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(matricula, period_key, subject_name)
                    DO UPDATE SET
                        manual_absences = excluded.manual_absences,
                        max_absences = excluded.max_absences,
                        grade_entries = excluded.grade_entries,
                        updated_at = excluded.updated_at
                    """,
                    (
                        annotation["matricula"],
                        annotation["periodKey"],
                        annotation["subjectName"],
                        annotation["manualAbsences"],
                        annotation["maxAbsences"],
                        json.dumps(annotation["gradeEntries"]),
                        annotation["updatedAt"],
                    ),
                )

        return annotation

    def merge_periods_with_annotations(
        self,
        periods: list[dict[str, Any]],
        matricula: str,
    ) -> list[dict[str, Any]]:
        annotations = self._list_annotations(matricula)
        merged_periods: list[dict[str, Any]] = []

        for period in periods:
            subjects: list[dict[str, Any]] = []
            for subject in period.get("subjects", []):
                annotation_key = self._annotation_key(period["key"], subject["name"])
                annotation = annotations.get(annotation_key)
                default_config = self._default_subject_config(subject["name"])
                portal_absences = self._extract_int(subject.get("absences"))
                manual_absences = (
                    annotation["manualAbsences"] if annotation is not None else None
                )
                tracked_absences = (
                    manual_absences if manual_absences is not None else portal_absences
                )
                grade_entries = annotation["gradeEntries"] if annotation is not None else []
                grade_average = self._calculate_grade_average(grade_entries)
                manual_max_absences = (
                    annotation["maxAbsences"] if annotation is not None else None
                )
                inferred_max_absences = (
                    default_config["maxAbsences"] if default_config is not None else None
                )
                effective_max_absences = (
                    manual_max_absences
                    if manual_max_absences is not None
                    else inferred_max_absences
                )
                remaining_absences = (
                    effective_max_absences - tracked_absences
                    if effective_max_absences is not None and tracked_absences is not None
                    else None
                )

                subjects.append(
                    {
                        **subject,
                        "portalAbsences": portal_absences,
                        "manualAbsences": manual_absences,
                        "trackedAbsences": tracked_absences,
                        "gradeEntries": grade_entries,
                        "gradeAverage": grade_average,
                        "configuredHours": (
                            default_config["hours"] if default_config is not None else None
                        ),
                        "configuredPeriods": (
                            default_config["periods"] if default_config is not None else None
                        ),
                        "maxAbsences": effective_max_absences,
                        "manualMaxAbsences": manual_max_absences,
                        "remainingAbsences": remaining_absences,
                        "maxAbsencesSource": (
                            "manual"
                            if manual_max_absences is not None
                            else "default"
                            if inferred_max_absences is not None
                            else "pending"
                        ),
                    }
                )

            merged_periods.append({**period, "subjects": subjects})

        return merged_periods

    def _list_annotations(self, matricula: str) -> dict[str, dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT matricula, period_key, subject_name, manual_absences,
                       max_absences, grade_entries, updated_at
                FROM subject_annotations
                WHERE matricula = ?
                """,
                (matricula,),
            ).fetchall()

        return {
            self._annotation_key(row["period_key"], row["subject_name"]): self._serialize_annotation(
                row
            )
            for row in rows
        }

    def _normalize_annotation(self, matricula: str, payload: dict[str, Any]) -> dict[str, Any]:
        period_key = str(payload.get("periodKey", "")).strip()
        subject_name = str(payload.get("subjectName", "")).strip()

        if not matricula:
            raise ValueError("Matricula da sessao nao encontrada.")
        if not period_key:
            raise ValueError("Periodo da disciplina e obrigatorio.")
        if not subject_name:
            raise ValueError("Nome da disciplina e obrigatorio.")

        manual_absences = self._to_optional_int(payload.get("manualAbsences"))
        max_absences = self._to_optional_int(payload.get("maxAbsences"))
        if manual_absences is not None and manual_absences < 0:
            raise ValueError("Faltas anotadas nao podem ser negativas.")
        if max_absences is not None and max_absences < 0:
            raise ValueError("Limite de faltas nao pode ser negativo.")

        grade_entries = self._normalize_grade_entries(payload.get("gradeEntries", []))
        updated_at = datetime.now(UTC).isoformat()

        return {
            "matricula": matricula,
            "periodKey": period_key,
            "subjectName": subject_name,
            "manualAbsences": manual_absences,
            "maxAbsences": max_absences,
            "gradeEntries": grade_entries,
            "gradeAverage": self._calculate_grade_average(grade_entries),
            "updatedAt": updated_at,
        }

    def _serialize_annotation(self, row: sqlite3.Row) -> dict[str, Any]:
        grade_entries = self._normalize_grade_entries(row["grade_entries"])
        return {
            "matricula": row["matricula"],
            "periodKey": row["period_key"],
            "subjectName": row["subject_name"],
            "manualAbsences": row["manual_absences"],
            "maxAbsences": row["max_absences"],
            "gradeEntries": grade_entries,
            "gradeAverage": self._calculate_grade_average(grade_entries),
            "updatedAt": row["updated_at"],
        }

    def _default_subject_config(self, subject_name: str) -> dict[str, int] | None:
        normalized_name = self._normalize_label(subject_name)
        for known_name, hours in DEFAULT_SUBJECT_HOURS.items():
            if known_name in normalized_name:
                class_periods = math.floor((hours * 60) / CLASS_PERIOD_MINUTES)
                max_absences = math.floor(class_periods * DEFAULT_ABSENCE_PERCENTAGE)
                return {
                    "hours": hours,
                    "periods": class_periods,
                    "maxAbsences": max_absences,
                }
        return None

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
    def _annotation_key(period_key: str, subject_name: str) -> str:
        return f"{period_key}::{subject_name}"

    @staticmethod
    def _annotation_is_empty(annotation: dict[str, Any]) -> bool:
        return (
            annotation["manualAbsences"] is None
            and annotation["maxAbsences"] is None
            and not annotation["gradeEntries"]
        )

    @staticmethod
    def _calculate_grade_average(grade_entries: list[str]) -> float | None:
        values: list[float] = []
        for entry in grade_entries:
            cleaned = entry.replace(",", ".").strip()
            if not cleaned:
                continue
            try:
                values.append(float(cleaned))
            except ValueError:
                continue

        if not values:
            return None

        return round(sum(values) / len(values), 2)

    @staticmethod
    def _normalize_grade_entries(value: Any) -> list[str]:
        raw_entries = value
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            try:
                raw_entries = json.loads(stripped)
            except json.JSONDecodeError:
                raw_entries = [value]

        if not isinstance(raw_entries, list):
            raise ValueError("As notas devem ser enviadas como lista.")

        normalized_entries: list[str] = []
        for entry in raw_entries:
            cleaned = str(entry).strip()
            if cleaned:
                normalized_entries.append(cleaned)

        return normalized_entries

    @staticmethod
    def _extract_int(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, int):
            return value

        match = re.search(r"-?\d+", str(value))
        if not match:
            return None

        return int(match.group())

    @staticmethod
    def _normalize_label(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value)
        ascii_only = "".join(char for char in normalized if not unicodedata.combining(char))
        return re.sub(r"\s+", " ", ascii_only).strip().lower()

    @staticmethod
    def _to_int(value: Any) -> int:
        return int(value)

    @staticmethod
    def _to_float(value: Any) -> float:
        return float(value)

    @staticmethod
    def _to_optional_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        return int(value)

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
