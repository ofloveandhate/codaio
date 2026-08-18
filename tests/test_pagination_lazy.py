"""
Pagination: laziness, and the guards on every hop.

The regression this file exists for is the last class in it. A failure partway
through a paginated walk used to be merged into the result as though it were
data, so callers received a silently truncated result set with no exception and
no way to notice.
"""

import pytest

from codaio import err

BASE_URL = "https://coda.io/apis/v1"


def _page(items, next_link=None):
    body = {"items": items}
    if next_link:
        body["nextPageLink"] = next_link
    return body


class TestLaziness:
    def test_first_page_costs_one_request(self, coda, mocked_responses):
        mocked_responses.add(
            "GET", BASE_URL + "/docs", json=_page([{"a": 1}], BASE_URL + "/docs?page=2")
        )
        mocked_responses.add("GET", BASE_URL + "/docs?page=2", json=_page([{"b": 2}]))

        pages = coda.iter_pages("/docs")
        next(pages)

        assert len(mocked_responses.calls) == 1

    def test_second_page_is_only_fetched_when_asked_for(self, coda, mocked_responses):
        mocked_responses.add(
            "GET", BASE_URL + "/docs", json=_page([{"a": 1}], BASE_URL + "/docs?page=2")
        )
        mocked_responses.add("GET", BASE_URL + "/docs?page=2", json=_page([{"b": 2}]))

        pages = coda.iter_pages("/docs")
        next(pages)
        next(pages)

        assert len(mocked_responses.calls) == 2

    def test_iter_items_flattens_across_pages(self, coda, mocked_responses):
        mocked_responses.add(
            "GET", BASE_URL + "/docs", json=_page([{"a": 1}], BASE_URL + "/docs?page=2")
        )
        mocked_responses.add("GET", BASE_URL + "/docs?page=2", json=_page([{"b": 2}]))

        assert list(coda.iter_items("/docs")) == [{"a": 1}, {"b": 2}]

    def test_iter_items_limit_stops_early_without_fetching_more(
        self, coda, mocked_responses
    ):
        """`limit` here is a total cap, and stops before the next request."""
        mocked_responses.add(
            "GET",
            BASE_URL + "/docs",
            json=_page([{"a": 1}, {"b": 2}], BASE_URL + "/docs?page=2"),
        )
        mocked_responses.add("GET", BASE_URL + "/docs?page=2", json=_page([{"c": 3}]))

        assert list(coda.iter_items("/docs", limit=2)) == [{"a": 1}, {"b": 2}]
        assert len(mocked_responses.calls) == 1


class TestEagerGet:
    def test_drained_result_drops_the_stale_cursor(self, coda, mocked_responses):
        """
        A fully-walked result must not still advertise a next page.

        The merge used to accumulate keys across pages, so `nextPageLink` from
        page one survived into a result that had already consumed every page.
        """
        mocked_responses.add(
            "GET", BASE_URL + "/docs", json=_page([{"a": 1}], BASE_URL + "/docs?page=2")
        )
        mocked_responses.add("GET", BASE_URL + "/docs?page=2", json=_page([{"b": 2}]))

        result = coda.get("/docs")

        assert result["items"] == [{"a": 1}, {"b": 2}]
        assert "nextPageLink" not in result

    def test_a_body_less_success_becomes_a_status_stub(self, coda, mocked_responses):
        """An empty body used to raise inside the branch meant to handle it."""
        mocked_responses.add("GET", BASE_URL + "/docs", body="", status=204)

        assert coda.get("/docs") == {"status": 204}


class TestOriginGuardOnEveryHop:
    """
    `nextPageLink` comes from the response *body*, so `requests`' cross-host
    stripping of the Authorization header never applies to it. Both the lazy and
    the eager path must therefore check it themselves.
    """

    @pytest.mark.parametrize("drain", [True, False], ids=["eager_get", "lazy_iter"])
    def test_hostile_next_page_link_is_refused(self, coda, mocked_responses, drain):
        mocked_responses.add(
            "GET", BASE_URL + "/docs", json=_page([{"a": 1}], "https://evil.example/steal")
        )
        mocked_responses.add("GET", "https://evil.example/steal", json=_page([]))

        with pytest.raises(err.UntrustedHost):
            if drain:
                coda.get("/docs")
            else:
                list(coda.iter_pages("/docs"))

        # the hostile host was never contacted, so the token never left
        assert [c.request.url for c in mocked_responses.calls] == [
            BASE_URL + "/docs"
        ]


class TestFailurePartwayThroughIsNotData:
    """
    The regression this module exists for.

    `handle_response` merged every page body into one dict without ever looking
    at a status code, so an error on page two was `update()`d in alongside the
    real items. `list_docs()` returned `{"items": [...], "statusCode": 429,
    "message": "rate limited"}` with no exception raised, and the caller saw a
    short result set that looked complete.
    """

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_error_on_a_later_page_raises(self, coda, mocked_responses, status):
        mocked_responses.add(
            "GET", BASE_URL + "/docs", json=_page([{"a": 1}], BASE_URL + "/docs?page=2")
        )
        mocked_responses.add(
            "GET",
            BASE_URL + "/docs?page=2",
            status=status,
            json={"statusCode": status, "message": "nope"},
        )

        with pytest.raises(err.HTTPError) as caught:
            coda.get("/docs")

        assert caught.value.status_code == status

    def test_the_error_is_not_smuggled_into_the_result(self, coda, mocked_responses):
        mocked_responses.add(
            "GET", BASE_URL + "/docs", json=_page([{"a": 1}], BASE_URL + "/docs?page=2")
        )
        mocked_responses.add(
            "GET",
            BASE_URL + "/docs?page=2",
            status=429,
            json={"statusCode": 429, "message": "rate limited"},
        )

        with pytest.raises(err.RateLimited):
            coda.get("/docs")
