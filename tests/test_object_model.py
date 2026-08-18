"""
Lookups and mutations on the `Table` / `Row` / `Column` objects.

Reuses the mocking the existing test modules established: a list of
(path, fixture file, kwargs) fed to `mock_json_responses`, and the
`main_table` / `main_document` fixtures from conftest.
"""

import pytest

from codaio import Cell, Column, Row, err
from tests.conftest import BASE_URL

TABLE_URL = BASE_URL + "/docs/doc_id/tables/table_id/"


@pytest.fixture
def mock_object_responses(mock_json_responses):
    responses = [
        ("rows?useColumnNames=False", "get_rows.json", {}),
        ("columns", "get_columns.json", {}),
        ("column/column_id", "get_column.json", {}),
        ("rows/index_id", "get_row.json", {}),
        ("rows/index_id", "put_row.json", {"method": "PUT"}),
        ("rows/no_such_id", "row_not_found.json", {"status": 404}),
    ]
    mock_json_responses(responses, base_url=TABLE_URL)


@pytest.mark.usefixtures("mock_object_responses")
class TestColumnLookup:
    def test_get_column_by_name(self, main_table):
        column = main_table.get_column_by_name("Alpha")
        assert isinstance(column, Column)
        assert column.name == "Alpha"

    def test_unknown_name_raises_column_not_found(self, main_table):
        with pytest.raises(err.ColumnNotFound):
            main_table.get_column_by_name("NoSuchColumn")

    def test_duplicate_names_raise_ambiguous_name(self, main_table):
        # Coda allows two columns to share a name, so the lookup has to refuse
        # rather than silently pick one.
        columns = main_table.columns()
        main_table.columns_storage = columns + [columns[0]]

        with pytest.raises(err.AmbiguousName):
            main_table.get_column_by_name(columns[0].name)


@pytest.mark.usefixtures("mock_object_responses")
class TestRowLookup:
    def test_getitem_by_column_object(self, main_table):
        row, column = main_table.rows()[0], main_table.columns()[0]
        assert row[column].column == column

    def test_getitem_by_column_id(self, main_table):
        row = main_table.rows()[0]
        assert row["column_id"].column.id == "column_id"

    def test_getitem_falls_back_to_column_name(self, main_table):
        # "Alpha" is a name, not an id, so this only resolves via the
        # name lookup after the id lookup misses.
        row = main_table.rows()[0]
        assert row["Alpha"].column.name == "Alpha"

    def test_getitem_with_unknown_key_raises(self, main_table):
        with pytest.raises((KeyError, err.ColumnNotFound)):
            main_table.rows()[0]["not_a_column"]

    def test_get_cell_by_column_id_missing_raises_keyerror(self, main_table):
        # column_id-4 exists on the table but the fixture rows carry no
        # value for it
        with pytest.raises(KeyError):
            main_table.rows()[0].get_cell_by_column_id("column_id-4")

    def test_row_columns_delegates_to_its_table(self, main_table):
        assert main_table.rows()[0].columns() == main_table.columns()


@pytest.mark.usefixtures("mock_object_responses")
class TestRowMutation:
    def test_setitem_sends_a_put(self, main_table, mocked_responses):
        row = main_table.rows()[0]

        row["column_id"] = "new value"

        puts = [c for c in mocked_responses.calls if c.request.method == "PUT"]
        assert len(puts) == 1
        assert b"new value" in puts[0].request.body

    def test_setitem_does_not_update_the_row_in_place(self, main_table):
        """
        Current behaviour, pinned rather than endorsed.

        `__setitem__` assigns to `cell.value_storage`, but `Row.cells()`
        builds fresh `Cell` objects out of `Row.values` on every call, so it
        is mutating a throwaway. `Row.values` is untouched and a read-back
        still shows the old value until `refresh()` is called.
        """
        row = main_table.rows()[0]
        before = row["column_id"].value

        row["column_id"] = "new value"

        assert row["column_id"].value == before

    def test_setitem_on_unknown_column_raises(self, main_table):
        with pytest.raises((KeyError, err.ColumnNotFound)):
            main_table.rows()[0]["not_a_column"] = "x"


