#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""POST /api/config - aggiorna la configurazione.

Nota: su Vercel il salvataggio va su /tmp (effimero, per-istanza). Per impostazioni
durature usa le variabili d'ambiente (OPENROUTER_API_KEY, OPENROUTER_MODEL)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _handler import BaseHandler, _core  # noqa: E402


class handler(BaseHandler):
    def do_POST(self):
        cfg = _core.update_config(self._body())
        self._send(200, {"ok": True, "config": cfg})
