"""Сервер для «Баг-репорт REDSMS».

Отдаёт index.html на любой путь (/, /app/, /что-угодно), поэтому работает
из любой рабочей папки и за прокси, который добавляет префикс к адресу.

Запуск:
    python server.py --host 0.0.0.0 --port 8501
Порт также можно задать переменной окружения PORT.
"""
import argparse
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

INDEX = Path(__file__).resolve().parent / "index.html"


class Handler(BaseHTTPRequestHandler):
    def _send(self, with_body: bool) -> None:
        body = INDEX.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def do_GET(self) -> None:
        self._send(True)

    def do_HEAD(self) -> None:
        self._send(False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Баг-репорт REDSMS")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8501")))
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Баг-репорт REDSMS: http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
