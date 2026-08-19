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
        """
        Fill the placeholders from `args`, ignoring any other keyword given.

        >>> ENDPOINTS["get_row"].format(
        ...     doc_id="AbCDeFGH", table_id_or_name="grid-1", row_id_or_name="i-1"
        ... )
        '/docs/AbCDeFGH/tables/grid-1/rows/i-1'
        """
        path = self.path
        for name in self.args:
            path = _PLACEHOLDER.sub(str(kwargs[name]), path, count=1)
        return path


_PAGING = ("limit", "pageToken")

#: Formats `POST /pages/{id}/export` accepts.
PAGE_EXPORT_FORMATS = frozenset({"markdown", "html"})

#: How much of a cell's value a read returns. `simple` is the API's default and
#: is lossy: array values come back joined into a comma-delimited string.
VALUE_FORMATS = frozenset({"simple", "simpleWithArrays", "rich"})

#: Orders `GET /rows` accepts. `natural` is the order shown in the app, which
#: only applies to visible rows, so it implies visibleOnly.
ROW_SORT_ORDERS = frozenset({"createdAt", "updatedAt", "natural"})

#: Every id the API mints carries a type prefix. Used to tell an id from a name,
#: which matters because addressing by name selects an arbitrary match among
#: things sharing that name -- so a write addressed by name can never be replayed.
ID_PREFIXES = {
    "page": ("canvas-",),
    "table": ("grid-",),
    "column": ("c-",),
    "row": ("i-",),
    "folder": ("fl-",),
    "formula": ("f-",),
    "control": ("ctrl-",),
    "element": ("cl-",),
}


def looks_like_page_id(value: str) -> bool:
    """
    Whether this addresses a page by id rather than by name.

    Conservative on purpose: anything that is not recognisably an id is treated
    as a name, and a name is never safe to replay.

    >>> looks_like_page_id("canvas-IjkLmnO")
    True
    >>> looks_like_page_id("Launch Status")
    False
    """
    return isinstance(value, str) and value.startswith(ID_PREFIXES["page"])


