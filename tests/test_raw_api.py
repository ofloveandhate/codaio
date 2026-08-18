"""
The thin `Coda.*` wrappers around the raw API endpoints.

Each of these is a one-liner that builds a URL and delegates, so the thing worth
pinning down is that it hits the URL it claims to. The expected URLs are not
written here: they come from codaio._endpoints, so that one description drives
these tests, the retry layer's idempotency classification, and the conformance
check against the published spec.

Note what this file can and cannot prove. It shows codaio calls the URL codaio
intends to -- self-consistency, not correctness. An endpoint that does not exist
passes every test here, which is exactly how `/docs/{docId}/folders` survived.
Only the conformance check can catch that.
"""

import pytest

from codaio._endpoints import ENDPOINTS
from tests.conftest import BASE_URL

# Arguments to call each method with. The URL it should produce is *not* here --
# that comes from codaio._endpoints, so the path is written down once and the
# conformance check compares the same string against the published spec.
CALLS = {
    "list_docs": {},
    "get_doc": {"doc_id": "d1"},
    "delete_doc": {"doc_id": "d1"},
    "list_pages": {"doc_id": "d1"},
    "get_page": {"doc_id": "d1", "page_id_or_name": "canvas-p1"},
    "get_page_content": {"doc_id": "d1", "page_id_or_name": "canvas-p1"},
    "get_page_export": {
        "doc_id": "d1", "page_id_or_name": "canvas-p1", "request_id": "req-1",
    },
    "list_folders": {"doc_id": "d1"},
    "get_folder": {"doc_id": "d1", "folder_id_or_name": "f1"},
    "get_view": {"doc_id": "d1", "view_id_or_name": "v1"},
    "list_columns": {"doc_id": "d1", "table_id_or_name": "t1"},
    "get_column": {"doc_id": "d1", "table_id_or_name": "t1", "column_id_or_name": "c1"},
    "list_rows": {"doc_id": "d1", "table_id_or_name": "t1"},
    "get_row": {"doc_id": "d1", "table_id_or_name": "t1", "row_id_or_name": "r1"},
    "delete_row": {"doc_id": "d1", "table_id_or_name": "t1", "row_id_or_name": "r1"},
    "list_formulas": {"doc_id": "d1"},
    "get_formula": {"doc_id": "d1", "formula_id_or_name": "fx"},
    "list_controls": {"doc_id": "d1"},
    "get_control": {"doc_id": "d1", "control_id_or_name": "ctl"},
    "account": {},
    "resolve_browser_link": {"url": "https://coda.io/d/_dABC"},
}


@pytest.mark.parametrize("name", sorted(CALLS), ids=sorted(CALLS))
def test_endpoint_hits_expected_url(coda, mock_json_response, mocked_responses, name):
    endpoint = ENDPOINTS[name]
    path = endpoint.format(**CALLS[name])

    mock_json_response(BASE_URL + path, "empty.json", method=endpoint.method)

    getattr(coda, name)(**CALLS[name])

    assert len(mocked_responses.calls) == 1
    called = mocked_responses.calls[0].request.url
    assert called.startswith(BASE_URL + path)


def test_every_registry_entry_is_a_real_method(coda):
    """The registry describes `Coda`; an entry naming nothing is a typo."""
    missing = [name for name in ENDPOINTS if not hasattr(coda, name)]
    assert not missing, f"registry entries with no matching method: {sorted(missing)}"


def test_every_public_endpoint_is_registered():
    """
    Guards against an endpoint being added without a registry entry.

    Parses `Coda` rather than introspecting it, so a method that exists but is
    never described in codaio._endpoints is caught -- which matters because the
    registry is what the conformance check compares against the published spec.
    Deliberate omissions go in `not_endpoints` with a reason.
    """
    import ast
    import pathlib

    not_endpoints = {
        # generic verbs and plumbing rather than endpoints
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "from_environment",
        # pagination primitives; covered by tests/test_pagination_lazy.py
        "iter_pages",
        "iter_items",
    }

    tree = ast.parse(
        (pathlib.Path(__file__).parent.parent / "codaio" / "client.py").read_text()
    )
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Coda")

    def is_property(fn):
        for dec in fn.decorator_list:
            if isinstance(dec, ast.Name) and dec.id == "property":
                return True
            if isinstance(dec, ast.Attribute) and dec.attr in ("setter", "deleter"):
                return True
        return False

    public = {
        fn.name
        for fn in cls.body
        if isinstance(fn, ast.FunctionDef)
        and not fn.name.startswith("_")
        and not is_property(fn)
    }

    missing = public - set(ENDPOINTS) - not_endpoints
    assert not missing, f"endpoints with no registry entry: {sorted(missing)}"


