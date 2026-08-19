"""
The query parameters `GET /rows` accepts, and reading rows lazily.

`valueFormat` is the one that matters most and was not exposed at all, so every
read used the API's `simple` default -- which joins array values into a
comma-delimited string that cannot be taken apart again once any value contains
a comma. A multiselect was silently lossy.
"""

import pytest

from codaio import err
from tests.conftest import BASE_URL

TABLE_URL = BASE_URL + "/docs/doc_id/tables/table_id/"


def rows_url(**params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return TABLE_URL + "rows" + ("?" + query if query else "")


class TestValueFormat:
    def test_it_reaches_the_request(self, main_table, mocked_responses):
        mocked_responses.add("GET", TABLE_URL + "rows", json={"items": []})
        main_table.rows(value_format="rich")

        assert "valueFormat=rich" in mocked_responses.calls[-1].request.url

    def test_an_unknown_format_is_refused(self, coda):
        with pytest.raises(err.InvalidQuery, match="simpleWithArrays"):
            coda.list_rows("d1", "t1", value_format="verbose")

    def test_to_dict_does_not_use_the_lossy_default(self, main_table,
                                                    mock_json_responses,
                                                    mocked_responses):
        """
        `simple` joins arrays into a comma-delimited string. A multiselect whose
        value contains a comma cannot be recovered from that, so the documented
        pandas path must not use it.
        """
        mock_json_responses(
            [("columns", "get_columns.json", {})], TABLE_URL)
        mocked_responses.add("GET", TABLE_URL + "rows", json={"items": []})

        main_table.to_dict()

        assert "valueFormat=simpleWithArrays" in mocked_responses.calls[-1].request.url


class TestSortBy:
    def test_it_reaches_the_request(self, main_table, mocked_responses):
        mocked_responses.add("GET", TABLE_URL + "rows", json={"items": []})
        main_table.rows(sort_by="updatedAt")

        assert "sortBy=updatedAt" in mocked_responses.calls[-1].request.url

    def test_an_unknown_order_is_refused(self, coda):
        with pytest.raises(err.InvalidQuery, match="createdAt"):
            coda.list_rows("d1", "t1", sort_by="alphabetical")

    def test_natural_order_cannot_ask_for_hidden_rows(self, coda, mocked_responses):
        """
        The API answers 400. Saying so here costs a round trip less, and explains
        why: `natural` is the order shown in the app, which only exists for rows
        the app shows.
        """
        before = len(mocked_responses.calls)

        with pytest.raises(err.InvalidQuery, match="natural"):
            coda.list_rows("d1", "t1", sort_by="natural", visible_only=False)

        assert len(mocked_responses.calls) == before

    def test_natural_order_alone_is_fine(self, main_table, mocked_responses):
        mocked_responses.add("GET", TABLE_URL + "rows", json={"items": []})
        main_table.rows(sort_by="natural")

        assert "sortBy=natural" in mocked_responses.calls[-1].request.url


class TestVisibleOnly:
    def test_it_reaches_the_request(self, main_table, mocked_responses):
        mocked_responses.add("GET", TABLE_URL + "rows", json={"items": []})
        main_table.rows(visible_only=True)

        assert "visibleOnly=True" in mocked_responses.calls[-1].request.url


class TestLazyRows:
    def test_the_first_row_costs_one_request(self, main_table, mocked_responses):
        mocked_responses.add(
            "GET", TABLE_URL + "rows",
            json={"items": [{"id": "i-1", "type": "row", "values": {}}],
                  "nextPageLink": TABLE_URL + "rows?page=2"})
        mocked_responses.add(
            "GET", TABLE_URL + "rows?page=2",
            json={"items": [{"id": "i-2", "type": "row", "values": {}}]})

        before = len(mocked_responses.calls)
        rows = main_table.iter_rows()
        next(rows)

        assert len(mocked_responses.calls) == before + 1

    def test_it_walks_every_page(self, main_table, mocked_responses):
        mocked_responses.add(
            "GET", TABLE_URL + "rows",
            json={"items": [{"id": "i-1", "type": "row", "values": {}}],
                  "nextPageLink": TABLE_URL + "rows?page=2"})
        mocked_responses.add(
            "GET", TABLE_URL + "rows?page=2",
            json={"items": [{"id": "i-2", "type": "row", "values": {}}]})

        assert [r.id for r in main_table.iter_rows()] == ["i-1", "i-2"]

    def test_a_total_limit_stops_before_the_next_request(self, main_table,
                                                         mocked_responses):
        mocked_responses.add(
            "GET", TABLE_URL + "rows",
            json={"items": [{"id": "i-1", "type": "row", "values": {}},
                            {"id": "i-2", "type": "row", "values": {}}],
                  "nextPageLink": TABLE_URL + "rows?page=2"})
        mocked_responses.add(
            "GET", TABLE_URL + "rows?page=2",
            json={"items": [{"id": "i-3", "type": "row", "values": {}}]})

        before = len(mocked_responses.calls)
        got = list(main_table.iter_rows(limit=2))

        assert [r.id for r in got] == ["i-1", "i-2"]
        assert len(mocked_responses.calls) == before + 1


class TestListViews:
    def test_table_types_is_a_parameter_not_part_of_the_path(self, coda,
                                                             mocked_responses):
        """
        It used to be glued onto the path *and* passed as params, so a caller who
        supplied their own sent it twice.
        """
        mocked_responses.add("GET", BASE_URL + "/docs/d1/tables", json={"items": []})
        coda.list_views("d1")

        url = mocked_responses.calls[-1].request.url
        assert url.count("tableTypes") == 1
        assert "tableTypes=view" in url

    def test_a_caller_can_override_it(self, coda, mocked_responses):
        mocked_responses.add("GET", BASE_URL + "/docs/d1/tables", json={"items": []})
        coda.list_views("d1", data={"tableTypes": "table,view"})

        url = mocked_responses.calls[-1].request.url
        assert url.count("tableTypes") == 1
