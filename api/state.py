#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GET /api/state - stato corrente + config pubblica."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _handler import BaseHandler, _core  # noqa: E402


class handler(BaseHandler):
    def do_GET(self):
        _core.load_state_from_disk()
        self._send(200, _core.state_snapshot())