def test_every_registered_endpoint_is_exercised():
    """
    Every registry entry is either called directly here or through the object
    model elsewhere. Without this an entry could describe a URL nothing sends.
    """
    exercised_via_object_model = {
        # deprecated spellings, covered by TestDeprecatedSpellings
        "list_sections",
        "get_section",
        "create_doc",
        "create_page",
        "update_page",
        "delete_page",
        "delete_page_content",
        "begin_page_export",
        "upsert_row",
        "update_row",
        "list_tables",
        "get_table",
        "list_views",
    }

    missing = set(ENDPOINTS) - set(CALLS) - exercised_via_object_model
    assert not missing, f"registered endpoints nothing exercises: {sorted(missing)}"


class TestDeprecatedSpellings:
    """
    Pages were called sections when these were named, and the URLs have pointed
    at /pages for years. The names still work, and say that they are the old ones.
    """

    def test_list_sections_warns_and_delegates(self, coda, mock_json_response,
                                               mocked_responses):
        mock_json_response(BASE_URL + "/docs/d1/pages", "empty.json")

        with pytest.deprecated_call(match="list_pages"):
            coda.list_sections("d1")

        assert mocked_responses.calls[0].request.url.startswith(
            BASE_URL + "/docs/d1/pages"
        )

    def test_get_section_warns_and_delegates(self, coda, mock_json_response,
                                             mocked_responses):
        mock_json_response(BASE_URL + "/docs/d1/pages/s1", "empty.json")

        with pytest.deprecated_call(match="get_page"):
            coda.get_section("d1", "s1")

        assert mocked_responses.calls[0].request.url.startswith(
            BASE_URL + "/docs/d1/pages/s1"
        )


class TestPagination:
    def test_limit_is_passed_through_rather_than_capped(self, coda, mocked_responses):
        """
        A caller's `limit` reaches the API unchanged.

        It used to be rewritten down to MAX_GET_LIMIT, so asking for 700 results
        quietly became a request for 200 and the caller was given no way to tell
        the difference. The API documents that its own maximum varies by endpoint
        and may change at any time, so the server's answer is the only honest one.
        """
        from codaio.client import MAX_GET_LIMIT

        mocked_responses.add("GET", BASE_URL + "/docs", json={"items": []})
        coda.list_docs(limit=MAX_GET_LIMIT + 500)

        assert f"limit={MAX_GET_LIMIT + 500}" in mocked_responses.calls[0].request.url

    def test_offset_becomes_a_page_token(self, coda, mocked_responses):
        mocked_responses.add("GET", BASE_URL + "/docs", json={"items": []})
        coda.list_docs(offset="opaque-token")

        assert "pageToken=opaque-token" in mocked_responses.calls[0].request.url

    def test_a_limited_request_does_not_follow_next_page(self, coda, mocked_responses):
        mocked_responses.add(
            "GET",
            BASE_URL + "/docs",
            json={"items": [{"a": 1}], "nextPageLink": BASE_URL + "/docs?page=2"},
        )
        coda.list_docs(limit=1)

        # asking for a limit means one page only
        assert len(mocked_responses.calls) == 1

    def test_pages_are_concatenated(self, coda, mocked_responses):
        mocked_responses.add(
            "GET",
            BASE_URL + "/docs",
            json={"items": [{"a": 1}], "nextPageLink": BASE_URL + "/docs?page=2"},
        )
        mocked_responses.add("GET", BASE_URL + "/docs?page=2", json={"items": [{"b": 2}]})

        assert coda.list_docs()["items"] == [{"a": 1}, {"b": 2}]


class TestResponseHandling:
    def test_empty_body_becomes_a_status_dict(self, coda, mocked_responses):
        mocked_responses.add("GET", BASE_URL + "/docs", json={}, status=200)
        assert coda.list_docs() == {"status": 200}

    def test_authorization_header_is_sent(self, coda, mocked_responses):
        mocked_responses.add("GET", BASE_URL + "/whoami", json={"name": "x"})
        coda.account()

        sent = mocked_responses.calls[0].request.headers["Authorization"]
        assert sent == f"Bearer {coda.api_key}"

    def test_post_sends_json_content_type(self, coda, mocked_responses):
        mocked_responses.add("POST", BASE_URL + "/docs", json={"id": "new"})
        coda.create_doc("My Document")

        req = mocked_responses.calls[0].request
        assert req.headers["Content-Type"] == "application/json"
        assert b"My Document" in req.body
