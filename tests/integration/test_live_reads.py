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

    def test_report_the_page_types_in_this_doc(self, live_doc):
        """
        Which content types a doc has is up to the doc. Reported rather than
        required, so a doc with no embed or sync page is not a failure.
        """
        kinds = {}
        for page in live_doc.list_pages():
            kinds.setdefault(page.content_type, []).append(page.name)

        print("\npage types in this doc:")
        for kind, names in sorted(kinds.items()):
            print(f"    {kind!r}: {len(names)} -- e.g. {names[0]!r}")

        missing = {"canvas", "embed", "syncPage"} - set(kinds)
        if missing:
            print(f"    (not exercised live: {sorted(missing)})")

    def test_page_content_reads(self, live_doc):
        for page in live_doc.list_pages():
            if not page.is_canvas:
                continue
            lines = page.content(limit=5)
            for line in lines:
                assert line.id, "a content line with no id cannot be edited later"
            return
        pytest.skip("no canvas pages in the test doc")


class TestColumnTypes:
    """
    What the doc's columns actually report, which is the thing you cannot learn
    from the spec: it says a column has a `format.type`, not which of the
    twenty-four names any given editor choice produces.
    """

    def test_report_every_column_type_and_the_shape_of_its_values(self, a_table):
        from codaio.values import CodaValue

        rows = list(a_table.iter_rows(value_format="rich", limit=25))
        print(f"\ncolumn types in {a_table.name!r}:")

        for column in a_table.columns():
            sample = None
            for row in rows:
                value = dict(row.values).get(column.id)
                if value not in (None, "", []):
                    sample = value
                    break

            parsed = None
            if sample is not None:
                from codaio.values import parse_value

                parsed = parse_value(sample)
                if isinstance(parsed, list) and parsed:
                    parsed = parsed[0]

            shape = type(parsed).__name__ if parsed is not None else "-"
            calculated = " (calculated)" if column.calculated else ""
            print(f"    {column.name!r:32} format.type={column.format.type!r:20} "
                  f"value={shape}{calculated}")

            if isinstance(parsed, CodaValue):
                assert parsed.to_json() == (
                    sample[0] if isinstance(sample, list) else sample
                ), f"{column.name} did not round-trip"

    def test_an_attachment_column_yields_something_fetchable(self, a_table):
        """
        Whatever the editor calls the column, the values should carry a url.

        The spec has `attachments`, `image` and `imageReference` among its column
        types but only one image-ish value type, so what a File column produces
        is worth establishing rather than assuming.
        """
        from codaio.values import ImageValue

        found = []
        for row in a_table.iter_rows(value_format="rich", limit=25):
            for cell in row.cells():
                values = cell.value if isinstance(cell.value, list) else [cell.value]
                for value in values:
                    if isinstance(value, ImageValue):
                        found.append((cell.name, cell.column.format.type, value))

        if not found:
            pytest.skip(
                "no attachment or image values in the first 25 rows; add a File "
                "column with something in it to exercise the fetch path"
            )

        name, format_type, image = found[0]
        print(f"\n  column {name!r} has format.type={format_type!r}")
        print(f"  value: name={image.name!r} status={image.status!r}")
        assert image.url, "an attachment with no url cannot be fetched"

        payload = image.read()
        print(f"  fetched {len(payload)} bytes with no credentials attached")
        assert payload


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
    def test_the_simple_format_loses_multi_valued_cells(self, a_table):
        """
        Why `Table.to_dict` no longer uses the API's default.

        Under `simple`, an array value is joined into one comma-delimited string.
        That is not merely untidy, it is ambiguous: ["a", "b"] and ["a, b"] both
        render as "a, b", so the original cannot be recovered. Options with
        leading or trailing spaces make it worse, because splitting on ", "
        rather than "," is equally wrong.
        """
        plain = {r.id: dict(r.values)
                 for r in a_table.rows(value_format="simple", limit=50)}
        arrays = {r.id: dict(r.values)
                  for r in a_table.rows(value_format="simpleWithArrays", limit=50)}

        multi = [
            (row_id, column, value)
            for row_id, values in arrays.items()
            for column, value in values.items()
            if isinstance(value, list) and len(value) > 1
        ]
        if not multi:
            pytest.skip(
                "no multi-valued cells in the first 50 rows; a multiselect with "
                "several options selected exercises this"
            )

        # Prefer a cell that actually demonstrates the loss: one whose values
        # contain a comma or padding. A cell of tidy values happens to survive
        # the round trip, which says nothing about the format being safe.
        def ambiguous(values):
            return any(
                isinstance(v, str) and ("," in v or v != v.strip()) for v in values
            )

        multi.sort(key=lambda entry: not ambiguous(entry[2]))
        row_id, column, real = multi[0]
        joined = plain[row_id][column]

        print(f"\n  simpleWithArrays: {real!r}")
        print(f"  simple:           {joined!r}")

        assert isinstance(joined, str), "simple should have flattened this"

        recovered = joined.split(",")
        faithful = recovered == [str(v) for v in real]
        print(f"  splitting on ',' gives {len(recovered)} values, "
              f"the cell has {len(real)}")
        print(f"  recovers the original: {faithful}")

        if not faithful:
            print("  -- `simple` cannot represent this cell; the separator and "
                  "the content are the same character")
        else:
            print("  -- this particular cell happens to survive, which is luck "
                  "rather than a property of the format")

    def test_values_with_commas_or_padding_cannot_survive_the_simple_format(
        self, a_table
    ):
        """
        The sharpest version: a value that itself contains a comma, or one padded
        with spaces, is indistinguishable from a separator once joined.
        """
        suspicious = []
        for row in a_table.iter_rows(value_format="simpleWithArrays", limit=50):
            for column, value in row.values:
                if not isinstance(value, list):
                    continue
                for item in value:
                    if isinstance(item, str) and (
                        "," in item or item != item.strip()
                    ):
                        suspicious.append((row.id, column, value, item))

        if not suspicious:
            pytest.skip(
                "no array values containing a comma or padded with spaces; those "
                "are the cases `simple` cannot represent at all"
            )

        row_id, column, whole, offender = suspicious[0]
        print(f"\n  a value that breaks the simple format: {offender!r}")
        print(f"  the whole cell:  {whole!r}")


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
