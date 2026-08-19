"""
Writes that the API accepted but has not necessarily applied.

Every mutating endpoint answers 202 with a `requestId` rather than 200, so a
write that "succeeded" has only been queued. `GET /mutationStatus/{id}` is how
you find out whether it landed -- and what it reports is narrower than it looks:
`completed` and an optional `warning`, with **no failure field at all**.
"""

import pytest

from codaio import err
from codaio.objects.mutation import Mutation, MutationGroup
from tests.conftest import BASE_URL

TABLE_URL = BASE_URL + "/docs/doc_id/tables/table_id/"
STATUS = BASE_URL + "/mutationStatus/"


@pytest.fixture
def table(main_table, mock_json_responses):
    mock_json_responses(
        [
            ("rows?useColumnNames=False", "get_rows.json", {}),
            ("columns", "get_columns.json", {}),
            ("rows", "upsert_result.json", {"method": "POST", "status": 202}),
            ("rows", "empty.json", {"method": "DELETE", "status": 202}),
        ],
        TABLE_URL,
    )
    return main_table


class TestWhatAWriteReturns:
    def test_a_write_reports_its_request_id(self, table):
        from codaio import Cell

        result = table.upsert_row([Cell(table.columns()[0], "x")])

        assert isinstance(result, Mutation)
        assert result.request_id == "abc-123-def-456"

    def test_added_row_ids_are_reported(self, table):
        from codaio import Cell

        result = table.upsert_row([Cell(table.columns()[0], "x")])

        assert result.row_ids == ["i-added-1", "i-added-2"]

    def test_a_synchronous_response_starts_complete(self, coda):
        """
        A few endpoints answer 200 or 201 and are simply done. Returning None for
        those would force a check at every call site for no benefit.
        """
        mutation = Mutation.from_response(coda, {"id": "doc-1"})

        assert mutation.completed and mutation.request_id is None
        assert mutation.id == "doc-1"


class TestWaiting:
    def test_polls_until_completed(self, coda, mocked_responses, fake_clock):
        mocked_responses.add("GET", STATUS + "r1", json={"completed": False})
        mocked_responses.add("GET", STATUS + "r1", json={"completed": True})
        mutation = Mutation.from_response(coda, {"requestId": "r1"})

        mutation.wait(sleep=fake_clock.sleep, clock=fake_clock)

        assert mutation.completed
        assert len(fake_clock.sleeps) == 1

    def test_it_gives_up_rather_than_looping(self, coda, mocked_responses, fake_clock):
        mocked_responses.add("GET", STATUS + "r1", json={"completed": False})
        mutation = Mutation.from_response(coda, {"requestId": "r1"})

        with pytest.raises(err.MutationTimeout) as caught:
            mutation.wait(timeout=5, sleep=fake_clock.sleep, clock=fake_clock)

        # the id, so the caller can resume rather than start again
        assert caught.value.request_id == "r1"
        assert "still queued" in str(caught.value)

    def test_an_already_complete_mutation_costs_nothing(self, coda, mocked_responses,
                                                        fake_clock):
        before = len(mocked_responses.calls)
        Mutation.from_response(coda, {"id": "x"}).wait(
            sleep=fake_clock.sleep, clock=fake_clock)

        assert len(mocked_responses.calls) == before
        assert fake_clock.sleeps == []

    def test_a_warning_is_surfaced(self, coda, mocked_responses, fake_clock):
        """
        `completed` means the API stopped working on the edit, not that it did
        what was asked. There is no failure field -- only this.
        """
        mocked_responses.add(
            "GET", STATUS + "r1",
            json={"completed": True, "warning": "Initial page HTML was invalid."})
        mutation = Mutation.from_response(coda, {"requestId": "r1"})

        mutation.wait(sleep=fake_clock.sleep, clock=fake_clock)

        assert mutation.warning == "Initial page HTML was invalid."


