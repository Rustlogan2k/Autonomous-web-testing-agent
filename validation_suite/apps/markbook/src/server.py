"""Markbook: a teacher's markbook for one class, and each student's view of published marks.

A WSGI application on the standard library's `wsgiref` server, with SQLite. Teacher pages
live under /teacher/..., the student's own page under /student/<s>. The markbook itself is
a server-rendered students x assessments matrix whose cells are edited in place: the cell
editor sends one PATCH and the server answers with the student's recomputed row (weighted
percentage, level) and the assessment's column (class average). Everything else is plain
HTML forms with post/redirect/get.

Environment: PORT (required), HOST, WTA_VOCAB, WTA_FIXED_NOW, WTA_DATA_DIR, WTA_TEST_HOOKS.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, quote
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

sys.path.insert(0, str(Path(__file__).resolve().parent))
import grades  # noqa: E402
import notes  # noqa: E402
import pages  # noqa: E402
import store  # noqa: E402
from pages import t  # noqa: E402
from store import highest_mark  # noqa: E402

APP_ID = "markbook"
HERE = Path(__file__).resolve().parent
pages.VOCAB.update(json.loads((HERE.parent / "vocab" / f"{os.environ.get('WTA_VOCAB', 'default')}.json")
                              .read_text(encoding="utf-8")))
CLOCK = dt.datetime.fromisoformat(os.environ.get("WTA_FIXED_NOW", "2026-06-10T09:00:00"))  # wta-clock
TEST_HOOKS = os.environ.get("WTA_TEST_HOOKS") == "1"
STATIC_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
MAX_MAXIMUM = 1000
WHOLE = re.compile(r"\d{1,9}")
HTML = "text/html; charset=utf-8"
#: What a form handler reports for the page it redirects to: (message, tone).
Outcome = tuple[str, str]


class Reply:
    def __init__(self, status: str, body: bytes = b"", content_type: str = HTML, location: str = "") -> None:
        self.status, self.body, self.content_type, self.location = status, body, content_type, location

    @classmethod
    def page(cls, body: bytes, status: str = "200 OK") -> "Reply":
        return cls(status, body)

    @classmethod
    def json(cls, payload: dict, status: str = "200 OK") -> "Reply":
        return cls(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    @classmethod
    def redirect(cls, location: str) -> "Reply":
        return cls("303 See Other", location=location)


class Request:
    def __init__(self, environ: dict) -> None:
        self.method = environ["REQUEST_METHOD"].upper()
        self.path = environ.get("PATH_INFO") or "/"
        self.session = ""
        self.query = {k: v[-1] for k, v in parse_qs(environ.get("QUERY_STRING", "")).items()}
        size = environ.get("CONTENT_LENGTH") or "0"
        self.raw = environ["wsgi.input"].read(int(size)) if size.isdigit() and int(size) else b""
        try:
            jar = SimpleCookie(environ.get("HTTP_COOKIE", ""))
            self.sid = jar["wta_sid"].value if "wta_sid" in jar else None
        except CookieError:
            self.sid = None

    def form(self) -> dict:
        text = self.raw.decode("utf-8", errors="replace")
        return {k: v[-1] for k, v in parse_qs(text, keep_blank_values=True).items()}

    def payload(self) -> dict | None:
        try:
            data = json.loads(self.raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError):
            return None
        return data if isinstance(data, dict) else None


# -- what the pages show -------------------------------------------------------------------------


def basis_of(assessments: list[dict]) -> str:
    """The maxima a markbook page was drawn with. A cell edit sent from a page drawn with other
    maxima is refused, so a page never mixes figures worked out against different maxima."""
    return ",".join(f'{a["id"]}:{a["max_marks"]}' for a in assessments)


def row_view(student: dict, scores: dict, assessments: list[dict]) -> dict:
    exact = grades.weighted(scores, assessments)
    rounded = grades.percent(exact) if exact is not None else None
    level = grades.level(rounded)
    return {"student": student, "scores": {a["id"]: scores.get(a["id"]) for a in assessments},
            "pct": grades.shown(exact), "level": "-" if level is None else str(level), "exact": exact}


def footer_view(rows: list[dict], assessments: list[dict]) -> dict:
    averages = {}
    for a in assessments:
        given = [r["scores"][a["id"]] for r in rows if r["scores"][a["id"]] is not None]
        averages[a["id"]] = grades.shown(grades.column_average(given, a["max_marks"]))
    complete = [r["exact"] for r in rows if r["exact"] is not None]
    return {"averages": averages, "class_average": grades.shown(grades.mean(complete))}


def matrix_rows(conn) -> tuple[list[dict], list[dict]]:
    assessments = store.assessments(conn)
    table = store.scores(conn)
    return assessments, [row_view(s, table[s["id"]], assessments) for s in store.students(conn)]


# -- handlers ------------------------------------------------------------------------------------


def classes_hub(conn) -> Reply:
    assessments = store.assessments(conn)
    entries = [{**c, "students": len(store.students(conn)), "assessments": len(assessments),
                "published": sum(1 for a in assessments if a["published"])} for c in store.classes(conn)]
    return Reply.page(pages.teacher_page(t("classes.title"), pages.classes_hub(entries)))


def markbook(conn, klass: dict) -> Reply:
    assessments, rows = matrix_rows(conn)
    body = pages.markbook(klass, assessments, rows, footer_view(rows, assessments), basis_of(assessments))
    return Reply.page(pages.teacher_page(t("markbook.title"), body, klass, script=True))


def save_cell(conn, request: Request, student_id: str, assessment_id: str) -> Reply:
    """PATCH one cell. Refusals are answered 200 with {ok: false, error} for the cell editor."""
    body = request.payload()
    if body is None:
        return Reply.json({"ok": False, "error": t("msg.bad_request")})
    assessments = store.assessments(conn)
    assessment = next(a for a in assessments if a["id"] == assessment_id)
    if body.get("basis") != basis_of(assessments):
        return Reply.json({"ok": False, "error": t("msg.stale")})
    text = str(body.get("mark") if body.get("mark") is not None else "").strip()
    if text == "":
        score = None  # an emptied cell clears the mark
    elif WHOLE.fullmatch(text) and int(text) <= assessment["max_marks"]:
        score = int(text)
    else:
        return Reply.json({"ok": False, "error": t("msg.mark_invalid").format(max=assessment["max_marks"])})
    store.set_mark(conn, student_id, assessment_id, score)
    assessments, rows = matrix_rows(conn)
    row = next(r for r in rows if r["student"]["id"] == student_id)
    footer = footer_view(rows, assessments)
    return Reply.json({
        "ok": True,
        "row": {"student": student_id, "scores": row["scores"], "pct": row["pct"], "level": row["level"]},
        "column": {"assessment": assessment_id, "average": footer["averages"][assessment_id]},
        "class_average": footer["class_average"],
    })


def assessment_page(conn, klass: dict, a: dict) -> Reply:
    marked = sum(1 for marks in store.scores(conn).values() if a["id"] in marks)
    body = pages.assessment_page(klass, a, marked, len(store.students(conn)),
                                 highest_mark(conn, a["id"]), store.take_flash(conn, "assessment"))
    return Reply.page(pages.teacher_page(a["title"], body, klass))


def save_maximum(conn, request: Request, assessment_id: str) -> Outcome:
    raw = request.form().get("max_marks", "").strip()
    if not WHOLE.fullmatch(raw) or not 1 <= int(raw) <= MAX_MAXIMUM:
        return t("msg.max_invalid"), "error"
    maximum = int(raw)
    if maximum == store.assessment(conn, assessment_id)["max_marks"]:
        return t("msg.max_same").format(max=maximum), "ok"
    highest = highest_mark(conn, assessment_id, published_only=False)
    if highest is not None and maximum < highest:
        return t("msg.max_below").format(highest=highest), "error"
    store.set_maximum(conn, assessment_id, maximum)
    return t("msg.max_saved"), "ok"


def set_publication(conn, assessment: dict, publish: bool) -> Outcome:
    if assessment["published"] == publish:
        return (t("msg.already_published") if publish else t("msg.not_published")), "error"
    store.set_published(conn, assessment["id"], publish)
    return (t("msg.published") if publish else t("msg.withdrawn")), "ok"


def record_page(conn, request: Request, klass: dict, student: dict) -> Reply:
    assessments = store.assessments(conn)
    scores = store.scores(conn)[student["id"]]
    row = row_view(student, scores, assessments)
    note = notes.private_note(conn, student["id"])
    body = pages.record_page(klass, student, assessments, scores, row["pct"], row["level"],
                             notes.feedback(conn, student["id"]), store.comment_bank(conn), note,
                             request.query.get("note") == "edit", store.take_flash(conn, "record"))
    return Reply.page(pages.teacher_page(student["name"], body, klass))


def save_feedback(conn, request: Request, student: dict) -> Outcome:
    chosen = request.form().get("feedback", "")
    entry = next((c for c in store.comment_bank(conn) if c["id"] == chosen), None)
    if entry is None:
        return t("msg.feedback_choose"), "error"
    notes.give_feedback(conn, student["id"], entry["id"], entry["body"])
    return t("msg.feedback_saved").format(name=student["name"]), "ok"


def save_note(conn, request: Request, student: dict) -> Outcome:
    body = request.form().get("private_note", "").replace("\r\n", "\n")
    if not body.strip():
        return t("msg.note_empty"), "error"
    if len(body) > notes.MAX_NOTE:
        return t("msg.note_long"), "error"
    notes.save_private_note(conn, student["id"], body)
    return t("msg.note_saved"), "ok"


def report_page(conn, klass: dict) -> Reply:
    _assessments, rows = matrix_rows(conn)
    by_level = {value: [] for value in (5, 4, 3, 2, 1)}
    incomplete = []
    for row in rows:
        if row["level"] == "-":
            incomplete.append(row["student"]["name"])
        else:
            by_level[int(row["level"])].append(row["student"]["name"])
    body = pages.report_page(klass, list(by_level.items()), incomplete, CLOCK.date().isoformat())
    return Reply.page(pages.teacher_page(t("report.title"), body, klass))


def student_view(conn, klass: dict, student: dict) -> Reply:
    assessments = store.assessments(conn)
    scores = store.scores(conn)[student["id"]]
    published = [{**a, "score": scores.get(a["id"]), "top": highest_mark(conn, a["id"], published_only=True)}
                 for a in assessments if a["published"]]
    body = pages.student_view(klass, student, published, grades.shown(grades.provisional(scores, assessments)),
                              notes.student_comments(conn, student["id"]))
    record = f'/teacher/classes/{quote(klass["id"])}/students/{quote(student["id"])}'
    return Reply.page(pages.student_page(student["name"], body, student, record))


def missing() -> Reply:
    return Reply.page(pages.teacher_page(t("missing.title"), pages.not_found()), "404 Not Found")


# -- routing -------------------------------------------------------------------------------------

CLASS = r"/teacher/classes/(?P<c>[\w-]+)"
ROUTES = [
    ("GET", re.compile(r"/teacher"), "hub"),
    ("GET", re.compile(CLASS), "markbook"),
    ("PATCH", re.compile(CLASS + r"/marks/(?P<s>[\w-]+)/(?P<a>[\w-]+)"), "cell"),
    ("GET", re.compile(CLASS + r"/report"), "report"),
    ("GET", re.compile(CLASS + r"/assessments/(?P<a>[\w-]+)"), "assessment"),
    ("POST", re.compile(CLASS + r"/assessments/(?P<a>[\w-]+)/maximum"), "maximum"),
    ("POST", re.compile(CLASS + r"/assessments/(?P<a>[\w-]+)/publish"), "publish"),
    ("POST", re.compile(CLASS + r"/assessments/(?P<a>[\w-]+)/withdraw"), "withdraw"),
    ("GET", re.compile(CLASS + r"/students/(?P<s>[\w-]+)"), "record"),
    ("POST", re.compile(CLASS + r"/students/(?P<s>[\w-]+)/feedback"), "feedback"),
    ("POST", re.compile(CLASS + r"/students/(?P<s>[\w-]+)/note"), "note"),
    ("GET", re.compile(r"/student/(?P<s>[\w-]+)"), "student"),
    ("GET", re.compile(r"/help"), "help"),
]


def dispatch(conn, request: Request) -> Reply:  # noqa: C901, PLR0911 - one flat routing table
    if request.method == "GET" and request.path == "/":
        return Reply.redirect("/teacher")
    if request.method == "GET" and request.path == "/__wta/state":
        if not TEST_HOOKS:
            return missing()
        committed = store.committed(conn)
        sha = hashlib.sha256(json.dumps(committed, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return Reply.json({"session": request.session, "committed": committed, "sha": sha})
    for method, pattern, name in ROUTES:
        match = pattern.fullmatch(request.path)
        if match and method == request.method:
            break
    else:
        return missing()
    found = match.groupdict()
    klass = store.the_class(conn, found["c"]) if "c" in found else store.classes(conn)[0]
    student = store.student(conn, found["s"]) if "s" in found else None
    assessment = store.assessment(conn, found["a"]) if "a" in found else None
    if klass is None or ("s" in found and student is None) or ("a" in found and assessment is None):
        return missing() if name != "cell" else Reply.json({"ok": False, "error": t("msg.bad_request")}, "404 Not Found")
    base = f'/teacher/classes/{quote(klass["id"])}'
    if name == "hub":
        return classes_hub(conn)
    if name == "markbook":
        return markbook(conn, klass)
    if name == "cell":
        return save_cell(conn, request, student["id"], assessment["id"])
    if name == "report":
        return report_page(conn, klass)
    if name == "assessment":
        return assessment_page(conn, klass, assessment)
    if name in ("maximum", "publish", "withdraw"):
        outcome = (save_maximum(conn, request, assessment["id"]) if name == "maximum"
                   else set_publication(conn, assessment, name == "publish"))
        store.push_flash(conn, "assessment", *outcome)
        return Reply.redirect(f'{base}/assessments/{quote(assessment["id"])}')
    if name == "record":
        return record_page(conn, request, klass, student)
    if name in ("feedback", "note"):
        handler = save_feedback if name == "feedback" else save_note
        store.push_flash(conn, "record", *handler(conn, request, student))
        return Reply.redirect(f'{base}/students/{quote(student["id"])}')
    if name == "student":
        return student_view(conn, klass, student)
    return Reply.page(pages.teacher_page(t("help.title"), pages.help_page()))


# -- WSGI ----------------------------------------------------------------------------------------


def create_app(data_dir: Path):
    sessions = store.Sessions(data_dir)
    lock = threading.Lock()

    def static(name: str) -> Reply:
        target = (HERE / "static" / name).resolve()
        if target.parent != HERE / "static" or target.suffix not in STATIC_TYPES or not target.is_file():
            return Reply("404 Not Found", b"Not found", "text/plain; charset=utf-8")
        return Reply("200 OK", target.read_bytes(), STATIC_TYPES[target.suffix])

    def application(environ, start_response):
        request = Request(environ)
        cookie = None
        if request.path == "/healthz":
            reply = Reply.json({"ok": True, "app": APP_ID})
        elif request.path.startswith("/static/"):
            reply = static(request.path[len("/static/"):])
        else:
            with lock:
                sid, conn, created = sessions.open(request.sid)
                request.session = sid
                try:
                    reply = dispatch(conn, request)
                    conn.commit()
                finally:
                    conn.close()
            if created:
                cookie = f"wta_sid={sid}; Path=/; HttpOnly; SameSite=Lax"
        headers = [("Content-Type", reply.content_type), ("Content-Length", str(len(reply.body))),
                   ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff")]
        if reply.location:
            headers.append(("Location", reply.location))
        if cookie:
            headers.append(("Set-Cookie", cookie))
        start_response(reply.status, headers)
        return [reply.body]

    return application


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    """A browser may open a connection it does not use at once; one thread per connection
    keeps such a connection from holding up the others. Requests still run one at a time."""

    daemon_threads = True


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - keep the server log for real problems
        return


def main() -> None:
    data_dir = Path(os.environ.get("WTA_DATA_DIR") or tempfile.mkdtemp(prefix="markbook-"))
    data_dir.mkdir(parents=True, exist_ok=True)
    app = create_app(data_dir)
    host, port = os.environ.get("HOST", "127.0.0.1"), int(os.environ["PORT"])
    with make_server(host, port, app, server_class=ThreadingWSGIServer, handler_class=QuietHandler) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    main()