def page_update_idempotency(page_id_or_name: str, data: Dict) -> Idempotency:
    """
    Whether one `PUT /pages/{id}` may be replayed. See `Coda.update_page`.

    Three arguments decide it, which is exactly why this cannot be a per-method
    constant and must not be left to callers.

    Renaming a page ends in the same state however many times it runs:

    >>> page_update_idempotency("canvas-1", {"name": "Launch"})
    <Idempotency.IDEMPOTENT: 'idempotent'>

    Appending content does not -- a replay adds it twice:

    >>> page_update_idempotency(
    ...     "canvas-1",
    ...     {"contentUpdate": {"insertionMode": "append", "canvasContent": {}}},
    ... )
    <Idempotency.UNSAFE: 'unsafe'>

    Nor does replacing one element, since the first attempt consumes the id the
    retry would need -- and a missing elementId means "the entire page":

    >>> page_update_idempotency(
    ...     "canvas-1",
    ...     {"contentUpdate": {"insertionMode": "replace", "elementId": "cl-9",
    ...                        "canvasContent": {}}},
    ... )
    <Idempotency.UNSAFE: 'unsafe'>

    And addressing a page by name is never replayable, because the API picks an
    arbitrary match among pages sharing that name:

    >>> page_update_idempotency("Launch Status", {"name": "Renamed"})
    <Idempotency.UNSAFE: 'unsafe'>
    """
    if not looks_like_page_id(page_id_or_name):
        # An arbitrary page among those sharing the name would be chosen, and it
        # need not be the same one twice.
        return Idempotency.UNSAFE

    update = (data or {}).get("contentUpdate")
    if not update:
        # Metadata only: name, subtitle, icon, cover, hidden. Full assignment.
        return Idempotency.IDEMPOTENT

    if update.get("insertionMode") != "replace":
        # append/prepend: a replay adds the content a second time.
        return Idempotency.UNSAFE

    if update.get("elementId"):
        # The first attempt consumes that element and its replacement gets fresh
        # ids, so on a replay the id is gone -- and a *missing* elementId is
        # documented as meaning "operate on the entire page". A retried paragraph
        # edit could therefore replace the whole page.
        return Idempotency.UNSAFE

    # Replacing all of a page's content with the same content converges.
    return Idempotency.IDEMPOTENT

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
    # Deprecated spellings, kept working. Pages were called sections when these
    # were named; the URLs have pointed at /pages for years.
    "list_sections": Endpoint(
        "GET", "/docs/{docId}/pages", args=("doc_id",), params=_PAGING,
    ),
    "get_section": Endpoint(
        "GET", "/docs/{docId}/pages/{pageIdOrName}",
        args=("doc_id", "section_id_or_name"),
    ),

    "list_pages": Endpoint(
        "GET", "/docs/{docId}/pages", args=("doc_id",), params=_PAGING,
    ),
    "get_page": Endpoint(
        "GET", "/docs/{docId}/pages/{pageIdOrName}",
        args=("doc_id", "page_id_or_name"),
    ),
    "create_page": Endpoint(
        "POST", "/docs/{docId}/pages", args=("doc_id",),
        idempotency=Idempotency.UNSAFE, success=(202,),
    ),
    # Idempotency here is computed per call by `page_update_idempotency`; this is
    # the value for the most common shape, a metadata-only update by id.
    "update_page": Endpoint(
        "PUT", "/docs/{docId}/pages/{pageIdOrName}",
        args=("doc_id", "page_id_or_name"),
        idempotency=Idempotency.IDEMPOTENT, success=(202,),
    ),
    "delete_page": Endpoint(
        "DELETE", "/docs/{docId}/pages/{pageIdOrName}",
        args=("doc_id", "page_id_or_name"),
        idempotency=Idempotency.IDEMPOTENT, success=(202,),
    ),
    "get_page_content": Endpoint(
        "GET", "/docs/{docId}/pages/{pageIdOrName}/content",
        args=("doc_id", "page_id_or_name"),
        params=("contentFormat",) + _PAGING,
    ),
    "delete_page_content": Endpoint(
        "DELETE", "/docs/{docId}/pages/{pageIdOrName}/content",
        args=("doc_id", "page_id_or_name"),
        idempotency=Idempotency.IDEMPOTENT, success=(202,),
    ),
    # No durable state changes, so a replay costs only a slot in the tightest
    # rate-limit bucket.
    "begin_page_export": Endpoint(
        "POST", "/docs/{docId}/pages/{pageIdOrName}/export",
        args=("doc_id", "page_id_or_name"),
        idempotency=Idempotency.IDEMPOTENT, success=(202,),
    ),
    "get_page_export": Endpoint(
        "GET", "/docs/{docId}/pages/{pageIdOrName}/export/{requestId}",
        args=("doc_id", "page_id_or_name", "request_id"),
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
    "delete_rows": Endpoint(
        "DELETE", "/docs/{docId}/tables/{tableIdOrName}/rows",
        args=("doc_id", "table_id_or_name"),
        idempotency=Idempotency.IDEMPOTENT, success=(202,),
    ),
    # A button can do anything its author wrote into it -- write elsewhere, call
    # a Pack action, send something -- so pressing it twice is never known to be
    # harmless.
    "push_button": Endpoint(
        "POST",
        "/docs/{docId}/tables/{tableIdOrName}/rows/{rowIdOrName}"
        "/buttons/{columnIdOrName}",
        args=("doc_id", "table_id_or_name", "row_id_or_name", "column_id_or_name"),
        idempotency=Idempotency.UNSAFE, success=(202,),
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
    "get_mutation_status": Endpoint(
        "GET", "/mutationStatus/{requestId}", args=("request_id",),
    ),
    "resolve_browser_link": Endpoint(
        "GET", "/resolveBrowserLink", params=("url", "degradeGracefully"),
    ),
}
