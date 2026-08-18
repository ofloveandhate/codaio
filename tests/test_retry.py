"""
Retrying, and the classification that decides whether retrying is allowed at all.

Two separable things live here. *Whether* a request may be replayed is a fact
about the request that only this library is in a position to know, so it is not
configurable and is pinned by `TestIdempotencyClassification` below. *How hard*
to try is the caller's business, and is what `RetryPolicy` expresses.

Nothing here sleeps: every policy is built with the `fake_clock` fixture.
"""

import attr
import pytest
import requests

from codaio import err
from codaio.http import (
    Idempotency,
    RetryPolicy,
    _retry_allowed,
    retry_after_seconds,
    run_with_retry,
)

URL = "https://coda.io/apis/v1/docs"


@pytest.fixture
def policy(fake_clock):
    return RetryPolicy(jitter=False, sleep=fake_clock.sleep, clock=fake_clock)


def send():
    return requests.get(URL)


def run(idempotency, policy=None):
    return run_with_retry(
        send, idempotency=idempotency, policy=policy, method="GET", url=URL
    )


class TestIdempotencyClassification:
    """
    The classification is a specification, so it is pinned rather than inferred.

    The interesting rows are the ones where the answer depends on the arguments
    rather than the verb, which is the whole reason this cannot be left to
    callers: appending page content is not replayable while replacing it is, and
    updating a row by id is replayable while updating one by name is not.
    """

    @pytest.mark.parametrize(
        "status,idempotency,allowed",
        [
            # 429 means the request was rejected, not processed, so replaying is
            # safe no matter what the request would have done.
            (429, Idempotency.SAFE, True),
            (429, Idempotency.IDEMPOTENT, True),
            (429, Idempotency.UNSAFE, True),
            # a 5xx is a real failure of unknown effect
            (500, Idempotency.SAFE, True),
            (500, Idempotency.IDEMPOTENT, True),
            (500, Idempotency.UNSAFE, False),
            (503, Idempotency.UNSAFE, False),
        ],
    )
    def test_retry_allowed(self, status, idempotency, allowed):
        assert _retry_allowed(status, idempotency) is allowed


class TestRateLimiting:
    def test_429_is_retried_even_for_an_unsafe_write(
        self, policy, fake_clock, mocked_responses
    ):
        mocked_responses.add("GET", URL, status=429, json={})
        mocked_responses.add("GET", URL, status=200, json={"ok": True})

        assert run(Idempotency.UNSAFE, policy).status_code == 200
        assert fake_clock.sleeps == [0.5]

    def test_retry_after_is_honoured(self, policy, fake_clock, mocked_responses):
        mocked_responses.add(
            "GET", URL, status=429, json={}, headers={"Retry-After": "3"}
        )
        mocked_responses.add("GET", URL, status=200, json={"ok": True})

        run(Idempotency.SAFE, policy)

        assert fake_clock.sleeps == [3.0]

    def test_an_absurd_retry_after_raises_rather_than_sleeping(
        self, policy, fake_clock, mocked_responses
    ):
        mocked_responses.add(
            "GET", URL, status=429, json={}, headers={"Retry-After": "9999"}
        )

        with pytest.raises(err.RateLimited):
            run(Idempotency.SAFE, policy)

        assert fake_clock.sleeps == []

    def test_retry_after_accepts_an_http_date(self):
        response = requests.Response()
        response.headers["Retry-After"] = "Wed, 21 Oct 2015 07:28:00 GMT"
        # a date in the past clamps to zero rather than going negative
        assert retry_after_seconds(response) == 0.0


class TestServerErrors:
    def test_5xx_is_retried_for_a_read(self, policy, mocked_responses):
        mocked_responses.add("GET", URL, status=500, json={})
        mocked_responses.add("GET", URL, status=200, json={"ok": True})

        assert run(Idempotency.SAFE, policy).status_code == 200

    def test_5xx_is_not_retried_for_an_unsafe_write(
        self, policy, fake_clock, mocked_responses
    ):
        mocked_responses.add("GET", URL, status=500, json={})

        assert run(Idempotency.UNSAFE, policy).status_code == 500
        assert fake_clock.sleeps == []
        assert len(mocked_responses.calls) == 1


class TestTransportFailures:
    def test_read_timeout_on_an_unsafe_write_is_ambiguous(
        self, policy, mocked_responses
    ):
        """
        The request was sent and the reply was lost, so the edit may or may not
        have landed. Replaying could apply it twice, and `mutationStatus` cannot
        help because a requestId only arrives in a response that never came.
        """
        mocked_responses.add("GET", URL, body=requests.exceptions.ReadTimeout())

        with pytest.raises(err.AmbiguousWrite) as caught:
            run(Idempotency.UNSAFE, policy)

        assert "may or may not" in str(caught.value)

    def test_read_timeout_on_a_read_is_retried(self, policy, mocked_responses):
        mocked_responses.add("GET", URL, body=requests.exceptions.ReadTimeout())
        mocked_responses.add("GET", URL, status=200, json={"ok": True})

        assert run(Idempotency.SAFE, policy).status_code == 200

    def test_connect_timeout_is_retried_even_for_an_unsafe_write(
        self, policy, mocked_responses
    ):
        """A connection that was never established cannot have sent anything."""
        mocked_responses.add("GET", URL, body=requests.exceptions.ConnectTimeout())
        mocked_responses.add("GET", URL, status=200, json={"ok": True})

        assert run(Idempotency.UNSAFE, policy).status_code == 200


