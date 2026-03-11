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
DEFAULT_PRESENCE_PERCENTAGE = 0.75
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
                    configured_hours INTEGER,
                    grade_entries TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (matricula, period_key, subject_name)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS annotation_history (
                    id TEXT PRIMARY KEY,
                    matricula TEXT NOT NULL,
                    period_key TEXT NOT NULL,
                    subject_name TEXT NOT NULL,
                    manual_absences INTEGER,
                    max_absences INTEGER,
                    configured_hours INTEGER,
                    grade_entries TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_settings (
                    matricula TEXT PRIMARY KEY,
                    absence_percentage REAL NOT NULL,
                    presence_percentage REAL NOT NULL,
                    class_period_minutes INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_annotation_columns(connection)

    def _ensure_annotation_columns(self, connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(subject_annotations)").fetchall()
        }
        if "configured_hours" not in columns:
            connection.execute(
                "ALTER TABLE subject_annotations ADD COLUMN configured_hours INTEGER"
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

    def get_settings(self, matricula: str) -> dict[str, Any]:
        if not matricula:
            return self._default_settings()

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT matricula, absence_percentage, presence_percentage,
                       class_period_minutes, updated_at
                FROM user_settings
                WHERE matricula = ?
                """,
                (matricula,),
            ).fetchone()

        if row is None:
            return self._default_settings()

        return self._serialize_settings_row(row)

    def update_settings(self, matricula: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not matricula:
            raise ValueError("Matricula da sessao nao encontrada.")

        current = self.get_settings(matricula)
        absence_percentage = self._to_optional_float(
            payload.get("absencePercentage", current["absencePercentage"])
        )
        class_period_minutes = self._to_optional_int(
            payload.get("classPeriodMinutes", current["classPeriodMinutes"])
        )

        if absence_percentage is None:
            absence_percentage = current["absencePercentage"]
        if class_period_minutes is None:
            class_period_minutes = current["classPeriodMinutes"]

        if absence_percentage <= 0 or absence_percentage >= 1:
            raise ValueError("O percentual de faltas deve ficar entre 0 e 1.")
        if class_period_minutes <= 0:
            raise ValueError("A duracao do periodo deve ser maior que zero.")

        updated_at = datetime.now(UTC).isoformat()
        presence_percentage = round(1 - absence_percentage, 4)

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO user_settings (
                    matricula, absence_percentage, presence_percentage,
                    class_period_minutes, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(matricula)
                DO UPDATE SET
                    absence_percentage = excluded.absence_percentage,
                    presence_percentage = excluded.presence_percentage,
                    class_period_minutes = excluded.class_period_minutes,
                    updated_at = excluded.updated_at
                """,
                (
                    matricula,
                    absence_percentage,
                    presence_percentage,
                    class_period_minutes,
                    updated_at,
                ),
            )

        return self.get_settings(matricula)

    def upsert_annotation(self, matricula: str, payload: dict[str, Any]) -> dict[str, Any]:
        settings = self.get_settings(matricula)
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
                        max_absences, configured_hours, grade_entries, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(matricula, period_key, subject_name)
                    DO UPDATE SET
                        manual_absences = excluded.manual_absences,
                        max_absences = excluded.max_absences,
                        configured_hours = excluded.configured_hours,
                        grade_entries = excluded.grade_entries,
                        updated_at = excluded.updated_at
                    """,
                    (
                        annotation["matricula"],
                        annotation["periodKey"],
                        annotation["subjectName"],
                        annotation["manualAbsences"],
                        annotation["maxAbsences"],
                        annotation["configuredHours"],
                        json.dumps(annotation["gradeEntries"]),
                        annotation["updatedAt"],
                    ),
                )
            connection.execute(
                """
                INSERT INTO annotation_history (
                    id, matricula, period_key, subject_name, manual_absences,
                    max_absences, configured_hours, grade_entries, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    annotation["matricula"],
                    annotation["periodKey"],
                    annotation["subjectName"],
                    annotation["manualAbsences"],
                    annotation["maxAbsences"],
                    annotation["configuredHours"],
                    json.dumps(annotation["gradeEntries"]),
                    annotation["updatedAt"],
                ),
            )

        history = self._list_annotation_history(matricula).get(
            self._annotation_key(annotation["periodKey"], annotation["subjectName"]),
            [],
        )

        return self._enrich_annotation(annotation, settings, history)

    def merge_periods_with_annotations(
        self,
        periods: list[dict[str, Any]],
        matricula: str,
    ) -> dict[str, Any]:
        settings = self.get_settings(matricula)
        annotations = self._list_annotations(matricula)
        history_map = self._list_annotation_history(matricula)
        merged_periods: list[dict[str, Any]] = []

        for period in periods:
            subjects: list[dict[str, Any]] = []
            for subject in period.get("subjects", []):
                display_name, subject_code = self._split_subject_name(subject.get("name", ""))
                annotation_key = self._annotation_key(period["key"], subject["name"])
                annotation = annotations.get(annotation_key)
                history = history_map.get(annotation_key, [])
                default_config = self._default_subject_config(display_name or subject["name"], settings)
                portal_absences = self._extract_int(subject.get("absences"))
                subjects.append(
                    self._build_subject_state(
                        {
                            **subject,
                            "displayName": display_name or subject.get("name", ""),
                            "subjectCode": subject_code,
                            "portalAbsences": portal_absences,
                        },
                        annotation,
                        history,
                        default_config,
                        settings,
                    )
                )

            merged_periods.append({**period, "subjects": subjects})

        return {"periods": merged_periods, "settings": settings}

    def _list_annotations(self, matricula: str) -> dict[str, dict[str, Any]]:
        if not matricula:
            return {}

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT matricula, period_key, subject_name, manual_absences,
                       max_absences, configured_hours, grade_entries, updated_at
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

    def _list_annotation_history(
        self,
        matricula: str,
        limit_per_subject: int = 6,
    ) -> dict[str, list[dict[str, Any]]]:
        if not matricula:
            return {}

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, matricula, period_key, subject_name, manual_absences,
                       max_absences, configured_hours, grade_entries, created_at
                FROM annotation_history
                WHERE matricula = ?
                ORDER BY created_at DESC
                """,
                (matricula,),
            ).fetchall()

        history_map: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            key = self._annotation_key(row["period_key"], row["subject_name"])
            entries = history_map.setdefault(key, [])
            if len(entries) >= limit_per_subject:
                continue
            entries.append(self._serialize_history_row(row))

        return history_map

    def _build_subject_state(
        self,
        subject: dict[str, Any],
        annotation: dict[str, Any] | None,
        history: list[dict[str, Any]],
        default_config: dict[str, Any] | None,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        portal_absences = self._extract_int(subject.get("portalAbsences", subject.get("absences")))
        manual_absences = annotation["manualAbsences"] if annotation else None
        tracked_absences = manual_absences if manual_absences is not None else portal_absences
        grade_entries = annotation["gradeEntries"] if annotation else []
        configured_hours = (
            annotation["configuredHours"]
            if annotation and annotation["configuredHours"] is not None
            else default_config["hours"]
            if default_config is not None
            else None
        )
        configured_periods = (
            math.floor((configured_hours * 60) / settings["classPeriodMinutes"])
            if configured_hours is not None and settings["classPeriodMinutes"] > 0
            else None
        )
        manual_max_absences = annotation["maxAbsences"] if annotation else None
        inferred_max_absences = (
            math.floor(configured_periods * settings["absencePercentage"])
            if configured_periods is not None
            else None
        )
        effective_max_absences = (
            manual_max_absences if manual_max_absences is not None else inferred_max_absences
        )
        remaining_absences = (
            effective_max_absences - tracked_absences
            if effective_max_absences is not None and tracked_absences is not None
            else None
        )
        grade_average = self._calculate_weighted_average(grade_entries)
        risk_level, risk_label, risk_message = self._risk_for_subject(
            tracked_absences,
            effective_max_absences,
        )

        return {
            **subject,
            "portalAbsences": portal_absences,
            "manualAbsences": manual_absences,
            "trackedAbsences": tracked_absences,
            "gradeEntries": grade_entries,
            "gradeAverage": grade_average,
            "configuredHours": configured_hours,
            "configuredPeriods": configured_periods,
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
            "riskLevel": risk_level,
            "riskLabel": risk_label,
            "riskMessage": risk_message,
            "history": history[:6],
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
        configured_hours = self._to_optional_int(payload.get("configuredHours"))
        if manual_absences is not None and manual_absences < 0:
            raise ValueError("Faltas anotadas nao podem ser negativas.")
        if max_absences is not None and max_absences < 0:
            raise ValueError("Limite de faltas nao pode ser negativo.")
        if configured_hours is not None and configured_hours <= 0:
            raise ValueError("A carga horaria precisa ser maior que zero.")

        grade_entries = self._normalize_grade_entries(payload.get("gradeEntries", []))
        updated_at = datetime.now(UTC).isoformat()

        return {
            "matricula": matricula,
            "periodKey": period_key,
            "subjectName": subject_name,
            "manualAbsences": manual_absences,
            "maxAbsences": max_absences,
            "configuredHours": configured_hours,
            "gradeEntries": grade_entries,
            "gradeAverage": self._calculate_weighted_average(grade_entries),
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
            "configuredHours": row["configured_hours"],
            "gradeEntries": grade_entries,
            "gradeAverage": self._calculate_weighted_average(grade_entries),
            "updatedAt": row["updated_at"],
        }

    def _serialize_history_row(self, row: sqlite3.Row) -> dict[str, Any]:
        grade_entries = self._normalize_grade_entries(row["grade_entries"])
        return {
            "id": row["id"],
            "manualAbsences": row["manual_absences"],
            "maxAbsences": row["max_absences"],
            "configuredHours": row["configured_hours"],
            "gradeEntries": grade_entries,
            "gradeAverage": self._calculate_weighted_average(grade_entries),
            "createdAt": row["created_at"],
        }

    def _default_subject_config(
        self,
        subject_name: str,
        settings: dict[str, Any],
    ) -> dict[str, int] | None:
        normalized_name = self._normalize_label(subject_name)
        for known_name, hours in DEFAULT_SUBJECT_HOURS.items():
            if known_name in normalized_name:
                class_periods = math.floor((hours * 60) / settings["classPeriodMinutes"])
                max_absences = math.floor(class_periods * settings["absencePercentage"])
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

    def _default_settings(self) -> dict[str, Any]:
        return {
            "absencePercentage": DEFAULT_ABSENCE_PERCENTAGE,
            "presencePercentage": DEFAULT_PRESENCE_PERCENTAGE,
            "classPeriodMinutes": CLASS_PERIOD_MINUTES,
            "updatedAt": None,
        }

    def _serialize_settings_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "absencePercentage": float(row["absence_percentage"]),
            "presencePercentage": float(row["presence_percentage"]),
            "classPeriodMinutes": int(row["class_period_minutes"]),
            "updatedAt": row["updated_at"],
        }

    def _enrich_annotation(
        self,
        annotation: dict[str, Any],
        settings: dict[str, Any],
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        display_name, subject_code = self._split_subject_name(annotation["subjectName"])
        default_config = self._default_subject_config(
            display_name or annotation["subjectName"],
            settings,
        )
        configured_hours = (
            annotation["configuredHours"]
            if annotation["configuredHours"] is not None
            else default_config["hours"]
            if default_config is not None
            else None
        )
        configured_periods = (
            math.floor((configured_hours * 60) / settings["classPeriodMinutes"])
            if configured_hours is not None
            else None
        )
        inferred_max_absences = (
            math.floor(configured_periods * settings["absencePercentage"])
            if configured_periods is not None
            else None
        )
        effective_max_absences = (
            annotation["maxAbsences"]
            if annotation["maxAbsences"] is not None
            else inferred_max_absences
        )
        remaining_absences = (
            effective_max_absences - annotation["manualAbsences"]
            if effective_max_absences is not None and annotation["manualAbsences"] is not None
            else None
        )
        risk_level, risk_label, risk_message = self._risk_for_subject(
            annotation["manualAbsences"],
            effective_max_absences,
        )
        return {
            **annotation,
            "displayName": display_name or annotation["subjectName"],
            "subjectCode": subject_code,
            "configuredHours": configured_hours,
            "configuredPeriods": configured_periods,
            "maxAbsences": effective_max_absences,
            "manualMaxAbsences": annotation["maxAbsences"],
            "remainingAbsences": remaining_absences,
            "maxAbsencesSource": (
                "manual"
                if annotation["maxAbsences"] is not None
                else "default"
                if inferred_max_absences is not None
                else "pending"
            ),
            "riskLevel": risk_level,
            "riskLabel": risk_label,
            "riskMessage": risk_message,
            "history": history[:6],
        }

    @staticmethod
    def _annotation_key(period_key: str, subject_name: str) -> str:
        return f"{period_key}::{subject_name}"

    @staticmethod
    def _annotation_is_empty(annotation: dict[str, Any]) -> bool:
        return (
            annotation["manualAbsences"] is None
            and annotation["maxAbsences"] is None
            and annotation["configuredHours"] is None
            and not annotation["gradeEntries"]
        )

    @staticmethod
    def _calculate_weighted_average(grade_entries: list[dict[str, Any]]) -> float | None:
        weighted_sum = 0.0
        total_weight = 0.0
        values_without_weight: list[float] = []

        for entry in grade_entries:
            value = SubjectStore._to_optional_float(entry.get("value"))
            weight = SubjectStore._to_optional_float(entry.get("weight"))
            if value is None:
                continue

            if weight is not None and weight > 0:
                weighted_sum += value * weight
                total_weight += weight
            else:
                values_without_weight.append(value)

        if total_weight > 0:
            for value in values_without_weight:
                weighted_sum += value
                total_weight += 1
            return round(weighted_sum / total_weight, 2)

        if not values_without_weight:
            return None

        return round(sum(values_without_weight) / len(values_without_weight), 2)

    @staticmethod
    def _normalize_grade_entries(value: Any) -> list[dict[str, Any]]:
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

        normalized_entries: list[dict[str, Any]] = []
        for index, entry in enumerate(raw_entries):
            if isinstance(entry, dict):
                label = str(entry.get("label", "")).strip() or f"Nota {index + 1}"
                normalized_entries.append(
                    {
                        "id": str(entry.get("id", "")).strip() or str(uuid.uuid4()),
                        "label": label,
                        "value": SubjectStore._string_or_none(entry.get("value")),
                        "weight": SubjectStore._string_or_none(entry.get("weight")),
                    }
                )
                continue

            cleaned = str(entry).strip()
            if cleaned:
                normalized_entries.append(
                    {
                        "id": str(uuid.uuid4()),
                        "label": f"Nota {index + 1}",
                        "value": cleaned,
                        "weight": "",
                    }
                )

        return normalized_entries

    @staticmethod
    def _split_subject_name(value: str) -> tuple[str, str | None]:
        cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
        match = re.search(r"\(\s*([A-Z]{2,}\d+)\s*\)\s*$", cleaned, re.IGNORECASE)
        if not match:
            return cleaned, None
        code = match.group(1).upper()
        display_name = cleaned[: match.start()].strip()
        return display_name, code

    @staticmethod
    def _risk_for_subject(
        tracked_absences: int | None,
        max_absences: int | None,
    ) -> tuple[str, str, str]:
        if max_absences is None:
            return (
                "pending",
                "Limite pendente",
                "Configure a carga horaria ou o limite dessa materia.",
            )
        if tracked_absences is None:
            return (
                "neutral",
                "Sem faltas anotadas",
                "Ainda nao ha faltas registradas manualmente nem encontradas no portal.",
            )
        remaining = max_absences - tracked_absences
        if remaining < 0:
            return (
                "exceeded",
                "Limite ultrapassado",
                "As faltas atuais ja passaram do limite configurado.",
            )
        if remaining == 0:
            return (
                "limit",
                "No limite",
                "Nao ha mais margem de faltas para essa disciplina.",
            )
        if remaining <= 2:
            return (
                "attention",
                "Atencao",
                "Restam poucas faltas disponiveis nessa disciplina.",
            )
        return (
            "healthy",
            "Sob controle",
            "A disciplina ainda esta com margem confortavel de faltas.",
        )

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
    def _string_or_none(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

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
    def _to_optional_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        normalized = str(value).strip().replace(",", ".")
        if not normalized:
            return None
        return float(normalized)

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
