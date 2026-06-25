#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Helper condiviso dalle funzioni serverless di Vercel (api/*.py).

Importa la logica di _core (che vive nella stessa cartella) e offre una piccola
base BaseHTTPRequestHandler con la utility _send. Ogni rotta e' un file separato
in api/ (state.py, run-now.py, config.py, history.py, cron.py).
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

# _core.py vive accanto a questo file: rendilo importabile a runtime.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _core  # noqa: E402,F401  (riesportato per comodita')


class BaseHandler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {}

    def log_message(self, *args):
        pass
