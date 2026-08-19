"""
`meta_to_dict` across the object model.

Two things matter: the shape is stable, and the parent object -- ultimately
the `Coda` holding the API token -- is left out unless explicitly asked for.
"""

import json

import pytest

from codaio import Coda, Document, Table, err
from tests.conftest import BASE_URL


@pytest.fixture
def mock_meta_responses(mock_json_responses):
    base_table_url = BASE_URL + "/docs/doc_id/tables/table_id/"
    responses = [
        ("rows?useColumnNames=False", "get_rows.json", {}),
        # Table.to_dict reads with simpleWithArrays rather than the API's lossy
        # `simple` default, so it asks for a different URL.
        (
            "rows?useColumnNames=False&valueFormat=simpleWithArrays",
            "get_rows.json",
            {},
        ),
        ("columns", "get_columns.json", {}),
        ("rows/index_id", "get_row.json", {}),
    ]
    mock_json_responses(responses, base_url=base_table_url)


@pytest.mark.usefixtures("mock_meta_responses")
class TestDocumentMeta:
    def test_expected_keys(self, main_document):
        meta = main_document.meta_to_dict()
        assert set(meta) == {
            "id",
            "type",
            "href",
            "name",
            "owner",
            "created_at",
            "updated_at",
            "browser_link",
        }

    def test_omits_coda_by_default(self, main_document):
        assert "coda" not in main_document.meta_to_dict()

    def test_includes_coda_on_request(self, main_document):
        meta = main_document.meta_to_dict(incl_coda=True)
        assert isinstance(meta["coda"], Coda)

    def test_values_match_the_document(self, main_document):
        meta = main_document.meta_to_dict()
        assert meta["id"] == main_document.id
        assert meta["name"] == main_document.name


@pytest.mark.usefixtures("mock_meta_responses")
class TestTableMeta:
    def test_includes_the_codaobject_keys(self, main_table):
        meta = main_table.meta_to_dict()
        assert {"id", "type", "href"} <= set(meta)

    def test_includes_table_keys(self, main_table):
        meta = main_table.meta_to_dict()
        assert {"name", "row_count", "browser_link", "table_type"} <= set(meta)

    def test_omits_document_by_default(self, main_table):
        assert "doc" not in main_table.meta_to_dict()

    def test_includes_document_on_request(self, main_table):
        assert isinstance(main_table.meta_to_dict(incl_doc=True)["doc"], Document)


@pytest.mark.usefixtures("mock_meta_responses")
class TestRowMeta:
    def test_expected_keys(self, main_table):
        meta = main_table.rows()[0].meta_to_dict()
        assert {"id", "type", "href", "name", "index", "browser_link"} <= set(meta)

    def test_excludes_the_row_values(self, main_table):
        # values are the data, not metadata
        assert "values" not in main_table.rows()[0].meta_to_dict()

    def test_omits_table_by_default(self, main_table):
        assert "table" not in main_table.rows()[0].meta_to_dict()

    def test_includes_table_on_request(self, main_table):
        meta = main_table.rows()[0].meta_to_dict(incl_table=True)
        assert isinstance(meta["table"], Table)


@pytest.mark.usefixtures("mock_meta_responses")
class TestColumnMeta:
    def test_expected_keys(self, main_table):
        meta = main_table.columns()[0].meta_to_dict()
        assert {"id", "type", "href", "name", "display", "calculated"} <= set(meta)

    def test_omits_table_by_default(self, main_table):
        assert "table" not in main_table.columns()[0].meta_to_dict()

    def test_includes_table_on_request(self, main_table):
        meta = main_table.columns()[0].meta_to_dict(incl_table=True)
        assert "table" in meta


