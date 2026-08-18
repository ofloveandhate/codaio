"""
Backwards-compatible re-exports.

Everything that used to live here now lives in focused modules; this module
keeps the historical import paths working. Prefer importing from `codaio`.
"""

from __future__ import annotations

from codaio.client import MAX_GET_LIMIT, Coda  # noqa: F401
from codaio.http import _DEFAULT_PORTS, _origin, assert_same_origin  # noqa: F401
from codaio.objects.base import CodaObject  # noqa: F401
from codaio.objects.document import Document, Folder  # noqa: F401
from codaio.objects.page import Section  # noqa: F401
from codaio.objects.table import Cell, Column, Row, Table  # noqa: F401
