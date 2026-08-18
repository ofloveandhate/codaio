"""
One declarative description of every endpoint the client calls.

Private, but imported by the tests. It exists so that three things that would
otherwise drift apart are written down once:

* the URL each `Coda` method builds, which the raw-API tests assert against;
* whether a call may be replayed after an inconclusive outcome, which the retry
  layer needs and which callers must never have to work out for themselves;
* the shape to compare against the published OpenAPI document, which is how a
  path that does not exist gets noticed.

That last one is not hypothetical. The mocked test suite asserts that codaio
calls the URL codaio intends to call, which is self-consistency rather than
correctness -- an endpoint can be wrong for years and every test still passes.

Paths use the API's own parameter names so they can be compared to the spec
directly. `args` gives the corresponding Python argument names, in the order the
placeholders appear, so a path can also be filled in from a method's keyword
arguments.
"""

from __future__ import annotations

import re
from typing import Dict, Tuple

import attr

from codaio.http import Idempotency

_PLACEHOLDER = re.compile(r"\{[^}]+\}")


@attr.s(auto_attribs=True, frozen=True)
class Endpoint:
    """How one `Coda` method maps onto the API."""

    #: HTTP verb.
    method: str
    #: Path template in the API's own parameter names, e.g. `/docs/{docId}/pages`.
    path: str
    #: Python argument names matching the path placeholders, in order.
    args: Tuple[str, ...] = ()
    #: Query parameters this method sends.
    params: Tuple[str, ...] = ()
    #: Whether the call may be replayed. See :class:`codaio.http.Idempotency`.
    idempotency: Idempotency = Idempotency.SAFE
    #: Status codes the API returns on success. Writes are mostly 202: accepted
    #: and queued, not applied.
    success: Tuple[int, ...] = (200,)

    def format(self, **kwargs) -> str:
        """Fill the placeholders from `args`, ignoring any other keyword given."""
        path = self.path
        for name in self.args:
            path = _PLACEHOLDER.sub(str(kwargs[name]), path, count=1)
        return path


_PAGING = ("limit", "pageToken")

