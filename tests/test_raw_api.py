"""
The thin `Coda.*` wrappers around the raw API endpoints.

Each of these is a one-liner that builds a URL and delegates, so the thing
worth pinning down is that it hits the URL it claims to. Table-driven rather
than one test per method, so adding an endpoint means adding a row.
"""

import pytest

from tests.conftest import BASE_URL

# (method name, kwargs, HTTP verb, expected path)
ENDPOINTS = [
    ("list_docs", {}, "GET", "/docs"),
    ("get_doc", {"doc_id": "d1"}, "GET", "/docs/d1"),
    ("delete_doc", {"doc_id": "d1"}, "DELETE", "/docs/d1"),
    ("list_sections", {"doc_id": "d1"}, "GET", "/docs/d1/pages"),
    ("get_section", {"doc_id": "d1", "section_id_or_name": "s1"}, "GET", "/docs/d1/pages/s1"),
    ("list_folders", {"doc_id": "d1"}, "GET", "/docs/d1/folders"),
    ("get_folder", {"doc_id": "d1", "folder_id_or_name": "f1"}, "GET", "/docs/d1/folders/f1"),
    ("get_view", {"doc_id": "d1", "view_id_or_name": "v1"}, "GET", "/docs/d1/tables/v1"),
    ("list_columns", {"doc_id": "d1", "table_id_or_name": "t1"}, "GET", "/docs/d1/tables/t1/columns"),
    (
        "get_column",
        {"doc_id": "d1", "table_id_or_name": "t1", "column_id_or_name": "c1"},
        "GET",
        "/docs/d1/tables/t1/columns/c1",
    ),
    ("list_rows", {"doc_id": "d1", "table_id_or_name": "t1"}, "GET", "/docs/d1/tables/t1/rows"),
    (
        "get_row",
        {"doc_id": "d1", "table_id_or_name": "t1", "row_id_or_name": "r1"},
        "GET",
        "/docs/d1/tables/t1/rows/r1",
    ),
    (
        "delete_row",
        {"doc_id": "d1", "table_id_or_name": "t1", "row_id_or_name": "r1"},
        "DELETE",
        "/docs/d1/tables/t1/rows/r1",
    ),
    ("list_formulas", {"doc_id": "d1"}, "GET", "/docs/d1/formulas"),
    ("get_formula", {"doc_id": "d1", "formula_id_or_name": "fx"}, "GET", "/docs/d1/formulas/fx"),
    ("list_controls", {"doc_id": "d1"}, "GET", "/docs/d1/controls"),
    ("get_control", {"doc_id": "d1", "control_id_or_name": "ctl"}, "GET", "/docs/d1/controls/ctl"),
    ("account", {}, "GET", "/whoami"),
    ("resolve_browser_link", {"url": "https://coda.io/d/_dABC"}, "GET", "/resolveBrowserLink"),
]


@pytest.mark.parametrize(
    "method,kwargs,verb,path", ENDPOINTS, ids=[e[0] for e in ENDPOINTS]
)
def test_endpoint_hits_expected_url(
    coda, mock_json_response, mocked_responses, method, kwargs, verb, path
):
    mock_json_response(BASE_URL + path, "empty.json", method=verb)

    getattr(coda, method)(**kwargs)

    assert len(mocked_responses.calls) == 1
    called = mocked_responses.calls[0].request.url
    assert called.startswith(BASE_URL + path)


def test_every_public_endpoint_is_covered():
    """
    Guards against a new endpoint being added without a row above. It is fine
    to skip one deliberately -- add it to `known_untested` with a reason.
    """
    import ast
    import pathlib

    known_untested = {
        # exercised through the object model in the other test modules
        "create_doc",
        "upsert_row",
        "update_row",
        "list_tables",
        "get_table",
        "list_views",
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "from_environment",
        # pagination primitives rather than endpoints; covered by
        # tests/test_pagination_lazy.py
        "iter_pages",
        "iter_items",
    }

    tree = ast.parse((pathlib.Path(__file__).parent.parent / "codaio" / "client.py").read_text())
    cls = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Coda"
    )
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
    tested = {e[0] for e in ENDPOINTS}

    missing = public - tested - known_untested
    assert not missing, f"raw endpoints with no test: {sorted(missing)}"


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