@pytest.mark.usefixtures("mock_object_responses")
class TestTableUpdateRow:
    def test_accepts_a_row_object(self, main_table, mocked_responses):
        row = main_table.rows()[0]
        cells = [Cell(main_table.columns()[0], "updated")]

        main_table.update_row(row, cells)

        puts = [c for c in mocked_responses.calls if c.request.method == "PUT"]
        assert len(puts) == 1
        assert b"updated" in puts[0].request.body

    def test_accepts_a_row_id_string(self, main_table, mocked_responses):
        cells = [Cell(main_table.columns()[0], "updated")]

        main_table.update_row("index_id", cells)

        assert any(c.request.method == "PUT" for c in mocked_responses.calls)

    def test_rejects_anything_else(self, main_table):
        cells = [Cell(main_table.columns()[0], "updated")]
        with pytest.raises(TypeError):
            main_table.update_row(12345, cells)


@pytest.mark.usefixtures("mock_object_responses")
class TestUpsertKeyColumns:
    def test_rejects_a_key_column_that_is_neither_column_nor_str(self, main_table):
        cells = [Cell(main_table.columns()[0], "x")]
        with pytest.raises(err.ColumnNotFound):
            main_table.upsert_rows([cells], key_columns=[12345])


@pytest.mark.usefixtures("mock_object_responses")
class TestErrorBranches:
    def test_table_getitem_rejects_other_types(self, main_table):
        with pytest.raises(ValueError):
            main_table[12345]

    def test_row_getitem_rejects_other_types(self, main_table):
        with pytest.raises(KeyError):
            main_table.rows()[0][12345]

    def test_upsert_rejects_key_columns_that_is_not_a_list(self, main_table):
        cells = [Cell(main_table.columns()[0], "x")]
        with pytest.raises(err.ColumnNotFound):
            main_table.upsert_rows([cells], key_columns="Alpha")

    def test_upsert_accepts_key_columns_as_strings(self, main_table, mock_json_response):
        mock_json_response(TABLE_URL + "rows", "empty.json", method="POST")
        cells = [Cell(main_table.columns()[0], "x")]

        main_table.upsert_rows([cells], key_columns=["column_id"])

    def test_find_row_by_column_id_returns_empty_list(
        self, main_table, mock_json_response
    ):
        mock_json_response(
            TABLE_URL + "rows?useColumnNames=False&query=column_id%3A%22nope%22",
            "empty.json",
        )
        assert main_table.find_row_by_column_id_and_value("column_id", "nope") == []

    def test_row_delete_goes_through_its_table(self, main_table, mock_json_response,
                                               mocked_responses):
        mock_json_response(TABLE_URL + "rows/index_id", "empty.json", method="DELETE")
        main_table.rows()[0].delete()

        assert any(c.request.method == "DELETE" for c in mocked_responses.calls)


class TestDocument:
    def test_missing_document_raises_not_found(self, coda, mock_json_response):
        """
        A missing doc surfaces as `err.NotFound` from `handle_response`, not
        as the `err.DocumentNotFound` that `Document.__attrs_post_init__`
        appears to raise -- see `test_empty_body_branches_are_unreachable`.
        """
        from codaio import Document

        mock_json_response(BASE_URL + "/docs/no_such_doc/", "not_found.json", status=404)
        with pytest.raises(err.NotFound):
            Document("no_such_doc", coda=coda)

    def test_get_table_missing_raises_not_found(
        self, main_document, mock_json_response
    ):
        mock_json_response(
            BASE_URL + "/docs/doc_id/tables/no_such_table",
            "not_found.json",
            status=404,
        )
        with pytest.raises(err.NotFound):
            main_document.get_table("no_such_table")

    def test_empty_body_branches_are_unreachable(self, coda, mock_json_response):
        """
        Documents a dead branch rather than exercising it.

        `Document.__attrs_post_init__` guards with `if not data: raise
        err.DocumentNotFound`, and `Document.get_table` with `if table_data:
        ... raise err.TableNotFound`. Neither can fire: `handle_response`
        turns an empty 200 body into `{"status": 200}`, which is truthy, and
        a genuinely absent doc or table is a 404 that `handle_response` has
        already raised as `err.NotFound`. So `err.DocumentNotFound` and
        `err.TableNotFound` are never raised by this library.
        """
        mock_json_response(BASE_URL + "/docs/empty_doc/", "empty.json")
        assert coda.get("/docs/empty_doc/") == {"status": 200}

    def test_list_tables(self, main_document, mock_json_response):
        from codaio import Table

        mock_json_response(BASE_URL + "/docs/doc_id/tables", "get_tables.json")
        tables = main_document.list_tables()

        assert tables
        assert all(isinstance(t, Table) for t in tables)

    def test_from_credentials_uses_the_named_profile(
        self, fake_keyring, mock_json_response
    ):
        from codaio import Document

        fake_keyring.set_password("codaio", "research", "tok")
        mock_json_response(BASE_URL + "/docs/doc_id/", "get_doc.json")

        doc = Document.from_credentials("doc_id", keyring_profile="research")
        assert doc.coda.api_key == "tok"