ENDPOINTS: Dict[str, Endpoint] = {
    # -- Docs --------------------------------------------------------------
    "list_docs": Endpoint(
        "GET", "/docs",
        params=("isOwner", "query", "sourceDoc") + _PAGING,
    ),
    "create_doc": Endpoint(
        "POST", "/docs",
        idempotency=Idempotency.UNSAFE, success=(201,),
    ),
    "get_doc": Endpoint("GET", "/docs/{docId}", args=("doc_id",)),
    "delete_doc": Endpoint(
        "DELETE", "/docs/{docId}", args=("doc_id",),
        idempotency=Idempotency.IDEMPOTENT,
    ),

    # -- Pages (still spelled "section" in the method names) ---------------
    "list_sections": Endpoint(
        "GET", "/docs/{docId}/pages", args=("doc_id",), params=_PAGING,
    ),
    "get_section": Endpoint(
        "GET", "/docs/{docId}/pages/{pageIdOrName}",
        args=("doc_id", "section_id_or_name"),
    ),

    # -- Folders -----------------------------------------------------------
    # These two paths do not exist. Folders are a workspace-level concept
    # (`/folders`, `/folders/{folderId}`), so both of these can only ever 404.
    # Recorded as what the code does today rather than what it should do; the
    # conformance check is what turns this from a comment into a failure.
    "list_folders": Endpoint(
        "GET", "/docs/{docId}/folders", args=("doc_id",), params=_PAGING,
    ),
    "get_folder": Endpoint(
        "GET", "/docs/{docId}/folders/{folderIdOrName}",
        args=("doc_id", "folder_id_or_name"),
    ),

    # -- Tables and views --------------------------------------------------
    "list_tables": Endpoint(
        "GET", "/docs/{docId}/tables", args=("doc_id",),
        params=("tableTypes", "sortBy") + _PAGING,
    ),
    "get_table": Endpoint(
        "GET", "/docs/{docId}/tables/{tableIdOrName}",
        args=("doc_id", "table_id_or_name"),
    ),
    "list_views": Endpoint(
        "GET", "/docs/{docId}/tables", args=("doc_id",),
        params=("tableTypes",) + _PAGING,
    ),
    "get_view": Endpoint(
        "GET", "/docs/{docId}/tables/{tableIdOrName}",
        args=("doc_id", "view_id_or_name"),
    ),

    # -- Columns -----------------------------------------------------------
    "list_columns": Endpoint(
        "GET", "/docs/{docId}/tables/{tableIdOrName}/columns",
        args=("doc_id", "table_id_or_name"), params=("visibleOnly",) + _PAGING,
    ),
    "get_column": Endpoint(
        "GET", "/docs/{docId}/tables/{tableIdOrName}/columns/{columnIdOrName}",
        args=("doc_id", "table_id_or_name", "column_id_or_name"),
    ),

    # -- Rows --------------------------------------------------------------
    "list_rows": Endpoint(
        "GET", "/docs/{docId}/tables/{tableIdOrName}/rows",
        args=("doc_id", "table_id_or_name"),
        params=("query", "sortBy", "useColumnNames", "valueFormat", "visibleOnly",
                "syncToken") + _PAGING,
    ),
    # Unsafe whether or not keyColumns is given. Without it a replay inserts
    # duplicates; with it the set of rows a key matches can change between
    # attempts, so a replay is not the same operation.
    "upsert_row": Endpoint(
        "POST", "/docs/{docId}/tables/{tableIdOrName}/rows",
        args=("doc_id", "table_id_or_name"), params=("disableParsing",),
        idempotency=Idempotency.UNSAFE, success=(202,),
    ),
    "get_row": Endpoint(
        "GET", "/docs/{docId}/tables/{tableIdOrName}/rows/{rowIdOrName}",
        args=("doc_id", "table_id_or_name", "row_id_or_name"),
        params=("useColumnNames", "valueFormat"),
    ),
    # Idempotent when addressing a row by id, which is the normal case. Addressing
    # by *name* is not: the API selects an arbitrary match, so a replay may land
    # on a different row. Call sites that accept a name pass unsafe explicitly.
    "update_row": Endpoint(
        "PUT", "/docs/{docId}/tables/{tableIdOrName}/rows/{rowIdOrName}",
        args=("doc_id", "table_id_or_name", "row_id_or_name"),
        params=("disableParsing",),
        idempotency=Idempotency.IDEMPOTENT, success=(202,),
    ),
    "delete_row": Endpoint(
        "DELETE", "/docs/{docId}/tables/{tableIdOrName}/rows/{rowIdOrName}",
        args=("doc_id", "table_id_or_name", "row_id_or_name"),
        idempotency=Idempotency.IDEMPOTENT, success=(202,),
    ),

    # -- Formulas and controls --------------------------------------------
    "list_formulas": Endpoint(
        "GET", "/docs/{docId}/formulas", args=("doc_id",), params=_PAGING,
    ),
    "get_formula": Endpoint(
        "GET", "/docs/{docId}/formulas/{formulaIdOrName}",
        args=("doc_id", "formula_id_or_name"),
    ),
    "list_controls": Endpoint(
        "GET", "/docs/{docId}/controls", args=("doc_id",), params=_PAGING,
    ),
    "get_control": Endpoint(
        "GET", "/docs/{docId}/controls/{controlIdOrName}",
        args=("doc_id", "control_id_or_name"),
    ),

    # -- Miscellaneous -----------------------------------------------------
    "account": Endpoint("GET", "/whoami"),
    "resolve_browser_link": Endpoint(
        "GET", "/resolveBrowserLink", params=("url", "degradeGracefully"),
    ),
}