@pytest.mark.usefixtures("mock_meta_responses")
class TestSerializesToJson:
    """
    These dicts hold datetimes and object references, so callers serialise
    them with json.dump(..., default=str). Anything not JSON-native has to
    survive that, and the API token must not appear in the result.
    """

    @pytest.mark.parametrize(
        "obj_of", [lambda t, d: d, lambda t, d: t, lambda t, d: t.rows()[0],
                   lambda t, d: t.columns()[0]]
    )
    def test_round_trips_through_json(self, main_table, main_document, obj_of):
        obj = obj_of(main_table, main_document)
        text = json.dumps(obj.meta_to_dict(), indent=4, default=str)
        assert json.loads(text)

    def test_token_absent_even_when_parents_are_included(
        self, main_table, main_document
    ):
        token = main_document.coda.api_key
        assert token

        dumps = [
            json.dumps(main_document.meta_to_dict(incl_coda=True), default=str),
            json.dumps(main_table.meta_to_dict(incl_doc=True), default=str),
            json.dumps(main_table.rows()[0].meta_to_dict(incl_table=True), default=str),
            json.dumps(
                main_table.columns()[0].meta_to_dict(incl_table=True), default=str
            ),
        ]
        for text in dumps:
            assert token not in text


@pytest.mark.usefixtures("mock_meta_responses")
class TestToDict:
    def test_a_column_with_no_value_is_omitted_not_invented(self, main_table):
        """
        A partly-filled row is ordinary, and must neither raise nor fabricate.

        It used to raise `KeyError`, taking `Table.to_dict` -- the documented
        pandas path -- down with it. Filling `None` instead would be worse for a
        stored copy: it cannot be told apart from a cell that is genuinely empty.
        So the column is simply absent. The repo's fixtures are shaped for this:
        get_columns.json has five columns, get_rows.json carries four.
        """
        row = main_table.rows()[0]
        as_dict = row.to_dict()

        carried = {c.id for c in main_table.columns()} & set(dict(row.values))
        assert set(as_dict) == {
            c.name for c in main_table.columns() if c.id in carried
        }
        assert "Delta" not in as_dict
        assert None not in as_dict.values()

    def test_a_row_sharing_no_column_with_its_table_raises(self, main_table):
        """
        Not a partial row but a mismatched one -- almost always `useColumnNames`.

        `values` is then keyed by column name, so every id lookup misses. Without
        this the result is a complete-looking set of keys with every value
        silently dropped, which is worse than any error.
        """
        from codaio import Row

        row = Row.from_json(
            {
                "id": "i-1",
                "type": "row",
                "name": "r",
                "index": 0,
                "values": {"Alpha": "value", "Beta": "other"},
            },
            table=main_table,
        )

        with pytest.raises(err.ColumnNotFound, match="useColumnNames"):
            row.to_dict()

    def test_table_to_dict_is_ragged_but_unions_to_every_column(self, main_table):
        """
        The property the documented pandas path depends on.

        `DataFrame` unions the keys of a list of dicts, so a ragged list still
        produces every column: an absent key becomes NaN, while a cell the API
        returned as empty keeps whatever it sent. That distinction is the reason
        a missing column is omitted rather than filled in, and it only survives
        if nothing fabricates keys. Asserted here without importing pandas, so
        the suite stays dependency-free.
        """
        rows = main_table.to_dict()
        carried = set()
        for row in rows:
            carried |= set(row)

        assert rows
        assert carried <= {c.name for c in main_table.columns()}
        assert any(set(row) != carried for row in rows) or len(rows) == 1

    def test_row_to_dict_is_column_name_keyed_when_values_are_complete(
        self, main_table
    ):
        row = main_table.rows()[0]
        # restrict the table to columns this row actually has values for
        row.table.columns_storage = [
            c for c in row.columns() if c.id in dict(row.values)
        ]

        d = row.to_dict()
        assert isinstance(d, dict)
        assert all(isinstance(k, str) for k in d)
        assert len(d) == len(dict(row.values))


@pytest.mark.usefixtures("mock_meta_responses")
class TestCell:
    def test_name_comes_from_the_column(self, main_table):
        cell = main_table.rows()[0].cells()[0]
        assert cell.name == cell.column.name

    def test_repr_shows_column_and_value(self, main_table):
        cell = main_table.rows()[0].cells()[0]
        text = repr(cell)
        assert cell.column.name in text
        assert str(cell.value) in text

    def test_value_reads_through_to_storage(self, main_table):
        cell = main_table.rows()[0].cells()[0]
        assert cell.value == cell.value_storage
