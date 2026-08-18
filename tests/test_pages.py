"""
Pages: reading them, writing them, and the guards on the ways to lose content.
"""

import pytest

from codaio import CanvasContent, EmbedContent, Page, SyncPageContent, err
from codaio._endpoints import page_update_idempotency
from codaio.http import Idempotency
from tests.conftest import BASE_URL

DOC = BASE_URL + "/docs/doc_id"
PAGE = "canvas-p1"


@pytest.fixture
def page(main_document, mock_json_response):
    mock_json_response(f"{DOC}/pages/{PAGE}", "get_sections.json")
    return Page.from_json(
        {
            "id": PAGE, "type": "page", "name": "Launch", "contentType": "canvas",
            "isHidden": False, "isEffectivelyHidden": False, "children": [],
        },
        document=main_document,
    )


class TestReading:
    def test_content_type_predicates(self, main_document):
        def built(kind):
            return Page.from_json({"id": "p", "type": "page", "contentType": kind},
                                  document=main_document)

        assert built("canvas").is_canvas
        assert built("embed").is_embed
        assert built("syncPage").is_sync_page
        assert not built("embed").is_canvas

    def test_content_lines_are_typed(self, page, mock_json_response):
        mock_json_response(
            f"{DOC}/pages/{PAGE}/content",
            "empty.json",
        )
        assert page.content() == []

    def test_content_exposes_style_and_text(self, page, mocked_responses):
        mocked_responses.add(
            "GET", f"{DOC}/pages/{PAGE}/content",
            json={"items": [
                {"id": "cl-1", "type": "line",
                 "itemContent": {"style": "h1", "format": "plainText",
                                 "content": "Heading"}},
                {"id": "cl-2", "type": "line",
                 "itemContent": {"style": "bulletedList", "format": "plainText",
                                 "content": "A point", "lineLevel": 1}},
            ]},
        )
        first, second = page.content()

        assert (first.id, first.style, first.content) == ("cl-1", "h1", "Heading")
        assert second.line_level == 1

    def test_content_is_fetched_lazily(self, page, mocked_responses):
        mocked_responses.add(
            "GET", f"{DOC}/pages/{PAGE}/content",
            json={"items": [{"id": "cl-1", "type": "line"}],
                  "nextPageLink": f"{DOC}/pages/{PAGE}/content?page=2"},
        )
        mocked_responses.add(
            "GET", f"{DOC}/pages/{PAGE}/content?page=2",
            json={"items": [{"id": "cl-2", "type": "line"}]},
        )
        before = len(mocked_responses.calls)
        lines = page.iter_content()
        next(lines)

        assert len(mocked_responses.calls) == before + 1


class TestWriting:
    def test_update_sends_only_what_changed(self, page, mocked_responses):
        mocked_responses.add("PUT", f"{DOC}/pages/{PAGE}", json={"requestId": "r"})
        page.update(name="Renamed")

        import json as _json
        assert _json.loads(mocked_responses.calls[-1].request.body) == {"name": "Renamed"}

    def test_update_with_nothing_to_do_is_refused(self, page):
        with pytest.raises(err.InvalidQuery, match="nothing to change"):
            page.update()

    def test_append_sends_a_content_update(self, page, mocked_responses):
        mocked_responses.add("PUT", f"{DOC}/pages/{PAGE}", json={"requestId": "r"})
        page.append("# Hello")

        import json as _json
        body = _json.loads(mocked_responses.calls[-1].request.body)
        assert body["contentUpdate"]["insertionMode"] == "append"
        assert body["contentUpdate"]["canvasContent"] == {
            "format": "markdown", "content": "# Hello"}

    def test_there_is_no_way_to_move_a_page(self, page):
        """
        Deliberate: `PageUpdate` has no reparenting field, so the API cannot move
        pages. A `move()` here would imply otherwise.
        """
        assert not hasattr(page, "move")


class TestContentDeletionGuards:
    def test_an_empty_element_list_is_refused(self, page, mocked_responses):
        """
        The API treats an empty list of ids exactly like an omitted one and
        deletes everything. A caller who built the list from a filter that
        matched nothing would wipe the page.
        """
        with pytest.raises(err.InvalidQuery, match="entire"):
            page.delete_content([])

        assert not [c for c in mocked_responses.calls if c.request.method == "DELETE"]

    def test_clearing_everything_has_to_be_asked_for(self, page, mocked_responses):
        mocked_responses.add(
            "DELETE", f"{DOC}/pages/{PAGE}/content", json={"requestId": "r"})
        page.clear_content()

        assert mocked_responses.calls[-1].request.body is None

    def test_named_elements_are_sent(self, page, mocked_responses):
        mocked_responses.add(
            "DELETE", f"{DOC}/pages/{PAGE}/content", json={"requestId": "r"})
        page.delete_content(["cl-1", "cl-2"])

        import json as _json
        assert _json.loads(mocked_responses.calls[-1].request.body) == {
            "elementIds": ["cl-1", "cl-2"]}