class TestRawOptionalParams:
    def test_create_doc_passes_source_and_timezone(self, coda, mocked_responses):
        mocked_responses.add("POST", BASE_URL + "/docs", json={"id": "new"})
        coda.create_doc("Title", source_doc="src", tz="Europe/Berlin")

        body = mocked_responses.calls[0].request.body
        assert b"src" in body and b"Europe/Berlin" in body

    def test_list_rows_passes_use_column_names(self, coda, mocked_responses):
        mocked_responses.add(
            "GET", BASE_URL + "/docs/d1/tables/t1/rows", json={"items": []}
        )
        coda.list_rows("d1", "t1", use_column_names=True)

        assert "useColumnNames=True" in mocked_responses.calls[0].request.url

    def test_delete_can_send_a_body(self, coda, mocked_responses):
        mocked_responses.add("DELETE", BASE_URL + "/docs/d1", json={})
        coda.delete("/docs/d1", data={"confirm": True})

        assert mocked_responses.calls[0].request.body

    def test_list_rows_merges_use_column_names_into_supplied_data(
        self, coda, mocked_responses
    ):
        mocked_responses.add(
            "GET", BASE_URL + "/docs/d1/tables/t1/rows", json={"items": []}
        )
        coda.list_rows("d1", "t1", use_column_names=True, data={"valueFormat": "rich"})

        url = mocked_responses.calls[0].request.url
        assert "useColumnNames=True" in url
        assert "valueFormat=rich" in url

    def test_list_views(self, coda, mocked_responses):
        mocked_responses.add(
            "GET", BASE_URL + "/docs/d1/tables", json={"items": []}
        )
        coda.list_views("d1")

        assert "tableTypes=view" in mocked_responses.calls[0].request.url

    def test_document_from_environment(self, monkeypatch, mock_json_response):
        from codaio import Document

        monkeypatch.setenv("CODA_API_KEY", "tok")
        mock_json_response(BASE_URL + "/docs/doc_id/", "get_doc.json")

        assert Document.from_environment("doc_id").coda.api_key == "tok"

    def test_document_list_sections(self, main_document, mock_json_response):
        from codaio.objects.page import Section

        mock_json_response(BASE_URL + "/docs/doc_id/pages", "get_sections.json")
        sections = main_document.list_sections()

        assert sections
        assert all(isinstance(s, Section) for s in sections)


class TestFindRowByColumnName:
    def test_returns_matching_rows(self, main_table, mock_json_response):
        mock_json_response(
            TABLE_URL + 'rows?useColumnNames=False&query=%22Alpha%22%3A%22value-Alpha%22',
            "get_row_by_query.json",
        )
        rows = main_table.find_row_by_column_name_and_value("Alpha", "value-Alpha")

        assert rows
        assert all(isinstance(r, Row) for r in rows)

    def test_returns_empty_list_when_nothing_matches(
        self, main_table, mock_json_response
    ):
        mock_json_response(
            TABLE_URL + 'rows?useColumnNames=False&query=%22Alpha%22%3A%22nope%22',
            "empty.json",
        )
        assert main_table.find_row_by_column_name_and_value("Alpha", "nope") == []
