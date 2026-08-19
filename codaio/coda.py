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
from codaio.objects.page import (  # noqa
    CanvasContent,
    ContentItem,
    EmbedContent,
    Page,
    PageExport,
    PageTree,
    SyncPageContent,
)
from codaio.objects.mutation import Mutation, MutationGroup  # noqa: F401
from codaio.objects.table import Cell, Column, Row, Table  # noqa: F401


def __getattr__(name):
    """`Section` is `Page`; using the old name says so once per import site."""
    if name == "Section":
        import warnings

        warnings.warn(
            "codaio.coda.Section is deprecated; use codaio.Page. Section is Page, "
            "so isinstance checks keep working.",
            DeprecationWarning,
            stacklevel=2,
        )
        return Page
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
