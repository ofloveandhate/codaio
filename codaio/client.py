"""
The raw API client: one method per endpoint, each returning a plain dict.
"""

from __future__ import annotations

import warnings
from typing import Dict, Iterator, Sequence

import attr
import requests

from codaio import credentials, err
from codaio._endpoints import (
    ENDPOINTS,
    PAGE_EXPORT_FORMATS,
    looks_like_page_id,
    page_update_idempotency,
)
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

    # ----------------------------------------------------------------------
    # Pages
    # ----------------------------------------------------------------------

    def list_pages(self, doc_id: str, offset: str = None, limit: int = None) -> Dict:
        """
        Returns a list of pages in a doc.

        The listing is flat, but each page carries both its `parent` and its
        `children`, so one call is enough to rebuild the whole tree. See
        :meth:`codaio.Document.page_tree`.

        :param doc_id: ID of the doc. Example: "AbCDeFGH"

        :param limit: Maximum number of results to return in this query.

        :param offset: An opaque token used to fetch the next page of results.

        :return:
        """
        return self.get(f"/docs/{doc_id}/pages", offset=offset, limit=limit)

    def get_page(self, doc_id: str, page_id_or_name: str) -> Dict:
        """
        Returns details about a page.

        :param doc_id: ID of the doc. Example: "AbCDeFGH"

        :param page_id_or_name: ID or name of the page.
            Names are discouraged because they're easily prone to being changed by
            users, and if several pages share a name an arbitrary one is chosen.
            If you're using a name, be sure to URI-encode it.
            Example: "canvas-IjkLmnO"

        :return:
        """
        return self.get(f"/docs/{doc_id}/pages/{page_id_or_name}")

    def create_page(self, doc_id: str, data: Dict) -> Dict:
        """
        Creates a page in a doc.

        Returns 202: the page is queued rather than created by the time this
        returns. Requires the Doc Maker role in the doc's workspace.

        :param doc_id: ID of the doc. Example: "AbCDeFGH"

        :param data: a `PageCreate` body -- `name`, `subtitle`, `iconName`,
            `imageUrl`, `parentPageId`, `pageContent`.

        :return:
        """
        return self.post(
            f"/docs/{doc_id}/pages", data,
            idempotency=ENDPOINTS["create_page"].idempotency,
        )

    def update_page(self, doc_id: str, page_id_or_name: str, data: Dict) -> Dict:
        """
        Updates a page: its title, icon, cover, hidden flag, or its content.

        Whether this may be retried is worked out from the arguments rather than
        assumed, because it genuinely varies:

        * changing only metadata, or replacing the whole page's content, ends in
          the same state however many times it runs;
        * appending or prepending content does not -- a replay adds it twice;
        * replacing content *relative to an `elementId`* does not either. The
          first attempt consumes that element and its replacement gets fresh ids,
          so on a replay the id no longer exists -- and the API documents that a
          *missing* `elementId` means "operate on the entire page", which would
          turn a retried paragraph edit into replacing the whole page.
        * addressing the page by name rather than id is never safe to replay,
          since the API picks an arbitrary match among pages sharing a name.

        :param doc_id: ID of the doc. Example: "AbCDeFGH"

        :param page_id_or_name: ID or name of the page.

        :param data: a `PageUpdate` body.

        :return:
        """
        return self.put(
            f"/docs/{doc_id}/pages/{page_id_or_name}", data,
            idempotency=page_update_idempotency(page_id_or_name, data),
        )

    def delete_page(self, doc_id: str, page_id_or_name: str) -> Dict:
        """
        Deletes a page.

        :param doc_id: ID of the doc. Example: "AbCDeFGH"

        :param page_id_or_name: ID or name of the page. Deleting *by name* cannot
            be retried safely, since the API picks an arbitrary match.

        :return:
        """
        return self.delete(
            f"/docs/{doc_id}/pages/{page_id_or_name}",
            idempotency=(
                Idempotency.IDEMPOTENT if looks_like_page_id(page_id_or_name)
                else Idempotency.UNSAFE
            ),
        )

    def get_page_content(
        self,
        doc_id: str,
        page_id_or_name: str,
        *,
        offset: str = None,
        limit: int = None,
        content_format: str = None,
    ) -> Dict:
        """
        Lists a page's content as styled lines.

        This is the synchronous read, and it speaks only `plainText`: each line
        comes back with a style (`h1`, `paragraph`, `bulletedList`, ...) and a
        stable element id like `cl-2ZUJuRhNuN`, which is what you pass back when
        editing content relative to a specific element. For Markdown or HTML you
        want the export instead -- see :meth:`begin_page_export`.

        :param doc_id: ID of the doc. Example: "AbCDeFGH"

        :param page_id_or_name: ID or name of the page.

        :param limit: Maximum number of results to return in this query.

        :param offset: An opaque token used to fetch the next page of results.

        :param content_format: Only `plainText` is accepted today.

        :return:
        """
        data = {"contentFormat": content_format} if content_format else None
        return self.get(
            f"/docs/{doc_id}/pages/{page_id_or_name}/content",
            data=data, offset=offset, limit=limit,
        )

    def delete_page_content(
        self, doc_id: str, page_id_or_name: str, element_ids: Sequence[str] = None
    ) -> Dict:
        """
        Deletes content from a page.

        :param doc_id: ID of the doc. Example: "AbCDeFGH"

        :param page_id_or_name: ID or name of the page.

        :param element_ids: which elements to delete. **Omitting this deletes the
            entire page's content**, which is the API's documented behaviour for
            an omitted *or empty* list -- so an empty list is refused here rather
            than passed on. A caller who built the list from a filter that
            happened to match nothing would otherwise wipe the page.

        :return:
        """
        if element_ids is not None and len(element_ids) == 0:
            raise err.InvalidQuery(
                "delete_page_content(element_ids=[]) would delete the entire "
                "page's content, because the API treats an empty list the same "
                "as an omitted one. Pass element_ids=None if that is what you "
                "meant."
            )
        data = {"elementIds": list(element_ids)} if element_ids else None
        return self.delete(
            f"/docs/{doc_id}/pages/{page_id_or_name}/content", data,
            idempotency=ENDPOINTS["delete_page_content"].idempotency,
        )

    def begin_page_export(
        self, doc_id: str, page_id_or_name: str, output_format: str = "markdown"
    ) -> Dict:
        """
        Starts exporting a page's content, and returns a request id to poll.

        Markdown and HTML are only available this way: the synchronous content
        listing speaks plain text only. Poll :meth:`get_page_export` until a
        download link appears.

        Note this is a write as far as rate limiting is concerned, and lands in
        the tightest bucket -- five requests per ten seconds for doc content -- so
        exporting many pages wants to be serial rather than fanned out.

        :param doc_id: ID of the doc. Example: "AbCDeFGH"

        :param page_id_or_name: ID or name of the page.

        :param output_format: "markdown" or "html".

        :return:
        """
        if output_format not in PAGE_EXPORT_FORMATS:
            raise err.InvalidQuery(
                f"output_format must be one of {sorted(PAGE_EXPORT_FORMATS)}, "
                f"got {output_format!r}"
            )
        return self.post(
            f"/docs/{doc_id}/pages/{page_id_or_name}/export",
            {"outputFormat": output_format},
            idempotency=ENDPOINTS["begin_page_export"].idempotency,
        )

    def get_page_export(self, doc_id: str, page_id_or_name: str, request_id: str) -> Dict:
        """
        Reports on an export started by :meth:`begin_page_export`.

        `downloadLink` appears when the export finishes, and expires shortly
        afterwards -- call this again for a fresh one rather than holding on to
        it. Note the response's `status` is an untyped string with no documented
        values, so gate on `downloadLink` or `error` being present instead.

        :param doc_id: ID of the doc. Example: "AbCDeFGH"

        :param page_id_or_name: ID or name of the page.

        :param request_id: the id returned by :meth:`begin_page_export`.

        :return:
        """
        return self.get(
            f"/docs/{doc_id}/pages/{page_id_or_name}/export/{request_id}"
        )

    # -- deprecated spellings ---------------------------------------------

    def list_sections(self, doc_id: str, offset: str = None, limit: int = None) -> Dict:
        """
        Deprecated. Pages were called sections when this method was named; the
        URL has pointed at /pages for years. Use :meth:`list_pages`.
        """
        warnings.warn(
            "Coda.list_sections is deprecated; use Coda.list_pages.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.list_pages(doc_id, offset=offset, limit=limit)

    def get_section(self, doc_id: str, section_id_or_name: str) -> Dict:
        """
        Deprecated. Use :meth:`get_page`.
        """
        warnings.warn(
            "Coda.get_section is deprecated; use Coda.get_page.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.get_page(doc_id, section_id_or_name)

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
