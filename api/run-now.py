#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""POST /api/run-now - esegue una analisi in modo SINCRONO e restituisce il risultato.

Su serverless non possiamo lanciare un thread in background che sopravviva alla
richiesta, quindi l'analisi gira dentro la richiesta. Il risultato torna nel body
cosi' la dashboard si aggiorna subito anche senza storage condiviso fra istanze."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _handler import BaseHandler, _core  # noqa: E402


class handler(BaseHandler):
    def do_POST(self):
        _core.do_run()
        self._send(200, {"ok": True, "state": _core.state_snapshot()})

    # comodo per testare via browser/cron anche in GET
    def do_GET(self):
        _core.do_run()
        self._send(200, {"ok": True, "state": _core.state_snapshot()})
