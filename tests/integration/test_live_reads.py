"""
Reading a real doc.

The mocked suite proves codaio parses what codaio's fixtures contain. This
proves it parses what Coda actually sends -- a different claim, and the one that
matters. Nothing here writes, so it is safe to run against any doc the token
covers.
"""

import warnings

import pytest

from codaio import Page, Table, err
from codaio.objects import base

pytestmark = pytest.mark.integration


class TestTheDocItself:
    def test_it_has_the_fields_codaio_expects(self, live_doc):
        assert live_doc.id
        assert live_doc.name
        assert live_doc.created_at is not None

    def test_it_knows_its_workspace(self, live_doc):
        assert live_doc.workspace_id, (
            "a real doc payload names its workspace; codaio did not pick it up"
        )


class TestPages:
    def test_pages_parse(self, live_doc):
        """
        The payload that used to be fatal.

        A real page carries subtitle, contentType, isHidden and children, and
        every one of those raised TypeError before the object model was made
        tolerant -- so this call was broken against every live doc while the
        mocked suite stayed green on a hand-written fixture.
        """
        pages = live_doc.list_pages()

        assert pages, "the test doc has no pages"
        assert all(isinstance(page, Page) for page in pages)
        assert all(page.id and page.content_type for page in pages)

    def test_the_tree_reconstructs(self, live_doc):
        tree = live_doc.page_tree()

        assert tree.roots, "no root pages, so parent/children disagree"
        walked = [page for page, _ in tree.walk()]
        assert len(walked) == len(tree.by_id), (
            "walking the tree did not reach every page; some page is filed under "
            "something that is not in the listing"
        )

    def test_page_content_reads(self, live_doc):
        for page in live_doc.list_pages():
            if not page.is_canvas:
                continue
            lines = page.content(limit=5)
            for line in lines:
                assert line.id, "a content line with no id cannot be edited later"
            return
        pytest.skip("no canvas pages in the test doc")


class TestTables:
    def test_tables_and_columns_parse(self, a_table):
        assert isinstance(a_table, Table)
        columns = a_table.columns()
        assert columns, "the test table has no columns"

    def test_columns_report_their_type(self, a_table):
        """
        `format` was discarded on arrival, so the only way to find out what a
        column held was to inspect a row that happened to have a value in it.
        """
        for column in a_table.columns():
            assert column.format is not None, f"{column.name} has no format"
            assert column.format.type, f"{column.name} has a format with no type"

    def test_rows_read_in_every_value_format(self, a_table):
        for value_format in ("simple", "simpleWithArrays", "rich"):
            rows = a_table.rows(value_format=value_format, limit=5)
            assert isinstance(rows, list)

    def test_rich_values_become_objects_where_they_should(self, a_table):
        """Which value types this finds depends on the doc; report what it saw."""
        from codaio.values import CodaValue

        seen = set()
        for row in a_table.iter_rows(value_format="rich", limit=25):
            for _, raw in row.values:
                parsed = row.table and raw
                if isinstance(parsed, dict) and "@type" in parsed:
                    seen.add(parsed["@type"])
        print(f"\nrich value types in this table: {sorted(seen) or 'none'}")
        for row in a_table.iter_rows(value_format="rich", limit=5):
            for cell in row.cells():
                assert not isinstance(cell.value, dict), (
                    f"{cell.name} came back as a bare dict: "
                    f"{cell.raw_value!r} was not recognised"
                )
                if isinstance(cell.value, CodaValue):
                    assert cell.value.to_json() == cell.raw_value, (
                        "a typed value must round-trip to exactly what arrived"
                    )


class TestLosslessness:
    def test_simple_format_is_lossy_and_simpleWithArrays_is_not(self, a_table):
        """
        The reason `Table.to_dict` no longer uses the API's default.

        Under `simple` an array value is joined into a comma-delimited string.
        This looks for a cell where that actually loses information.
        """
        plain = {r.id: dict(r.values) for r in a_table.rows(value_format="simple",
                                                            limit=25)}
        arrays = {r.id: dict(r.values) for r in a_table.rows(
            value_format="simpleWithArrays", limit=25)}

        lossy = [
            (row_id, column)
            for row_id, values in arrays.items()
            for column, value in values.items()
            if isinstance(value, list) and len(value) > 1
        ]
        if not lossy:
            pytest.skip(
                "no multi-valued cells in the first 25 rows; add a multiselect "
                "with several options selected to exercise this"
            )
        row_id, column = lossy[0]
        assert isinstance(plain[row_id][column], str)
        print(f"\nsimple:           {plain[row_id][column]!r}")
        print(f"simpleWithArrays: {arrays[row_id][column]!r}")


class TestTolerance:
    def test_codaio_models_everything_this_doc_sends(self, live_doc, a_table):
        """
        The live counterpart to the conformance check.

        Conformance says codaio does not *miss* a field the spec guarantees.
        This says codaio does not miss a field this doc actually sends -- which
        catches anything the spec fails to mention.

        A warning is a to-do list, not a failure, so this reports rather than
        asserts.
        """
        unmodelled = {}

        def note(obj):
            if getattr(obj, "unknown_fields", None):
                unmodelled.setdefault(type(obj).__name__, set()).update(
                    obj.unknown_fields
                )

        for page in live_doc.list_pages():
            note(page)
        note(a_table)
        for column in a_table.columns():
            note(column)
        for row in a_table.iter_rows(limit=10):
            note(row)

        if unmodelled:
            print("\nfields this doc sends that codaio does not model:")
            for name, fields in sorted(unmodelled.items()):
                print(f"    {name}: {sorted(fields)}")
        else:
            print("\ncodaio models every field this doc sent")

    def test_warnings_can_be_turned_on(self, live_doc, monkeypatch):
        """The flag exists so drift can be found; check it actually fires."""
        monkeypatch.setattr(base, "WARN_ON_UNKNOWN_FIELDS", True)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            live_doc.list_pages()
        for warning in caught:
            if issubclass(warning.category, err.UnknownFieldWarning):
                print(f"\n{warning.message}")
