"""Сервер для «Баг-репорт REDSMS».

- Отдаёт index.html на любой путь (работает из любой папки и за прокси с префиксом).
- Хранит баги в SQLite, вложения — файлами на диске, в папке DATA_DIR (по умолчанию ./data).
- Если задан APP_PASSWORD, весь сайт закрыт паролем (HTTP Basic Auth).

Запуск:
    python server.py --host 0.0.0.0 --port 8501

Переменные окружения:
    PORT          порт (если не указан --port), по умолчанию 8501
    DATA_DIR      папка с базой и вложениями, по умолчанию ./data
    APP_USER      логин (необязательно)
    APP_PASSWORD  пароль; без него сайт открыт всем, у кого есть адрес

API (пути работают и с префиксом, например /prefix/api/bugs):
    GET    /api/health          проверка, что это сервер REDSMS
    GET    /api/version         номер версии данных (меняется при каждой записи)
    GET    /api/bugs            все баги: {"version": n, "bugs": [{"id", "data"}]}
    POST   /api/bugs            завести баг: нужен только title, остальное заполнится само;
                                с "dedupe": true вернёт уже открытый баг с тем же заголовком
    GET    /api/bugs/<id>       один баг
    PUT    /api/bugs/<id>       создать или заменить баг (тело — JSON-объект)
    PATCH  /api/bugs/<id>       дописать поля в существующий баг
    DELETE /api/bugs/<id>       удалить баг
    POST   /api/files           загрузить файл (тело — содержимое, заголовок X-Filename)
    GET    /files/<id>          скачать вложение
"""
import argparse
import base64
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "index.html"
DATA_DIR = Path(os.environ.get("DATA_DIR") or ROOT / "data").resolve()
FILES_DIR = DATA_DIR / "files"
DB_PATH = DATA_DIR / "bugs.sqlite3"

MAX_JSON = 1 * 1024 * 1024
MAX_FILE = 20 * 1024 * 1024
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
FILE_ID_RE = re.compile(r"[a-f0-9]{32}")
# Типы, которые браузер может показать прямо на странице. Остальное отдаётся как скачивание,
# чтобы загруженный HTML или SVG не исполнялся на нашем домене.
INLINE_TYPES = {
    "image/png", "image/jpeg", "image/gif", "image/webp",
    "video/mp4", "video/webm", "video/quicktime",
    "application/pdf", "text/plain", "application/json",
}
WRITE_LOCK = threading.Lock()

SEVERITIES = {"blocker", "critical", "major", "minor", "trivial"}
PRIORITIES = {"high", "medium", "low"}
STATUSES = {"new", "in_progress", "fixed", "closed", "rejected"}
OPEN_STATUSES = {"new", "in_progress"}
REPROS = {"always", "often", "sometimes", "once"}
STANDS = {"test", "stage", "prod"}
ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def now_ms():
    return int(time.time() * 1000)


def new_bug_id():
    return "".join(secrets.choice(ID_ALPHABET) for _ in range(16))


def pick(value, allowed, default):
    return value if value in allowed else default


def text(value, limit=20000):
    if value is None:
        return ""
    if isinstance(value, list):
        value = "\n".join(str(v) for v in value)
    return str(value).strip()[:limit]


def normalize_new_bug(data):
    """Баг из внешнего источника (Postman, CI): дополняет поля до формата, который понимает страница."""
    title = text(data.get("title"), 200)
    if not title:
        raise HttpError(400, "Нужен заголовок бага (поле title)")
    env = data.get("env") if isinstance(data.get("env"), dict) else {}
    bug = {k: v for k, v in data.items() if k not in ("id", "dedupe")}
    ts = now_ms()
    bug.update(
        title=title,
        steps=text(data.get("steps")),
        expected=text(data.get("expected")),
        actual=text(data.get("actual")),
        workaround=text(data.get("workaround"), 2000),
        since=text(data.get("since"), 200),
        severity=pick(data.get("severity"), SEVERITIES, "major"),
        priority=pick(data.get("priority"), PRIORITIES, "medium"),
        repro=pick(data.get("repro"), REPROS, "always"),
        status=pick(data.get("status"), STATUSES, "new"),
        env={
            "version": text(env.get("version"), 200),
            "platform": text(env.get("platform"), 200),
            "stand": pick(env.get("stand"), STANDS, "test"),
            "account": text(env.get("account"), 200),
        },
        attachments=data.get("attachments") if isinstance(data.get("attachments"), list) else [],
        createdAt=ts,
        updatedAt=ts,
    )
    bug.setdefault("author", None)
    return bug


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


