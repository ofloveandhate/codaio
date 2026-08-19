"""
Rebuilding a doc's page hierarchy from one flat listing.

Every page carries both its `parent` and its `children`, and both are needed:
`children` is the only thing that gives the *order* pages appear in, while
`parent` covers any page a children array happens to omit. Those were the two
fields the old object model deleted on arrival, which is why the hierarchy was
unreachable at all.

The tree is built from whatever the API returned, so the awkward inputs -- a
truncated listing, a page listed out of order, a cycle -- are the point.
"""

import pytest

from codaio import Page, PageTree, err


def page(page_id, *, parent=None, children=(), hidden=False, name=None):
    payload = {
        "id": page_id, "type": "page", "name": name or page_id,
        "isHidden": hidden, "contentType": "canvas",
        "children": [{"id": child, "type": "page"} for child in children],
    }
    if parent:
        payload["parent"] = {"id": parent, "type": "page"}
    return Page.from_json(payload)


def tree(*pages):
    return PageTree.from_pages(list(pages))


class TestShape:
    def test_a_flat_list_of_roots(self):
        built = tree(page("a"), page("b"))

        assert [p.id for p in built.roots] == ["a", "b"]

    def test_children_are_nested_under_their_parent(self):
        built = tree(page("a", children=["b"]), page("b", parent="a"))

        assert [p.id for p in built.roots] == ["a"]
        assert [p.id for p in built.children_of("a")] == ["b"]

    def test_grandchildren(self):
        built = tree(
            page("a", children=["b"]),
            page("b", parent="a", children=["c"]),
            page("c", parent="b"),
        )

        assert [(p.id, depth) for p, depth in built.walk()] == [
            ("a", 0), ("b", 1), ("c", 2)]

    def test_order_comes_from_the_parents_children_array(self):
        """
        A `parent`-only reconstruction cannot recover the order pages appear in,
        because the listing is not necessarily in tree order.
        """
        built = tree(
            page("a", children=["z", "m"]),
            page("m", parent="a"),
            page("z", parent="a"),
        )

        assert [p.id for p in built.children_of("a")] == ["z", "m"]

    def test_a_page_listed_out_of_order_still_lands_correctly(self):
        built = tree(page("b", parent="a"), page("a", children=["b"]))

        assert [p.id for p in built.roots] == ["a"]
        assert [p.id for p in built.children_of("a")] == ["b"]


class TestAwkwardInput:
    def test_an_orphan_becomes_a_root_rather_than_an_error(self):
        """
        A page whose parent is not in the listing is normal: `limit` truncates,
        and the caller still wants a usable tree out of what arrived.
        """
        built = tree(page("b", parent="missing-parent"))

        assert [p.id for p in built.roots] == ["b"]

    def test_a_child_the_parent_did_not_list_is_still_placed(self):
        """`parent` covers what `children` misses."""
        built = tree(page("a"), page("b", parent="a"))

        assert [p.id for p in built.children_of("a")] == ["b"]

    def test_a_child_reference_to_a_page_not_in_the_listing_is_ignored(self):
        built = tree(page("a", children=["absent"]))

        assert built.children_of("a") == []

    def test_a_hierarchy_with_no_root_is_reported(self):
        """
        Two pages each claiming the other as parent leaves nowhere to start.
        Without this the tree would simply be empty, which looks like a doc with
        no pages rather than a contradiction.
        """
        with pytest.raises(err.CodaError, match="no root"):
            tree(
                page("a", parent="b", children=["b"]),
                page("b", parent="a", children=["a"]),
            )

    def test_a_cycle_below_a_root_raises_instead_of_hanging(self):
        """
        The API should never produce one. This tree is built from whatever
        arrived, though, and a hang is a far worse way to discover the problem.
        """
        built = tree(
            page("a", children=["b"]),
            page("b", parent="a", children=["c"]),
            page("c", parent="b", children=["b"]),
        )

        with pytest.raises(err.CodaError, match="twice"):
            list(built.walk())

    def test_path_of_a_self_parented_page_terminates(self):
        built = tree(page("a", parent="a"))

        assert [p.id for p in built.path("a")] == ["a"]

    def test_an_empty_doc(self):
        built = tree()

        assert built.roots == [] and len(built) == 0


class TestNavigation:
    def test_path_from_root_down(self):
        built = tree(
            page("a", children=["b"]),
            page("b", parent="a", children=["c"]),
            page("c", parent="b"),
        )

        assert [p.id for p in built.path("c")] == ["a", "b", "c"]

    def test_lookup_by_id(self):
        built = tree(page("a", name="Launch"))

        assert built["a"].name == "Launch"

    def test_iterating_walks_the_tree(self):
        built = tree(page("a", children=["b"]), page("b", parent="a"))

        assert [p.id for p in built] == ["a", "b"]

    def test_hidden_pages_can_be_left_out(self):
        built = tree(
            page("a", children=["b"]),
            page("b", parent="a", hidden=True),
        )

        assert [p.id for p, _ in built.walk(include_hidden=False)] == ["a"]
        assert [p.id for p, _ in built.walk()] == ["a", "b"]


def test_built_from_a_real_listing(main_document, mock_json_response):
    """The repo's own fixture is a parent and its child."""
    from tests.conftest import BASE_URL

    mock_json_response(BASE_URL + "/docs/doc_id/pages", "get_sections.json")
    built = main_document.page_tree()

    assert [p.id for p in built.roots] == ["section_id"]
    assert [p.id for p in built.children_of("section_id")] == ["section_id-1"]
    assert [p.name for p, _ in built.walk()] == ["Main Page", "Second Page"]
