import pytest

from codaio import Cell, err
from tests.conftest import BASE_URL

BASE_TABLE_URL = BASE_URL + "/docs/doc_id/tables/table_id/"
MUTATION_URL = BASE_URL + "/mutationStatus/request_id"


@pytest.fixture
def writable_table(mock_json_responses, mock_json_response, main_table):
    """A table whose one row can be written to, and whose write then completes."""
    mock_json_responses(
        [
            ("rows?useColumnNames=False", "get_rows.json", {}),
            ("rows?useColumnNames=False", "get_updated_rows.json", {}),
            ("columns", "get_columns.json", {}),
            ("rows/index_id", "put_row.json", {"method": "PUT", "status": 202}),
            ("rows/index_id", "get_updated_row.json", {}),
        ],
        BASE_TABLE_URL,
    )
    mock_json_response(MUTATION_URL, "mutation_completed.json")
    return main_table


class TestCell:
    def test_set_value(self, writable_table):
        cell_a = writable_table.rows()[0].cells()[0]
        assert isinstance(cell_a, Cell)

        cell_a.value = "completely_new_value"

        assert cell_a.value == "completely_new_value"
        fetched_again = writable_table.rows()[0].cells()[0]
        assert fetched_again.value == "completely_new_value"

    def test_a_write_waits_for_the_api_to_report_it_done(
        self, writable_table, mocked_responses
    ):
        """
        The wait is on `mutationStatus`, not on the value coming back matching.

        The old loop re-read the row until what it read equalled what was sent,
        with no bound at all -- so any value Coda coerced ("$12.34" to 12.34, a
        reformatted date, a normalised select option) meant spinning forever.
        """
        writable_table.rows()[0].cells()[0].value = "x"

        assert any(
            call.request.url.startswith(MUTATION_URL)
            for call in mocked_responses.calls
        )

    def test_a_write_can_skip_waiting(self, writable_table, mocked_responses):
        """Bulk work wants to fire the writes and wait once, or not at all."""
        cell = writable_table.rows()[0].cells()[0]

        mutation = cell.set("x", wait=False)

        assert mutation.request_id == "request_id"
        assert not any(
            call.request.url.startswith(MUTATION_URL)
            for call in mocked_responses.calls
        )

    def test_a_write_that_never_completes_gives_up(
        self, mock_json_responses, mock_json_response, main_table, fake_clock
    ):
        mock_json_responses(
            [
                ("rows?useColumnNames=False", "get_rows.json", {}),
                ("columns", "get_columns.json", {}),
                ("rows/index_id", "put_row.json", {"method": "PUT", "status": 202}),
            ],
            BASE_TABLE_URL,
        )
        mock_json_response(MUTATION_URL, "empty.json")

        cell = main_table.rows()[0].cells()[0]
        mutation = cell.set("x", wait=False)

        with pytest.raises(err.MutationTimeout, match="request_id"):
            mutation.wait(timeout=5, sleep=fake_clock.sleep, clock=fake_clock)
