#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GET /api/history - storico delle ultime analisi."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _handler import BaseHandler, _core  # noqa: E402


class handler(BaseHandler):
    def do_GET(self):
        self._send(200, _core.read_history())
