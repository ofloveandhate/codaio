"""
The raw API client: one method per endpoint, each returning a plain dict.
"""

from __future__ import annotations

from typing import Dict, Iterator

import attr
import requests

from codaio import credentials
from codaio._endpoints import ENDPOINTS
from codaio.http import (
    DEFAULT_RETRY_POLICY,
    Idempotency,
    RetryPolicy,
    assert_same_origin,
    raise_for_status,
    run_with_retry,
)

# Previously this module read a `.env` from the current working directory at
# import time, mutating the whole process environment as a side effect of
# `import codaio`. That is now opt-in via CODAIO_DOTENV.
credentials.maybe_load_dotenv()

#: Historical constant, kept because it has always been importable. It is no
#: longer applied as a silent cap: the API documents that its own maximum page
#: size may change at any time and differs per endpoint, so rewriting a caller's
#: `limit` down to a guess meant asking for 300 results, receiving 200, and being
#: given no indication of the difference. The server's answer is the honest one.
MAX_GET_LIMIT = 200


@attr.s(eq=True, hash=False)
class Coda:
    """
    Raw API client.

    It is used in `codaio` objects like Document to access the raw API endpoints.
    Can also be used by itself to access Raw API.

    With no arguments the API token is resolved from the environment or the
    OS keyring; see :mod:`codaio.credentials`.

    `keyring_service` and `keyring_profile` are the two coordinates the
    `keyring` package uses to address an entry -- its service name and its
    username. They are named that way to make clear they are keyring
    addressing, not concepts `Coda` itself has. Use them to keep a different
    token per docset::

        python -m keyring set codaio research
        coda = Coda(keyring_profile="research")
    """

    # Named with a leading underscore so attrs takes `api_key` as the init
    # argument while the token itself never lives in an attrs field: see
    # __attrs_post_init__.
    _api_key: str = attr.ib(default=None, repr=False, eq=False)
    href: str = attr.ib(default=None, repr=False)
    keyring_profile: str = attr.ib(default=None)
    keyring_service: str = attr.ib(default=None)
    source: str = attr.ib(default=None, init=False, eq=False, repr=False)
    # How persistently to retry; `None` disables retrying entirely. Excluded from
    # equality because it is an operational setting, not part of what this client
    # identifies -- two clients pointed at the same account are the same client
    # whether or not one of them is more patient.
    retry: RetryPolicy = attr.ib(default=DEFAULT_RETRY_POLICY, eq=False, repr=False)

    @classmethod
    def from_environment(cls, keyring_profile: str = None) -> Coda:
        """
        Instantiates Coda using a stored API key.

        Historically this read only the `CODA_API_KEY` environment variable.
        It now goes through the full resolution chain, so it additionally
        finds a token in the OS keyring. Every case that worked before still
        works identically; plain `Coda()` is the preferred spelling now.

        :param keyring_profile: which stored token to use.

        :return:
        """
        return cls(keyring_profile=keyring_profile)

    def __attrs_post_init__(self):
        token, self.source = credentials.get_api_key_with_source(
            self._api_key,
            keyring_profile=self.keyring_profile,
            keyring_service=self.keyring_service,
        )
        # Keep the token out of every attrs field. `attr.asdict()` reads
        # fields directly and ignores repr=False, so a token left in one
        # would be exposed by `attr.asdict(some_document)` recursing into
        # the Coda it holds.
        self._api_key = None
        self._token = token

        self.keyring_profile = credentials.default_keyring_profile(self.keyring_profile)
        self.keyring_service = credentials.default_keyring_service(self.keyring_service)
        self.href = credentials.resolve_endpoint(self.href)

    @property
    def api_key(self) -> str:
        """The resolved API token."""
        return self._token

    @api_key.setter
    def api_key(self, value: str):
        self._token = value

    @property
    def authorization(self) -> Dict:
        """The Authorization header sent with every request."""
        return {"Authorization": f"Bearer {self.api_key}"}

    # ----------------------------------------------------------------------
    # The single point every request goes through
    # ----------------------------------------------------------------------

    def _request(
        self,
        method: str,
        url: str,
        *,
        idempotency: Idempotency,
        params: Dict = None,
        json: Dict = None,
    ) -> Dict:
        """
        Perform one API call and return its parsed body.

        Every request the client makes goes through here, so the retry policy,
        the status-to-exception mapping and the Authorization header are applied
        in exactly one place. `idempotency` says whether this particular call may
        be replayed; see :class:`codaio.http.Idempotency` for why that depends on
        the arguments and not just the verb.
        """
        policy = self.retry
        timeout = policy.timeout if policy else 30.0

        def send():
            return requests.request(
                method,
                url,
                params=params,
                json=json,
                headers=self.authorization,
                timeout=timeout,
            )

        response = run_with_retry(
            send, idempotency=idempotency, policy=policy, method=method, url=url
        )
        raise_for_status(response, method=method, url=url)
        return self._body(response)

    @staticmethod
    def _body(response) -> Dict:
        """
        The response body as a dict, or a status stub when there isn't one.

        Tolerant of a body-less success: a bare `response.json()` raises on an
        empty body rather than returning something falsy, so a 204 used to blow
        up in the branch that existed to handle exactly that case.
        """
        try:
            body = response.json()
        except ValueError:
            body = None
        if not body:
            return {"status": response.status_code}
        return body

    # ----------------------------------------------------------------------
    # Verbs
    # ----------------------------------------------------------------------

    def iter_pages(
        self, endpoint: str, *, data: Dict = None, page_size: int = None, offset: str = None
    ) -> Iterator[Dict]:
        """
        Yield each page body in turn, fetching the next only when asked for one.

        This is the only pagination loop in the library; the eager :meth:`get` is
        a wrapper over it. Keeping it single means the `assert_same_origin` guard
        on every hop cannot drift out of one copy and not the other.

        :param page_size: results per request. The API's own maximum may be
            smaller and may change; ask for what you want and read what arrives.

        :param offset: an opaque page token to resume from.
        """
        params = dict(data or {})
        if page_size:
            params["limit"] = page_size
        if offset:
            params["pageToken"] = offset

        url = self.href + endpoint
        while True:
            body = self._request("GET", url, idempotency=Idempotency.SAFE, params=params)
            yield body

            next_page = body.get("nextPageLink")
            if not next_page:
                return
            assert_same_origin(next_page, self.href)
            # "Any other parameters provided alongside a pageToken will be
            # ignored" -- the link already carries everything it needs.
            url, params = next_page, None

    def iter_items(
        self,
        endpoint: str,
        *,
        data: Dict = None,
        page_size: int = None,
        offset: str = None,
        limit: int = None,
    ) -> Iterator[Dict]:
        """
        Yield the elements of `items` across pages, fetching lazily.

        `limit` caps the total number of items yielded, across however many
        requests that takes -- unlike :meth:`get`'s `limit`, which is a page size
        and stops after one request.
        """
        seen = 0
        for body in self.iter_pages(
            endpoint, data=data, page_size=page_size, offset=offset
        ):
            for item in body.get("items") or ():
                yield item
                seen += 1
                if limit and seen >= limit:
                    return

    def get(self, endpoint: str, data: Dict = None, limit=None, offset=None) -> Dict:
        """
        Makes a GET request to API endpoint.

        Without `limit`, every page is fetched and their `items` concatenated.

        :param endpoint: API endpoint to request

        :param data: dictionary of optional query params

        :param limit: Maximum number of results to return in this query. Passing
            it also means a single request: no further pages are followed.

        :param offset: An opaque token used to fetch the next page of results.

        :return:
        """
        pages = self.iter_pages(endpoint, data=data, page_size=limit, offset=offset)
        first = next(pages)
        if limit:
            return first

        merged = dict(first)
        items = list(first.get("items") or ())
        had_items = "items" in first
        for body in pages:
            merged.update(body)
            items.extend(body.get("items") or ())
            had_items = had_items or "items" in body

        if had_items:
            merged["items"] = items
        # The walk is finished, so a cursor left over from an earlier page would
        # only be a lie about there being more.
        merged.pop("nextPageLink", None)
        merged.pop("nextPageToken", None)
        return merged

    def post(self, endpoint: str, data: Dict, *, idempotency: Idempotency = None) -> Dict:
        """
        Makes a POST request to the API endpoint.

        :param endpoint: API endpoint to request

        :param data: data dict to be sent as body json

        :param idempotency: whether this particular call may be replayed. Defaults
            to unsafe, which is the right default for a POST: most create rows,
            docs or pages, and replaying those duplicates them.

        :return:
        """
        return self._request(
            "POST",
            self.href + endpoint,
            idempotency=idempotency or Idempotency.UNSAFE,
            json=data,
        )

    def put(self, endpoint: str, data: Dict, *, idempotency: Idempotency = None) -> Dict:
        """
        Makes a PUT request to the API endpoint.

        :param endpoint: API endpoint to request

        :param data: data dict to be sent as body json

        :param idempotency: whether this particular call may be replayed. Defaults
            to idempotent, since a PUT normally assigns a whole object -- but see
            the page and row methods, which pass something stricter when their
            arguments make replaying unsafe.

        :return:
        """
        return self._request(
            "PUT",
            self.href + endpoint,
            idempotency=idempotency or Idempotency.IDEMPOTENT,
            json=data,
        )

    def patch(self, endpoint: str, data: Dict, *, idempotency: Idempotency = None) -> Dict:
        """
        Makes a PATCH request to the API endpoint.

        :param endpoint: API endpoint to request

        :param data: data dict to be sent as body json

        :return:
        """
        return self._request(
            "PATCH",
            self.href + endpoint,
            idempotency=idempotency or Idempotency.IDEMPOTENT,
            json=data,
        )

    def delete(self, endpoint: str, data: Dict = None, *, idempotency: Idempotency = None) -> Dict:
        """
        Makes a DELETE request to the API endpoint.

        :param endpoint: API endpoint to request

        :param data: data dict to be sent as body json

        :param idempotency: defaults to idempotent -- deleting something twice
            leaves it deleted. Deleting by *name* is not, because the API picks an
            arbitrary match, so those call sites pass unsafe explicitly.

        :return:
        """
        return self._request(
            "DELETE",
            self.href + endpoint,
            idempotency=idempotency or Idempotency.IDEMPOTENT,
            json=data,
        )

    def list_docs(
        self,
        is_owner: bool = False,
        query: str = None,
        source_doc_id: str = None,
        limit: int = None,
        offset: int = None,
    ) -> Dict:
        """
        Returns a list of Coda documents accessible by the user.

        These are returned in the same order as on the docs page: reverse
        chronological by the latest event relevant to the user (last viewed, edited, or shared).

        Docs: https://coda.io/developers/apis/v1/#operation/listDocs

        :param is_owner: Show only docs owned by the user.

        :param query: Search term used to filter down results.

        :param source_doc_id: Show only docs copied from the specified doc ID.

        :param limit: Maximum number of results to return in this query.

        :param offset: An opaque token used to fetch the next page of results.

        :return:
        """
        return self.get(
            "/docs",
            data={"isOwner": is_owner, "query": query, "sourceDoc": source_doc_id},
            limit=limit,
            offset=offset,
        )

    def create_doc(self, title: str, source_doc: str = None, tz: str = None) -> Dict:
        """
        Creates a new Coda doc, optionally copying an existing doc.

        Docs: https://coda.io/developers/apis/v1/#operation/createDoc

        :param title: Title of the new doc.

        :param source_doc: An optional doc ID from which to create a copy.

        :param tz: The timezone to use for the newly created doc.

        :return:
        """
        data = {"title": title}
        if source_doc:
            data["sourceDoc"] = source_doc
        if tz:
            data["timezone"] = tz

        return self.post("/docs", data, idempotency=ENDPOINTS["create_doc"].idempotency)

    def get_doc(self, doc_id: str) -> Dict:
        """
        Returns metadata for the specified doc.

        Docs: https://coda.io/developers/apis/v1/#operation/getDoc

        :param doc_id: ID of the doc. Example: "AbCDeFGH"

        :return:
        """
        return self.get("/docs/" + doc_id)

    def delete_doc(self, doc_id: str) -> Dict:
        """
        Deletes a doc.

        Docs: https://coda.io/developers/apis/v1/#operation/deleteDoc

        :param doc_id: ID of the doc. Example: "AbCDeFGH"

        :return:
        """
        return self.delete(
            "/docs/" + doc_id, idempotency=ENDPOINTS["delete_doc"].idempotency
        )

    def list_sections(self, doc_id: str, offset: int = None, limit: int = None) -> Dict:
        """
        Returns a list of sections in a Coda doc.

        Docs: https://coda.io/developers/apis/v1/#operation/listSections

        :param doc_id: ID of the doc. Example: "AbCDeFGH"

        :param limit: Maximum number of results to return in this query.

        :param offset: An opaque token used to fetch the next page of results.

        :return:
        """
        return self.get(f"/docs/{doc_id}/pages", offset=offset, limit=limit)

    def get_section(self, doc_id: str, section_id_or_name: str) -> Dict:
        """
        Returns details about a section.

        Docs: https://coda.io/developers/apis/v1/#operation/getSection

        :param doc_id: ID of the doc. Example: "AbCDeFGH"

        :param section_id_or_name: ID or name of the section.
            Names are discouraged because they're easily prone to being changed by users.
            If you're using a name, be sure to URI-encode it. Example: "canvas-IjkLmnO"

        :return:
        """
        return self.get(f"/docs/{doc_id}/pages/{section_id_or_name}")

    def list_folders(self, doc_id: str, offset: int = None, limit: int = None) -> Dict:
        """
        Returns a list of folders in a Coda doc.

        Docs: https://coda.io/developers/apis/v1/#operation/listFolders

        :param doc_id: ID of the doc. Example: "AbCDeFGH"

        :param limit: Maximum number of results to return in this query.

        :param offset: An opaque token used to fetch the next page of results.

        :return:
        """
        return self.get(f"/docs/{doc_id}/folders", offset=offset, limit=limit)

    def get_folder(self, doc_id: str, folder_id_or_name: str) -> Dict:
        """
        Returns details about a folder.

        Docs: https://coda.io/developers/apis/v1/#operation/getFolder

        :param doc_id: ID of the doc. Example: "AbCDeFGH"

        :param folder_id_or_name: ID or name of the folder.
            Names are discouraged because they're easily prone to being
            changed by users. If you're using a name, be sure to URI-encode it.
            Example: "section-IjkLmnO"

        :return:
        """
        return self.get(f"/docs/{doc_id}/folders/{folder_id_or_name}")

    def list_tables(self, doc_id: str, offset: int = None, limit: int = None, data: Dict = None) -> Dict:
        """
        Returns a list of tables in a Coda doc.

        Docs: https://coda.io/developers/apis/v1/#operation/listTables

        :param doc_id: ID of the doc. Example: "AbCDeFGH"

        :param limit: Maximum number of results to return in this query.

        :param offset: An opaque token used to fetch the next page of results.
        
        :param data: A dict of additional options/parameters to use in the query
        :return:
        """
        response = self.get(f"/docs/{doc_id}/tables", offset=offset, limit=limit, data=data)
        return response

    def get_table(self, doc_id: str, table_id_or_name: str, data: Dict = None) -> Dict:
        """
        Returns details about a specific table.

        Docs: https://coda.io/developers/apis/v1/#operation/getTable

        :param doc_id: ID of the doc. Example: "AbCDeFGH"

        :param table_id_or_name: ID or name of the table.
            Names are discouraged because they're easily prone to being changed by users.
            If you're using a name, be sure to URI-encode it. Example: "grid-pqRst-U"
        
        :param data: A dict of additional options/parameters to use in the query

        :return:
        """
        response = self.get(f"/docs/{doc_id}/tables/{table_id_or_name}", data=data)
        return response

    def list_views(self, doc_id: str, offset: int = None, limit: int = None, data: Dict = None) -> Dict:
        """
        Returns a list of views in a Coda doc.

        Docs: https://coda.io/developers/apis/v1/#operation/listViews

        :param doc_id: ID of the doc. Example: "AbCDeFGH"

        :param limit: Maximum number of results to return in this query.

        :param offset: An opaque token used to fetch the next page of results.
        
        :param data: A dict of additional options/parameters to use in the query

        :return:
        """
        return self.get(
            f"/docs/{doc_id}/tables?tableTypes=view", offset=offset, limit=limit, data=data
        )

    def get_view(self, doc_id: str, view_id_or_name: str) -> Dict:
        """
        Returns details about a specific view.

        Docs: https://coda.io/developers/apis/v1/#operation/getView

        :param doc_id: ID of the doc. Example: "AbCDeFGH"

        :param view_id_or_name: ID or name of the view.
            Names are discouraged because they're easily prone to being changed by users.
            If you're using a name, be sure to URI-encode it. Example: "table-pqRst-U"

        :return:
        """
        return self.get(f"/docs/{doc_id}/tables/{view_id_or_name}")

    def list_columns(
        self, doc_id: str, table_id_or_name: str, offset: int = None, limit: int = None
    ) -> Dict:
        """
        Returns a list of columns in a table.

        :param doc_id: ID of the doc. Example: "AbCDeFGH"

        :param table_id_or_name: ID or name of the table.
            Names are discouraged because they're easily prone to being changed by users.
            If you're using a name, be sure to URI-encode it. Example: "grid-pqRst-U"

        :param limit: Maximum number of results to return in this query.

        :param offset: An opaque token used to fetch the next page of results.

        :return:
        """
        return self.get(
            f"/docs/{doc_id}/tables/{table_id_or_name}/columns",
            offset=offset,
            limit=limit,
        )

    def get_column(
        self, doc_id: str, table_id_or_name: str, column_id_or_name: str
    ) -> Dict:
        """
        Returns details about a column in a table.

        Docs: https://coda.io/developers/apis/v1/#operation/getColumn

        :param doc_id:  ID of the doc. Example: "AbCDeFGH"

        :param table_id_or_name: ID or name of the table.
            Names are discouraged because they're easily prone to being changed by users.
            If you're using a name, be sure to URI-encode it. Example: "grid-pqRst-U"

        :param column_id_or_name: ID or name of the column.
            Names are discouraged because they're easily prone to being changed by users.
            If you're using a name, be sure to URI-encode it. Example: "c-tuVwxYz"

        :return:
        """
        return self.get(
            f"/docs/{doc_id}/tables/{table_id_or_name}/columns/{column_id_or_name}"
        )

    def list_rows(
        self,
        doc_id: str,
        table_id_or_name: str,
        query: str = None,
        use_column_names: bool = False,
        limit: int = None,
        offset: int = None,
        sync_token: str = None,
        data: Dict = None
    ) -> Dict:
        """
        Returns a list of rows in a table.

        Docs: https://coda.io/developers/apis/v1/#tag/Rows

        :param doc_id:  ID of the doc. Example: "AbCDeFGH"

        :param table_id_or_name: ID or name of the table.
            Names are discouraged because they're easily prone to being changed by users.
            If you're using a name, be sure to URI-encode it. Example: "grid-pqRst-U"

        :param query: filter returned rows, specified as `<column_id_or_name>:<value>`.
            If you'd like to use a column name instead of an ID,
            you must quote it (e.g., `"My Column":123`).
            Also note that `value` is a JSON value; if you'd like to use a string,
            you must surround it in quotes (e.g., `"groceries"`).

        :param use_column_names: Use column names instead of column IDs in the returned output.
            This is generally discouraged as it is fragile.
            If columns are renamed, code using original names may throw errors.

        :param limit: Maximum number of results to return in this query.

        :param offset: An opaque token used to fetch the next page of results.

        :param sync_token: An opaque token returned from a previous call that
            can be used to return results that are relevant to the query since
            the call where the syncToken was generated..

        :param: data: A dict of additional parameters to use in the get call.
        """

        if data is None:
            data = {"useColumnNames": use_column_names}
        else:
            data["useColumnNames"]  = use_column_names  

        if query:
            data["query"] = query

        if sync_token:
            data['syncToken'] = sync_token

        return self.get(
            f"/docs/{doc_id}/tables/{table_id_or_name}/rows",
            data=data,
            limit=limit,
            offset=offset,
        )

    def upsert_row(self, doc_id: str, table_id_or_name: str, data: Dict) -> Dict:
        """
        Inserts rows into a table, optionally updating existing rows if key columns are provided.

        This endpoint will always return a 202, so long as the doc and table exist and
        are accessible (and the update is structurally valid). Row inserts/upserts are generally
        processed within several seconds.
        When upserting, if multiple rows match the specified key column(s),
        they will all be updated with the specified value.

        Docs: https://coda.io/developers/apis/v1/#operation/upsertRows

        :param doc_id:  ID of the doc. Example: "AbCDeFGH"

        :param table_id_or_name: ID or name of the table.
            Names are discouraged because they're easily prone to being changed by users.
            If you're using a name, be sure to URI-encode it. Example: "grid-pqRst-U"

        :param data:
            {
                "rows": [{"cells": [{"column": "c-tuVwxYz", "value": "$12.34"}]}],
                "keyColumns": ["c-bCdeFgh"]
            }
        """
        return self.post(
            f"/docs/{doc_id}/tables/{table_id_or_name}/rows",
            data,
            idempotency=ENDPOINTS["upsert_row"].idempotency,
        )

    def get_row(self, doc_id: str, table_id_or_name: str, row_id_or_name: str, data: Dict = None) -> Dict:
        """
        Returns details about a row in a table.

        Docs: https://coda.io/developers/apis/v1/#operation/getRow

        :param doc_id:  ID of the doc. Example: "AbCDeFGH"

        :param table_id_or_name: ID or name of the table.
            Names are discouraged because they're easily prone to being changed by users.
            If you're using a name, be sure to URI-encode it. Example: "grid-pqRst-U"

        :param row_id_or_name: ID or name of the row.
            Names are discouraged because they're easily prone to being changed by users.
            If you're using a name, be sure to URI-encode it.
            If there are multiple rows with the same value in the identifying column,
            an arbitrary one will be selected.
        """
        return self.get(
            f"/docs/{doc_id}/tables/{table_id_or_name}/rows/{row_id_or_name}", data=data
        )

    def update_row(
        self, doc_id: str, table_id_or_name: str, row_id_or_name: str, data: Dict
    ) -> Dict:
        """
        Updates the specified row in the table.

        This endpoint will always return a 202, so long as the doc and table exist and
        are accessible (and the update is structurally valid). Row updates are generally
        processed within several seconds.
        When updating using a name as opposed to an ID, an arbitrary row will be affected.

        Docs: https://coda.io/developers/apis/v1/#operation/updateRow

        :param doc_id:  ID of the doc. Example: "AbCDeFGH"

        :param table_id_or_name: ID or name of the table.
            Names are discouraged because they're easily prone to being changed by users.
            If you're using a name, be sure to URI-encode it. Example: "grid-pqRst-U"

        :param row_id_or_name: ID or name of the row.
            Names are discouraged because they're easily prone to being changed by users.
            If you're using a name, be sure to URI-encode it.
            If there are multiple rows with the same value in the identifying column,
            an arbitrary one will be selected.

        :param data: Example: {"row": {"cells": [{"column": "c-tuVwxYz", "value": "$12.34"}]}}
        """
        return self.put(
            f"/docs/{doc_id}/tables/{table_id_or_name}/rows/{row_id_or_name}",
            data,
            idempotency=ENDPOINTS["update_row"].idempotency,
        )

    def delete_row(self, doc_id, table_id_or_name: str, row_id_or_name: str) -> Dict:
        """
        Deletes the specified row from the table.

        This endpoint will always return a 202, so long as the row exists and
        is accessible (and the update is structurally valid).
        Row deletions are generally processed within several seconds.
        When deleting using a name as opposed to an ID, an arbitrary row will be removed.

        Docs: https://coda.io/developers/apis/v1/#operation/deleteRow

        :param doc_id:  ID of the doc. Example: "AbCDeFGH"

        :param table_id_or_name: ID or name of the table.
            Names are discouraged because they're easily prone to being changed by users.
        If you're using a name, be sure to URI-encode it. Example: "grid-pqRst-U"

        :param row_id_or_name: ID or name of the row.
            Names are discouraged because they're easily prone to being changed by users.
            If you're using a name, be sure to URI-encode it.
            If there are multiple rows with the same value in the identifying column,
            an arbitrary one will be selected.
        """
        return self.delete(
            f"/docs/{doc_id}/tables/{table_id_or_name}/rows/{row_id_or_name}",
            idempotency=ENDPOINTS["delete_row"].idempotency,
        )

    def list_formulas(self, doc_id: str, offset: int = None, limit: int = None) -> Dict:
        """
        Returns a list of named formulas in a Coda doc.

        Docs: https://coda.io/developers/apis/v1/#operation/listFormulas

        :param doc_id:  ID of the doc. Example: "AbCDeFGH"

        :param limit: Maximum number of results to return in this query.

        :param offset: An opaque token used to fetch the next page of results.
        """
        return self.get(f"/docs/{doc_id}/formulas", offset=offset, limit=limit)

    def get_formula(self, doc_id: str, formula_id_or_name: str) -> Dict:
        """
        Returns info on a formula.

        Docs: https://coda.io/developers/apis/v1/#operation/getFormula

        :param doc_id:  ID of the doc. Example: "AbCDeFGH"

        :param formula_id_or_name: ID or name of the formula.
            Names are discouraged because they're easily prone to being changed by users.
            If you're using a name, be sure to URI-encode it. Example: "f-fgHijkLm".
        """
        return self.get(f"/docs/{doc_id}/formulas/{formula_id_or_name}")

    def list_controls(self, doc_id: str, offset: int = None, limit: int = None) -> Dict:
        """
        Lists controls and get their current values.

        Controls provide a user-friendly way to input a value
        that can affect other parts of the doc.

        Docs: https://coda.io/developers/apis/v1/#tag/Controls

        :param doc_id:  ID of the doc. Example: "AbCDeFGH"

        :param limit: Maximum number of results to return in this query.

        :param offset: An opaque token used to fetch the next page of results.

        :return:
        """
        return self.get(f"/docs/{doc_id}/controls", offset=offset, limit=limit)

    def get_control(self, doc_id: str, control_id_or_name: str) -> Dict:
        """
        Returns info on a control.

        Docs: https://coda.io/developers/apis/v1/#operation/getControl

        :param doc_id:  ID of the doc. Example: "AbCDeFGH"
        :param control_id_or_name: ID or name of the control.
            Names are discouraged because they're easily prone to being changed by users.
            If you're using a name, be sure to URI-encode it. Example: "ctrl-cDefGhij".
        """
        return self.get(f"/docs/{doc_id}/controls/{control_id_or_name}")

    def account(self) -> Dict:
        """
        Retrieves logged-in account information.

        At this time, the API exposes some limited information about your account.
        However, /whoami is a good endpoint to hit to verify that
        you're hitting the API correctly and that your token is working as expected.

        Docs: https://coda.io/developers/apis/v1/#tag/Account
        """
        return self.get("/whoami")

    def resolve_browser_link(self, url: str, degrade_gracefully: bool = False) -> Dict:
        """
        Retrieves the metadata of a Coda object for an URL.

        Given a browser link to a Coda object, attempts to find it and
        return metadata that can be used to get more info on it.
        Returns a 400 if the URL does not appear to be a Coda URL or a
        404 if the resource cannot be located with the current credentials.

        Docs: https://coda.io/developers/apis/v1/#operation/resolveBrowserLink

        :param url: The browser link to try to resolve.
            Example: "https://coda.io/d/_dAbCDeFGH/Launch-Status_sumnO"

        :param degrade_gracefully: By default, attempting to resolve the Coda URL
            of a deleted object will result in an error. If this flag is set,
            the next-available object, all the way up to the doc itself, will be resolved.
        """
        return self.get(
            "/resolveBrowserLink",
            data={"url": url, "degradeGracefully": degrade_gracefully},
        )
