#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GET /api/cron - innescata da Vercel Cron (vedi vercel.json) per l'analisi periodica."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _handler import BaseHandler, _core  # noqa: E402


class handler(BaseHandler):
    def do_GET(self):
        if _core.config.get("auto_run", True):
            _core.do_run()
        self._send(200, _core.state_snapshot())