@contextmanager
def conn():
    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        with con:
            yield con
    finally:
        con.close()


def init_storage():
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    with conn() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE IF NOT EXISTS bugs (id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at REAL, updated_at REAL)")
        con.execute("CREATE TABLE IF NOT EXISTS files (id TEXT PRIMARY KEY, name TEXT, type TEXT, size INTEGER, created_at REAL)")
        con.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v INTEGER NOT NULL)")
        con.execute("INSERT OR IGNORE INTO meta VALUES ('version', 0)")


def data_version(con):
    return con.execute("SELECT v FROM meta WHERE k = 'version'").fetchone()[0]


def bump_version(con):
    con.execute("UPDATE meta SET v = v + 1 WHERE k = 'version'")


class Handler(BaseHTTPRequestHandler):
    server_version = "REDSMS"

    # ---------- общее ----------
    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def route(self):
        path = unquote(self.path.split("?", 1)[0])
        for marker in ("/api/", "/files/"):
            i = path.find(marker)
            if i >= 0:
                return path[i:].rstrip("/")
        return None

    def authorized(self):
        password = os.environ.get("APP_PASSWORD")
        if not password:
            return True
        login = os.environ.get("APP_USER", "")
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            user, _, pwd = base64.b64decode(header[6:]).decode("utf-8").partition(":")
        except Exception:
            return False
        ok_pwd = secrets.compare_digest(pwd.encode(), password.encode())
        ok_user = not login or secrets.compare_digest(user.encode(), login.encode())
        return ok_pwd and ok_user

    def send_bytes(self, status, body, ctype, extra=None, with_body=True):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def send_json(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_bytes(status, body, "application/json; charset=utf-8")

    def read_body(self, limit):
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise HttpError(413, "Слишком большой запрос")
        return self.rfile.read(length) if length else b""

    def read_object(self):
        try:
            obj = json.loads(self.read_body(MAX_JSON) or b"null")
        except json.JSONDecodeError:
            raise HttpError(400, "Тело запроса должно быть JSON")
        if not isinstance(obj, dict):
            raise HttpError(400, "Тело запроса должно быть JSON-объектом")
        return obj

    # ---------- методы ----------
    def do_GET(self):
        self.dispatch("GET")

    def do_HEAD(self):
        self.dispatch("HEAD")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def dispatch(self, method):
        if not self.authorized():
            body = "Нужен логин и пароль".encode("utf-8")
            return self.send_bytes(401, body, "text/plain; charset=utf-8",
                                   {"WWW-Authenticate": 'Basic realm="REDSMS", charset="UTF-8"'})
        try:
            self.handle_route(method, self.route())
        except HttpError as e:
            self.send_json(e.status, {"error": e.message})
        except Exception as e:  # noqa: BLE001
            print(f"Ошибка: {e!r}", flush=True)
            self.send_json(500, {"error": "Внутренняя ошибка сервера"})

    def handle_route(self, method, r):
        if r is None:
            if method not in ("GET", "HEAD"):
                raise HttpError(405, "Метод не поддерживается")
            return self.send_bytes(200, INDEX.read_bytes(), "text/html; charset=utf-8", with_body=method == "GET")

        if r == "/api/health":
            return self.send_json(200, {"app": "redsms", "ok": True})

        if r == "/api/version" and method == "GET":
            with conn() as con:
                return self.send_json(200, {"version": data_version(con)})

        if r == "/api/bugs" and method == "GET":
            with conn() as con:
                rows = con.execute("SELECT id, data FROM bugs").fetchall()
                return self.send_json(200, {"version": data_version(con),
                                            "bugs": [{"id": i, "data": json.loads(d)} for i, d in rows]})

        if r == "/api/bugs" and method == "POST":
            return self.handle_create()

        m = re.fullmatch(r"/api/bugs/([^/]+)", r)
        if m:
            bug_id = m.group(1)
            if not ID_RE.fullmatch(bug_id):
                raise HttpError(400, "Неверный id бага")
            return self.handle_bug(method, bug_id)

        if r == "/api/files" and method == "POST":
            return self.handle_upload()

        m = re.fullmatch(r"/files/([^/]+)", r)
        if m and method in ("GET", "HEAD"):
            return self.handle_file(m.group(1), method == "GET")

        raise HttpError(404, "Не найдено")

    def handle_create(self):
        data = self.read_object()
        bug = normalize_new_bug(data)
        with WRITE_LOCK, conn() as con:
            if data.get("dedupe"):
                for bid, raw in con.execute("SELECT id, data FROM bugs").fetchall():
                    old = json.loads(raw)
                    if old.get("title") == bug["title"] and old.get("status") in OPEN_STATUSES:
                        return self.send_json(200, {"id": bid, "duplicate": True, **old})
            bug_id = new_bug_id()
            con.execute("INSERT INTO bugs (id, data, created_at, updated_at) VALUES (?, ?, ?, ?)",
                        (bug_id, json.dumps(bug, ensure_ascii=False), time.time(), time.time()))
            bump_version(con)
        return self.send_json(201, {"id": bug_id, "duplicate": False, **bug})

    def handle_bug(self, method, bug_id):
        now = time.time()
        if method == "GET":
            with conn() as con:
                row = con.execute("SELECT data FROM bugs WHERE id = ?", (bug_id,)).fetchone()
            if not row:
                raise HttpError(404, "Баг не найден")
            return self.send_json(200, {"id": bug_id, **json.loads(row[0])})

        if method == "PUT":
            data = self.read_object()
            with WRITE_LOCK, conn() as con:
                con.execute(
                    "INSERT INTO bugs (id, data, created_at, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
                    (bug_id, json.dumps(data, ensure_ascii=False), now, now))
                bump_version(con)
            return self.send_json(200, data)

        if method == "PATCH":
            patch = self.read_object()
            with WRITE_LOCK, conn() as con:
                row = con.execute("SELECT data FROM bugs WHERE id = ?", (bug_id,)).fetchone()
                if not row:
                    raise HttpError(404, "Баг не найден")
                patch.setdefault("updatedAt", now_ms())
                merged = {**json.loads(row[0]), **patch}
                con.execute("UPDATE bugs SET data = ?, updated_at = ? WHERE id = ?",
                            (json.dumps(merged, ensure_ascii=False), now, bug_id))
                bump_version(con)
            return self.send_json(200, merged)

        if method == "DELETE":
            with WRITE_LOCK, conn() as con:
                con.execute("DELETE FROM bugs WHERE id = ?", (bug_id,))
                bump_version(con)
            self.send_response(204)
            self.end_headers()
            return None

        raise HttpError(405, "Метод не поддерживается")

    def handle_upload(self):
        body = self.read_body(MAX_FILE)
        if not body:
            raise HttpError(400, "Пустой файл")
        file_id = secrets.token_hex(16)
        name = unquote(self.headers.get("X-Filename") or "file")[:200]
        ctype = (self.headers.get("Content-Type") or "application/octet-stream").split(";")[0].strip().lower()
        (FILES_DIR / file_id).write_bytes(body)
        with WRITE_LOCK, conn() as con:
            con.execute("INSERT INTO files VALUES (?, ?, ?, ?, ?)", (file_id, name, ctype, len(body), time.time()))
        return self.send_json(200, {"id": file_id, "url": "files/" + file_id, "contentType": ctype, "sizeBytes": len(body)})

    def handle_file(self, file_id, with_body):
        if not FILE_ID_RE.fullmatch(file_id):
            raise HttpError(404, "Файл не найден")
        with conn() as con:
            row = con.execute("SELECT name, type FROM files WHERE id = ?", (file_id,)).fetchone()
        path = FILES_DIR / file_id
        if not row or not path.exists():
            raise HttpError(404, "Файл не найден")
        name, ctype = row
        extra = {"Cache-Control": "private, max-age=86400"}
        if ctype not in INLINE_TYPES:
            ctype = "application/octet-stream"
            safe = re.sub(r'["\\\r\n]', "_", name)
            extra["Content-Disposition"] = f'attachment; filename="{safe}"'
        self.send_bytes(200, path.read_bytes(), ctype, extra, with_body)


def main():
    parser = argparse.ArgumentParser(description="Баг-репорт REDSMS")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8501")))
    args = parser.parse_args()
    init_storage()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Баг-репорт REDSMS: http://{args.host}:{args.port}", flush=True)
    print(f"Данные: {DATA_DIR}", flush=True)
    if not os.environ.get("APP_PASSWORD"):
        print("Внимание: APP_PASSWORD не задан — сайт открыт всем, у кого есть адрес.", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
