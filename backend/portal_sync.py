from __future__ import annotations

import os
import re
import time
import unicodedata
from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup


DEFAULT_LOGIN_URL = "https://portalaluno.ufcspa.edu.br/aluno/login.action?error="
DEFAULT_NOTES_URL = "https://portalaluno.ufcspa.edu.br/aluno/aluno/nota/nota.action"
DEBUG_DIR = Path(__file__).resolve().parent / "data" / "debug"


@dataclass
class PortalSyncService:
    def login(self, payload: dict[str, Any]) -> dict[str, Any]:
        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", "")).strip()
        fallback_matricula = str(payload.get("matricula", "")).strip()
        login_url = str(payload.get("loginUrl") or DEFAULT_LOGIN_URL).strip()
        notes_url = str(payload.get("notesUrl") or DEFAULT_NOTES_URL).strip()
        verify_ssl = self._read_verify_ssl(payload)

        if not username or not password:
            return {
                "status": "error",
                "status_code": 400,
                "message": "Informe usuario e senha para entrar.",
            }

        session = self._build_session(verify_ssl)

        try:
            login_response = session.get(login_url, timeout=30)
            login_response.raise_for_status()
            login_action = self._extract_login_action(login_response.text, login_url)

            auth_response = session.post(
                login_action,
                data={"j_username": username, "j_password": password},
                allow_redirects=True,
                timeout=30,
            )
            auth_response.raise_for_status()

            if "login.action" in auth_response.url and "j_security_check" not in auth_response.url:
                return {
                    "status": "error",
                    "status_code": 401,
                    "message": "O portal voltou para a tela de login. Verifique usuario e senha.",
                }

            portal_context = self._discover_portal_context(
                session=session,
                login_url=login_url,
                notes_url=notes_url,
                fallback_matricula=fallback_matricula,
            )
            internal_matricula = str(portal_context.get("matricula", "")).strip()
            if not internal_matricula:
                return {
                    "status": "error",
                    "status_code": 502,
                    "message": (
                        "Nao consegui identificar a matricula interna do portal automaticamente. "
                        "Se precisar, use o ajuste avancado."
                    ),
                }

            return {
                "status": "success",
                "status_code": 200,
                "message": "Login realizado com sucesso.",
                "portalSession": self._serialize_session(
                    session=session,
                    matricula=internal_matricula,
                    display_matricula=str(portal_context.get("displayMatricula", "")).strip(),
                    login_url=login_url,
                    notes_url=notes_url,
                    verify_ssl=verify_ssl,
                ),
            }
        except requests.exceptions.SSLError as error:
            return {
                "status": "error",
                "status_code": 502,
                "message": (
                    "Falha de certificado SSL ao acessar o portal. "
                    "Se for um ambiente confiavel, desative a verificacao SSL localmente."
                ),
                "debug": {"error": str(error)},
            }
        except requests.RequestException as error:
            return {
                "status": "error",
                "status_code": 502,
                "message": "Nao foi possivel acessar o portal da universidade.",
                "debug": {"error": str(error)},
            }

    def fetch_periods(self, portal_session: dict[str, Any]) -> dict[str, Any]:
        if not portal_session:
            return {
                "status": "error",
                "status_code": 401,
                "message": "Faca login no portal antes de sincronizar.",
            }

        session = self._restore_session(portal_session)
        matricula = str(portal_session.get("matricula", "")).strip()
        notes_url = str(portal_session.get("notesUrl") or DEFAULT_NOTES_URL).strip()

        if not matricula:
            return {
                "status": "error",
                "status_code": 400,
                "message": "Matricula ausente na sessao.",
            }

        try:
            notes_response = self._fetch_notes_page(session, notes_url, matricula)
            self._write_debug_html("periodos.html", notes_response.text)
            page_debug = self._describe_page(notes_response)

            if self._looks_like_login_page(notes_response.text, notes_response.url):
                print(f"Portal sync debug: {page_debug}")
                return {
                    "status": "error",
                    "status_code": 401,
                    "message": "A sessao do portal expirou ou voltou para a tela de login.",
                    "debug": page_debug,
                }

            periods = self._extract_periods(
                notes_response.text,
                notes_response.url,
                session,
            )
            student_info = self._extract_student_info(notes_response.text)

            if not periods:
                print(f"Portal sync debug: {page_debug}")
                return {
                    "status": "error",
                    "status_code": 502,
                    "message": "Nao foi possivel identificar os periodos na pagina de notas.",
                    "debug": page_debug,
                }

            return {
                "status": "success",
                "status_code": 200,
                "message": "Periodos e disciplinas carregados com sucesso.",
                "periods": periods,
                "studentInfo": student_info,
            }
        except requests.exceptions.SSLError as error:
            return {
                "status": "error",
                "status_code": 502,
                "message": "Falha de certificado SSL ao acessar a pagina de notas.",
                "debug": {"error": str(error)},
            }
        except requests.RequestException as error:
            return {
                "status": "error",
                "status_code": 502,
                "message": "Nao foi possivel carregar a pagina de notas.",
                "debug": {"error": str(error)},
            }

    def _fetch_notes_page(
        self,
        session: requests.Session,
        notes_url: str,
        matricula: str,
    ) -> requests.Response:
        index_url = urljoin(notes_url, "/aluno/index.action")
        attempts: list[tuple[str, dict[str, str]]] = [
            (
                index_url,
                {},
            ),
            (
                notes_url,
                {"Referer": index_url},
            ),
            (
                notes_url,
                {"Referer": index_url, "Cache-Control": "no-cache", "Pragma": "no-cache"},
            ),
        ]

        last_response: requests.Response | None = None

        for url, headers in attempts:
            if url == notes_url:
                response = session.get(
                    url,
                    params={"matricula": matricula},
                    headers=headers,
                    timeout=30,
                )
            else:
                response = session.get(url, headers=headers, timeout=30)

            response.raise_for_status()
            last_response = response

            if url != notes_url:
                continue

            if response.text.strip():
                return response

            time.sleep(1)

        if last_response is None:
            raise ValueError("Nao foi possivel carregar a pagina de notas.")

        return last_response

    @staticmethod
    def _build_session(verify_ssl: bool) -> requests.Session:
        if not verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        session = requests.Session()
        session.verify = verify_ssl
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                )
            }
        )
        return session

    def _restore_session(self, portal_session: dict[str, Any]) -> requests.Session:
        session = self._build_session(bool(portal_session.get("verifySsl", True)))
        for cookie in portal_session.get("cookies", []):
            session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie.get("domain"),
                path=cookie.get("path", "/"),
            )
        return session

    @staticmethod
    def _serialize_session(
        session: requests.Session,
        matricula: str,
        display_matricula: str,
        login_url: str,
        notes_url: str,
        verify_ssl: bool,
    ) -> dict[str, Any]:
        return {
            "matricula": matricula,
            "displayMatricula": display_matricula,
            "loginUrl": login_url,
            "notesUrl": notes_url,
            "verifySsl": verify_ssl,
            "cookies": [
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                }
                for cookie in session.cookies
            ],
        }

    def _discover_portal_context(
        self,
        session: requests.Session,
        login_url: str,
        notes_url: str,
        fallback_matricula: str,
    ) -> dict[str, str | None]:
        index_url = urljoin(login_url, "/aluno/index.action")
        candidates: list[tuple[str, str]] = []

        try:
            index_response = session.get(index_url, timeout=30)
            index_response.raise_for_status()
            candidates.append((index_response.url, index_response.text))
        except requests.RequestException:
            pass

        try:
            bare_notes = session.get(
                notes_url,
                headers={"Referer": index_url},
                allow_redirects=True,
                timeout=30,
            )
            bare_notes.raise_for_status()
            candidates.append((bare_notes.url, bare_notes.text))
        except requests.RequestException:
            pass

        internal_matricula = fallback_matricula or None
        student_info: dict[str, str | None] = {}

        for candidate_url, candidate_html in candidates:
            if internal_matricula is None:
                internal_matricula = self._extract_internal_matricula(candidate_url, candidate_html)

            if not student_info:
                student_info = self._extract_student_info(candidate_html)

            if internal_matricula and student_info:
                break

        return {
            "matricula": internal_matricula,
            "displayMatricula": student_info.get("displayMatricula"),
            "course": student_info.get("course"),
            "enrollmentPeriod": student_info.get("enrollmentPeriod"),
            "currentPeriod": student_info.get("currentPeriod"),
        }

    @staticmethod
    def _read_verify_ssl(payload: dict[str, Any]) -> bool:
        raw_value = payload.get("verifySsl", True)
        return str(raw_value).strip().lower() not in {"0", "false", "no"}

    @staticmethod
    def _extract_login_action(html: str, base_url: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form")
        if not form or not form.get("action"):
            raise ValueError("Nao encontrei o formulario de login no portal.")
        return urljoin(base_url, str(form["action"]))

    @staticmethod
    def _looks_like_login_page(html: str, url: str) -> bool:
        if "login.action" in url:
            return True

        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form")
        if not form:
            return False

        action = str(form.get("action", "")).lower()
        return "j_security_check" in action

    @staticmethod
    def _extract_internal_matricula(url: str, html: str) -> str | None:
        for source in (url, html):
            match = re.search(r"nota\.action\?matricula=(\d+)", source, re.IGNORECASE)
            if match:
                return match.group(1)

        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href", ""))
            match = re.search(r"[?&]matricula=(\d+)", href, re.IGNORECASE)
            if match:
                return match.group(1)

        return None

    @staticmethod
    def _extract_student_info(html: str) -> dict[str, str | None]:
        soup = BeautifulSoup(html, "html.parser")
        mapping = {
            "matricula": "displayMatricula",
            "curso": "course",
            "periodo de matricula": "enrollmentPeriod",
            "periodo atual": "currentPeriod",
        }
        result: dict[str, str | None] = {
            "displayMatricula": None,
            "course": None,
            "enrollmentPeriod": None,
            "currentPeriod": None,
        }

        for label_tag in soup.find_all("span", class_="label"):
            label = PortalSyncService._normalize_label(label_tag.get_text(" ", strip=True)).rstrip(":")
            key = mapping.get(label)
            if not key:
                continue

            parent = label_tag.parent
            if parent is None:
                continue

            text = parent.get_text(" ", strip=True).replace("\xa0", " ")
            label_text = label_tag.get_text(" ", strip=True).replace("\xa0", " ")
            value = re.sub(rf"^{re.escape(label_text)}\s*:?\s*", "", text, count=1).strip()
            if value:
                result[key] = value

        return result

    @staticmethod
    def _describe_page(response: requests.Response) -> dict[str, Any]:
        html = response.text
        url = response.url
        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        semester_matches = re.findall(r"\d+\.\s*Semestre\s*/\s*\d{4}", html, flags=re.IGNORECASE)
        accordion = soup.find(class_="accordionTurma")
        disciplina_header = soup.find(string=re.compile(r"disciplina", re.IGNORECASE))
        notas_icon = soup.find("img", src=re.compile(r"notas_icon", re.IGNORECASE))

        return {
            "url": url,
            "statusCode": response.status_code,
            "history": [item.status_code for item in response.history],
            "contentType": response.headers.get("Content-Type"),
            "title": title,
            "semesterPreview": semester_matches[:3],
            "firstAccordionLabel": accordion.get_text(" ", strip=True) if accordion else None,
            "hasDisciplinaHeader": bool(disciplina_header),
            "hasNotasIcon": bool(notas_icon),
            "bodyLength": len(html),
        }

    @staticmethod
    def _extract_periods(
        html: str,
        base_url: str,
        session: requests.Session | None = None,
    ) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        heading_pattern = re.compile(r"^\d+\.\s*Semestre\s*/\s*\d{4}$", re.IGNORECASE)
        periods: list[dict[str, Any]] = []
        seen_labels: set[str] = set()

        for tag in soup.find_all(True):
            label = tag.get_text(" ", strip=True)
            if not label or not heading_pattern.match(label):
                continue
            if label in seen_labels:
                continue

            table = tag.find_next("table")
            if table is None:
                continue

            subjects = PortalSyncService._extract_subject_rows_from_table(table, base_url, session)
            if not subjects:
                continue

            seen_labels.add(label)
            period_number, year = PortalSyncService._parse_period_label(label)
            periods.append(
                {
                    "key": f"{year}-{period_number}",
                    "label": label,
                    "year": year,
                    "semester": period_number,
                    "subjects": subjects,
                }
            )

        return periods

    @staticmethod
    def _extract_subject_rows_from_table(
        table: Any,
        base_url: str,
        session: requests.Session | None = None,
    ) -> list[dict[str, Any]]:
        rows = table.find_all("tr")
        subjects: list[dict[str, Any]] = []

        for row in rows:
            cells = row.find_all("td")
            if not cells:
                continue

            first_cell = cells[0].get_text(" ", strip=True)
            if not first_cell or first_cell.lower() == "disciplina":
                continue

            note_link = PortalSyncService._extract_note_link(row, base_url)
            note_data = (
                PortalSyncService._fetch_note_details(session, note_link, first_cell)
                if session is not None and note_link
                else {
                    "grades": [],
                    "absences": None,
                    "average": None,
                    "frequency": None,
                    "status": None,
                    "exam": None,
                    "finalConcept": None,
                }
            )
            subjects.append(
                {
                    "name": first_cell,
                    "noteUrl": note_link,
                    "grades": note_data["grades"],
                    "absences": note_data["absences"],
                    "average": note_data["average"],
                    "frequency": note_data["frequency"],
                    "status": note_data["status"],
                    "exam": note_data["exam"],
                    "finalConcept": note_data["finalConcept"],
                }
            )

        return subjects

    @staticmethod
    def _fetch_note_details(
        session: requests.Session,
        note_url: str,
        subject_name: str,
    ) -> dict[str, Any]:
        try:
            response = session.get(note_url, timeout=30)
            response.raise_for_status()
            PortalSyncService._write_debug_html(
                (
                    "nota_"
                    f"{PortalSyncService._safe_filename(subject_name)}_"
                    f"{PortalSyncService._safe_filename(note_url)}.html"
                ),
                response.text,
            )
            return PortalSyncService._parse_note_page(response.text)
        except requests.RequestException:
            return {
                "grades": [],
                "absences": None,
                "average": None,
                "frequency": None,
                "status": None,
                "exam": None,
                "finalConcept": None,
            }

    @staticmethod
    def _extract_note_link(row: Any, base_url: str) -> str | None:
        for image in row.find_all("img"):
            source = str(image.get("src", ""))
            if "notas_icon" not in source:
                continue

            anchor = image.find_parent("a")
            if anchor and anchor.get("href"):
                href = str(anchor["href"])
                extracted = PortalSyncService._extract_dialog_url(href, base_url)
                if extracted:
                    return extracted
                return urljoin(base_url, href)

        for anchor in row.find_all("a"):
            text = anchor.get_text(" ", strip=True).lower()
            if "ver notas" in text and anchor.get("href"):
                href = str(anchor["href"])
                extracted = PortalSyncService._extract_dialog_url(href, base_url)
                if extracted:
                    return extracted
                return urljoin(base_url, href)

        return None

    @staticmethod
    def _extract_dialog_url(href: str, base_url: str) -> str | None:
        match = re.search(r"loadDialog\(\s*'([^']+)'", href)
        if not match:
            return None
        return urljoin(base_url, match.group(1))

    @staticmethod
    def _parse_note_page(html: str) -> dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        grades: list[dict[str, str]] = PortalSyncService._extract_grade_table(soup)
        absences: str | None = None
        average: str | None = None
        frequency: str | None = None
        status: str | None = None
        exam: str | None = None
        final_concept: str | None = None
        seen_grade_pairs = {(item["label"], item["value"]) for item in grades}
        label_map = PortalSyncService._extract_label_value_blocks(soup)

        absences = absences or label_map.get("total de faltas") or label_map.get("faltas")
        status = status or label_map.get("situacao")
        average = average or label_map.get("media final") or label_map.get("media")
        final_concept = label_map.get("conceito final")
        frequency = frequency or label_map.get("frequencia")
        exam = exam or label_map.get("exame")

        for row in soup.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
            cells = [cell for cell in cells if cell]
            if not cells:
                continue

            normalized = [PortalSyncService._normalize_label(cell) for cell in cells]

            if len(cells) >= 2:
                for index in range(len(cells) - 1):
                    left = normalized[index]
                    right = cells[index + 1]

                    if absences is None and "falta" in left:
                        absences = right
                    if average is None and ("media final" in left or "media" in left or "nota final" in left):
                        average = right
                    if frequency is None and ("frequencia" in left or "presenca" in left):
                        frequency = right
                    if status is None and "situacao" in left:
                        status = right
                    if final_concept is None and "conceito final" in left:
                        final_concept = right
                    if (
                        exam is None
                        and left == "exame"
                        and right
                        and not PortalSyncService._looks_like_grade_label(
                            PortalSyncService._normalize_label(right)
                        )
                    ):
                        exam = right

                    if PortalSyncService._looks_like_grade_label(left) and right:
                        pair = (cells[index], right)
                        if pair not in seen_grade_pairs:
                            grades.append({"label": cells[index], "value": right})
                            seen_grade_pairs.add(pair)

            if len(cells) == 1:
                only = normalized[0]
                if absences is None and "faltas" in only:
                    absences = cells[0]
                if average is None and "media" in only:
                    average = cells[0]

        return {
            "grades": grades[:8],
            "absences": absences,
            "average": average,
            "frequency": frequency,
            "status": status,
            "exam": exam,
            "finalConcept": final_concept,
        }

    @staticmethod
    def _extract_grade_table(soup: BeautifulSoup) -> list[dict[str, str]]:
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue

            matrix = []
            for row in rows:
                cells = row.find_all(["th", "td"])
                matrix.append([cell.get_text(" ", strip=True) for cell in cells])

            flat_text = " ".join(" ".join(row) for row in matrix).lower()
            if "nota 1" not in flat_text and "media final" not in flat_text:
                continue

            expanded = [PortalSyncService._expand_row(row) for row in rows[:3]]
            if len(expanded) < 2:
                continue

            top = expanded[0]
            middle = expanded[1] if len(expanded) > 1 else []
            bottom = expanded[2] if len(expanded) > 2 else []

            size = max(len(top), len(middle), len(bottom))
            top += [""] * (size - len(top))
            middle += [""] * (size - len(middle))
            bottom += [""] * (size - len(bottom))

            grades: list[dict[str, str]] = []
            for index in range(size):
                value = bottom[index].strip()
                if not value:
                    continue

                parent = top[index].strip()
                child = middle[index].strip()
                if child and child.lower() != parent.lower():
                    label = f"{parent} - {child}" if parent else child
                else:
                    label = parent or child or f"Item {index + 1}"

                grades.append({"label": label, "value": value})

            if grades:
                return grades

        return []

    @staticmethod
    def _expand_row(row: Any) -> list[str]:
        expanded: list[str] = []
        for cell in row.find_all(["th", "td"]):
            text = cell.get_text(" ", strip=True)
            colspan = int(cell.get("colspan", 1) or 1)
            expanded.extend([text] * max(colspan, 1))
        return expanded

    @staticmethod
    def _extract_label_value_blocks(soup: BeautifulSoup) -> dict[str, str]:
        result: dict[str, str] = {}

        for wrapper in soup.find_all("div", class_="divInfo"):
            children = wrapper.find_all("div", recursive=False)
            if len(children) < 2:
                continue

            labels_container = children[0]
            values_container = children[1]

            label_divs = labels_container.find_all("div", recursive=False)
            value_divs = values_container.find_all("div", recursive=False)

            if label_divs and value_divs:
                labels = [
                    PortalSyncService._normalize_label(div.get_text(" ", strip=True))
                    for div in label_divs
                ]
                values = [
                    PortalSyncService._clean_optional_text(div.get_text(" ", strip=True))
                    for div in value_divs
                ]
            else:
                labels = [
                    PortalSyncService._normalize_label(text)
                    for text in labels_container.stripped_strings
                    if text and text.strip() != "\xa0"
                ]
                values = [
                    PortalSyncService._clean_optional_text(text)
                    for text in values_container.stripped_strings
                ]

            for label, value in zip(labels, values):
                clean_label = label.rstrip(":")
                result[clean_label] = value

        return result

    @staticmethod
    def _looks_like_grade_label(label: str) -> bool:
        keywords = {
            "nota",
            "grau",
            "avaliacao",
            "prova",
            "trabalho",
            "media",
            "final",
            "g1",
            "g2",
            "n1",
            "n2",
            "av1",
            "av2",
        }
        return any(keyword in label for keyword in keywords)

    @staticmethod
    def _normalize_label(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value)
        ascii_only = "".join(char for char in normalized if not unicodedata.combining(char))
        return re.sub(r"\s+", " ", ascii_only).strip().lower()

    @staticmethod
    def _clean_optional_text(value: str) -> str:
        return value.replace("\xa0", " ").strip()

    @staticmethod
    def _parse_period_label(label: str) -> tuple[int, int]:
        match = re.search(r"(\d+)\.\s*Semestre\s*/\s*(\d{4})", label, re.IGNORECASE)
        if not match:
            return (0, 0)
        return (int(match.group(1)), int(match.group(2)))

    @staticmethod
    def _write_debug_html(filename: str, html: str) -> None:
        if os.getenv("SAVE_DEBUG_HTML", "true").lower() != "true":
            return
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        (DEBUG_DIR / filename).write_text(html, encoding="utf-8")

    @staticmethod
    def _safe_filename(value: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_")
        digest = sha1(value.encode("utf-8")).hexdigest()[:10]
        base = normalized[:40] or "pagina"
        return f"{base}_{digest}"