class TestPersistence:
    def test_backoff_is_exponential(self, fake_clock, mocked_responses):
        policy = RetryPolicy(
            attempts=4, jitter=False, sleep=fake_clock.sleep, clock=fake_clock
        )
        for _ in range(4):
            mocked_responses.add("GET", URL, status=429, json={})

        with pytest.raises(err.RetryExhausted):
            run(Idempotency.SAFE, policy)

        assert fake_clock.sleeps == [0.5, 1.0, 2.0]

    def test_exhaustion_reports_the_last_error(self, policy, mocked_responses):
        for _ in range(5):
            mocked_responses.add("GET", URL, status=429, json={})

        with pytest.raises(err.RetryExhausted) as caught:
            run(Idempotency.SAFE, policy)

        assert caught.value.attempts == 5
        assert isinstance(caught.value.last_error, err.RateLimited)
        assert caught.value.__cause__ is caught.value.last_error

    def test_no_policy_means_exactly_one_attempt(self, mocked_responses):
        mocked_responses.add("GET", URL, status=429, json={})

        assert run(Idempotency.SAFE, None).status_code == 429
        assert len(mocked_responses.calls) == 1


class TestClassificationReachesTheWire:
    """
    The registry is not decorative: what it declares is what happens.

    Both calls below are writes that fail the same way. The only thing that
    differs is how codaio classifies them, and that difference has to be visible
    in the number of requests actually made.
    """

    def test_an_unsafe_write_is_not_replayed(self, retrying_coda, mocked_responses):
        url = "https://coda.io/apis/v1/docs/d1/tables/t1/rows"
        for _ in range(3):
            mocked_responses.add("POST", url, status=500, json={"message": "boom"})

        with pytest.raises(err.ServerError):
            retrying_coda.upsert_row("d1", "t1", {"rows": []})

        assert len(mocked_responses.calls) == 1

    def test_an_idempotent_write_is_replayed(self, retrying_coda, mocked_responses):
        url = "https://coda.io/apis/v1/docs/d1/tables/t1/rows/r1"
        mocked_responses.add("PUT", url, status=500, json={"message": "boom"})
        mocked_responses.add("PUT", url, status=202, json={"requestId": "abc"})

        result = retrying_coda.update_row("d1", "t1", "r1", {"row": {"cells": []}})

        assert result["requestId"] == "abc"
        assert len(mocked_responses.calls) == 2

    def test_changing_the_registry_changes_the_behaviour(
        self, retrying_coda, mocked_responses, monkeypatch
    ):
        """If this ever passes with the classification ignored, the table is a lie."""
        from codaio import _endpoints

        url = "https://coda.io/apis/v1/docs/d1/tables/t1/rows/r1"
        mocked_responses.add("PUT", url, status=500, json={"message": "boom"})
        mocked_responses.add("PUT", url, status=202, json={"requestId": "abc"})

        monkeypatch.setitem(
            _endpoints.ENDPOINTS,
            "update_row",
            attr.evolve(
                _endpoints.ENDPOINTS["update_row"], idempotency=Idempotency.UNSAFE
            ),
        )

        with pytest.raises(err.ServerError):
            retrying_coda.update_row("d1", "t1", "r1", {"row": {"cells": []}})

        assert len(mocked_responses.calls) == 1


class TestErrorBodies:
    """
    The status code must survive a body that is not the JSON we expected. The
    previous implementation did `response.json()["message"]` unconditionally,
    so an HTML error page from an intermediary raised `JSONDecodeError` and the
    status code -- the one thing the caller needed -- was lost.
    """

    def test_a_non_json_body_still_yields_the_right_error(self, coda, mocked_responses):
        mocked_responses.add(
            "GET", URL, status=502, body="<html>bad gateway</html>",
            content_type="text/html",
        )

        with pytest.raises(err.ServerError) as caught:
            coda.get("/docs")

        assert caught.value.status_code == 502

    def test_json_without_a_message_key_still_yields_the_right_error(
        self, coda, mocked_responses
    ):
        mocked_responses.add("GET", URL, status=403, json={"unexpected": "shape"})

        with pytest.raises(err.Forbidden) as caught:
            coda.get("/docs")

        assert caught.value.status_code == 403

    def test_404_maps_to_not_found(self, coda, mocked_responses):
        mocked_responses.add("GET", URL, status=404, json={"message": "gone"})

        with pytest.raises(err.NotFound):
            coda.get("/docs")

    def test_410_maps_to_gone(self, coda, mocked_responses):
        mocked_responses.add("GET", URL, status=410, json={"message": "deleted"})

        with pytest.raises(err.Gone):
            coda.get("/docs")
