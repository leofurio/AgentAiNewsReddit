#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AgentAiNewsReddit - server locale standalone
=============================================
Legge le news da piu' fonti ogni N minuti e le passa a un panel di agenti
OpenRouter che esprimono un giudizio (rialzo/ribasso) su indici e asset di borsa.

La logica vive in api/_core.py (condivisa con la funzione serverless di Vercel).
Questo file aggiunge solo il server HTTP + lo scheduler per l'uso in locale.

Solo libreria standard di Python: nessun pip install necessario.
Avvio:  python server.py    ->  http://localhost:8765

ATTENZIONE: strumento a scopo di studio/ricerca. NON e' consulenza finanziaria.
"""

import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Importa la logica condivisa (api/_core.py).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "api"))
import _core  # noqa: E402

PORT = _core.PORT
PUBLIC_DIR = _core.PUBLIC_DIR
config = _core.config
state = _core.state

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            import json
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # silenzia il logging di default

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/state":
            return self._send(200, _core.state_snapshot())
        if path == "/api/history":
            return self._send(200, _core.read_history())
        return self.serve_static(path)

    def serve_static(self, path):
        if path == "/" or path == "":
            path = "/index.html"
        safe = os.path.normpath(path).lstrip("\\/")
        full = os.path.join(PUBLIC_DIR, safe)
        if not full.startswith(PUBLIC_DIR) or not os.path.isfile(full):
            return self._send(404, {"error": "not found"})
        ext = os.path.splitext(full)[1].lower()
        ctype = CONTENT_TYPES.get(ext, "application/octet-stream")
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        import json
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            payload = {}

        if path == "/api/config":
            cfg = _core.update_config(payload)
            return self._send(200, {"ok": True, "config": cfg})
        if path == "/api/run-now":
            threading.Thread(target=_core.do_run, daemon=True).start()
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "not found"})


def main():
    t = threading.Thread(target=_core.scheduler_loop, daemon=True)
    t.start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("=" * 60)
    print(f"  AgentAiNewsReddit attivo su:  http://localhost:{PORT}")
    print(f"  Cartella dati: {_core.DATA_DIR}")
    if not config.get("openrouter_api_key"):
        print("  ⚠  Inserisci la tua OpenRouter API key nella pagina (Impostazioni).")
    print("  Strumento di studio - NON e' consulenza finanziaria.")
    print("=" * 60)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArresto...")
        server.shutdown()


if __name__ == "__main__":
    main()