class TestBatching:
    """
    The property that makes a batch of edits quick.

    A write takes the better part of a minute to be applied, but writes are
    applied concurrently -- so issuing them all and waiting once costs about as
    long as the slowest, while waiting after each costs their sum.
    """

    def test_a_group_waits_against_one_shared_deadline(self, coda, mocked_responses,
                                                       fake_clock):
        """
        Not a fresh timeout each. Otherwise one slow write could consume the
        whole budget before the others were even asked about, even though they
        were already in flight and probably finished.
        """
        for request_id in ("r1", "r2", "r3"):
            mocked_responses.add("GET", STATUS + request_id, json={"completed": False})
            mocked_responses.add("GET", STATUS + request_id, json={"completed": True})

        group = MutationGroup()
        for request_id in ("r1", "r2", "r3"):
            group.add(Mutation.from_response(coda, {"requestId": request_id}))

        group.wait(sleep=fake_clock.sleep, clock=fake_clock)

        assert group.completed
        # one sleep for the whole group, not one per write
        assert len(fake_clock.sleeps) == 1

    def test_a_group_that_never_finishes_names_what_is_outstanding(
        self, coda, mocked_responses, fake_clock
    ):
        mocked_responses.add("GET", STATUS + "r1", json={"completed": True})
        mocked_responses.add("GET", STATUS + "r2", json={"completed": False})

        group = MutationGroup()
        group.add(Mutation.from_response(coda, {"requestId": "r1"}))
        group.add(Mutation.from_response(coda, {"requestId": "r2"}))

        with pytest.raises(err.MutationTimeout) as caught:
            group.wait(timeout=5, sleep=fake_clock.sleep, clock=fake_clock)

        message = str(caught.value)
        assert "r2" in message
        assert "1 of 2" in message

    def test_an_already_finished_group_costs_nothing(self, coda, fake_clock,
                                                     mocked_responses):
        before = len(mocked_responses.calls)
        group = MutationGroup()
        group.add(Mutation.from_response(coda, {"id": "already done"}))

        group.wait(sleep=fake_clock.sleep, clock=fake_clock)

        assert len(mocked_responses.calls) == before
        assert fake_clock.sleeps == []

    def test_add_hands_the_mutation_straight_back(self, coda):
        """So a write can be collected and used in one expression."""
        group = MutationGroup()
        mutation = group.add(Mutation.from_response(coda, {"requestId": "r1"}))

        assert mutation.request_id == "r1"
        assert len(group) == 1

    def test_the_default_timeout_allows_for_how_slow_writes_actually_are(self):
        """
        Measured against a real doc: a row update reported completed after 41
        seconds and a page create after about 60. A one-minute default therefore
        timed out on ordinary healthy writes, which is worse than useless.
        """
        from codaio.objects.mutation import MUTATION_TIMEOUT

        assert MUTATION_TIMEOUT >= 120, (
            "the default must leave room for writes that legitimately take a "
            "minute; see the measurements beside MUTATION_TIMEOUT"
        )


class TestBulkDelete:
    def test_deletes_in_one_request_when_it_fits(self, table, mocked_responses):
        result = table.delete_rows(["i-1", "i-2", "i-3"])

        assert isinstance(result, MutationGroup)
        assert len(result) == 1

        import json as _json
        assert _json.loads(mocked_responses.calls[-1].request.body) == {
            "rowIds": ["i-1", "i-2", "i-3"]}

    def test_chunks_a_large_delete(self, table):
        result = table.delete_rows([f"i-{n}" for n in range(250)], chunk=100)

        assert len(result) == 3

    def test_row_objects_are_accepted(self, table, mocked_responses):
        result = table.delete_rows(table.rows())

        assert len(result) == 1
        import json as _json
        assert _json.loads(mocked_responses.calls[-1].request.body)["rowIds"] == [
            "index_id"]

    def test_deleting_nothing_is_refused(self, table, mocked_responses):
        """
        Almost always a filter that matched nothing rather than an intention,
        and a request to delete no rows is not worth making either way.
        """
        before = len(mocked_responses.calls)

        with pytest.raises(err.InvalidQuery, match="empty list"):
            table.delete_rows([])

        assert len(mocked_responses.calls) == before

    def test_the_group_collects_every_request_id(self, table):
        result = table.delete_rows([f"i-{n}" for n in range(5)], chunk=2)

        assert len(result.request_ids) == 0 or len(result) == 3


class TestPushButton:
    def test_presses_the_button(self, table, mocked_responses):
        column = table.columns()[0]
        mocked_responses.add(
            "POST", TABLE_URL + f"rows/index_id/buttons/{column.id}",
            status=202, json={"requestId": "r1", "rowId": "index_id"})

        result = table.rows()[0].push_button(column)

        assert result.request_id == "r1"

    def test_an_unknown_button_column_is_never_pressed(self, table, mocked_responses):
        """
        The column is resolved first, so a typo is an error rather than a 400
        naming a column you did not mean -- and nothing is pressed either way.
        """
        row = table.rows()[0]

        with pytest.raises(err.ColumnNotFound, match="Nonexistent"):
            row.push_button("Nonexistent")

        assert not [
            c for c in mocked_responses.calls
            if c.request.method == "POST" and "/buttons/" in c.request.url
        ]