class TestUpdateIdempotency:
    """
    Whether one page update may be replayed depends on three of its arguments.
    That is the whole reason codaio decides it rather than the caller.
    """

    @pytest.mark.parametrize(
        "page_ref,data,expected",
        [
            # metadata only, by id: full assignment, converges
            ("canvas-1", {"name": "x"}, Idempotency.IDEMPOTENT),
            # replacing the whole page with the same content converges
            ("canvas-1",
             {"contentUpdate": {"insertionMode": "replace", "canvasContent": {}}},
             Idempotency.IDEMPOTENT),
            # appending twice appends twice
            ("canvas-1",
             {"contentUpdate": {"insertionMode": "append", "canvasContent": {}}},
             Idempotency.UNSAFE),
            ("canvas-1",
             {"contentUpdate": {"insertionMode": "prepend", "canvasContent": {}}},
             Idempotency.UNSAFE),
            # replace *relative to an element*: the first attempt consumes that
            # element, and a missing elementId means "the entire page"
            ("canvas-1",
             {"contentUpdate": {"insertionMode": "replace", "elementId": "cl-9",
                                "canvasContent": {}}},
             Idempotency.UNSAFE),
            # by name, an arbitrary match is chosen and need not be the same twice
            ("My Page", {"name": "x"}, Idempotency.UNSAFE),
        ],
        ids=["metadata", "replace_all", "append", "prepend", "replace_element",
             "by_name"],
    )
    def test_classification(self, page_ref, data, expected):
        assert page_update_idempotency(page_ref, data) is expected


class TestContentBuilders:
    def test_a_plain_string_is_markdown(self):
        assert CanvasContent("# Hi").to_json() == {
            "type": "canvas",
            "canvasContent": {"format": "markdown", "content": "# Hi"},
        }

    def test_html_is_available(self):
        assert CanvasContent("<b>Hi</b>", format="html").to_json()[
            "canvasContent"]["format"] == "html"

    def test_an_unknown_format_is_refused(self):
        with pytest.raises(err.InvalidQuery, match="markdown"):
            CanvasContent("x", format="rtf").to_json()

    def test_embed(self):
        assert EmbedContent("https://example.com").to_json() == {
            "type": "embed", "url": "https://example.com"}

    def test_sync_page(self):
        out = SyncPageContent("srcdoc", source_page_id="canvas-9",
                              include_subpages=True).to_json()
        assert out == {"type": "syncPage", "mode": "page", "sourceDocId": "srcdoc",
                       "sourcePageId": "canvas-9", "includeSubpages": True}


class TestCreate:
    def test_create_page_builds_the_body(self, main_document, mocked_responses):
        mocked_responses.add("POST", f"{DOC}/pages", json={"requestId": "r", "id": "p"})
        main_document.create_page(
            "New", subtitle="Sub", parent_page="canvas-parent", content="# Body")

        import json as _json
        body = _json.loads(mocked_responses.calls[-1].request.body)
        assert body["name"] == "New"
        assert body["parentPageId"] == "canvas-parent"
        assert body["pageContent"]["canvasContent"]["content"] == "# Body"

    def test_a_page_object_can_be_the_parent(self, main_document, mocked_responses):
        mocked_responses.add("POST", f"{DOC}/pages", json={"requestId": "r"})
        parent = Page.from_json({"id": "canvas-parent", "type": "page"},
                                document=main_document)
        main_document.create_page("New", parent_page=parent)

        import json as _json
        assert _json.loads(
            mocked_responses.calls[-1].request.body)["parentPageId"] == "canvas-parent"


class TestGoneMeansDeleted:
    def test_a_deleted_page_reports_410(self, main_document, mocked_responses):
        mocked_responses.add(
            "GET", f"{DOC}/pages/{PAGE}", status=410, json={"message": "deleted"})

        with pytest.raises(err.Gone):
            main_document.get_page(PAGE)
