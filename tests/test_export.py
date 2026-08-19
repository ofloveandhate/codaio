"""
Exporting a page's content, which is the only way to get Markdown or HTML.

The synchronous content read speaks plain text only. Everything else goes
through a three-step dance: start an export, poll it, then fetch a link that
expires shortly after it appears -- and each of those steps has a trap in it.
"""

import pytest

from codaio import Page, err
from tests.conftest import BASE_URL

DOC = BASE_URL + "/docs/doc_id"
PAGE = "canvas-p1"
EXPORT = f"{DOC}/pages/{PAGE}/export"
BLOB = "https://codahosted.io/blobs/DOC_EXPORT_RENDERING/req-1"


@pytest.fixture
def page(main_document):
    return Page.from_json(
        {"id": PAGE, "type": "page", "name": "Launch", "contentType": "canvas"},
        document=main_document,
    )


@pytest.fixture
def started(page, mocked_responses):
    mocked_responses.add(
        "POST", EXPORT, status=202,
        json={"id": "req-1", "status": "inProgress", "href": f"{EXPORT}/req-1"},
    )
    return page.begin_export("markdown")


class TestBeginning:
    def test_the_two_steps_are_separate(self, started):
        """
        Both halves are exposed, not just a blocking helper: an export is a write
        against the tightest rate-limit bucket, so exporting many pages means
        starting them and collecting them on your own terms.
        """
        assert started.request_id == "req-1"
        assert not started.done

    def test_an_unknown_format_is_refused_before_the_request(self, page, mocked_responses):
        before = len(mocked_responses.calls)

        with pytest.raises(err.InvalidQuery, match="markdown"):
            page.begin_export("pdf")

        assert len(mocked_responses.calls) == before

    def test_html_is_available(self, page, mocked_responses):
        mocked_responses.add("POST", EXPORT, status=202, json={"id": "req-2"})
        page.begin_export("html")

        import json as _json
        assert _json.loads(
            mocked_responses.calls[-1].request.body) == {"outputFormat": "html"}


class TestDoneIsNotStatus:
    def test_a_terminal_looking_status_is_not_enough(self, started, mocked_responses):
        """
        The single most dangerous detail here. `status` is typed as a plain
        string with no documented values, and the spec's own example shows
        "complete" on a response that has no download link yet. Reading it would
        mean fetching a link that is not there.
        """
        mocked_responses.add(
            "GET", f"{EXPORT}/req-1", json={"id": "req-1", "status": "complete"})
        started.refresh()

        assert started.status == "complete"
        assert not started.done

    def test_a_download_link_means_done(self, started, mocked_responses):
        mocked_responses.add(
            "GET", f"{EXPORT}/req-1",
            json={"id": "req-1", "status": "whatever", "downloadLink": BLOB})
        started.refresh()

        assert started.done and not started.failed

    def test_an_error_also_means_done(self, started, mocked_responses):
        mocked_responses.add(
            "GET", f"{EXPORT}/req-1", json={"id": "req-1", "error": "it broke"})
        started.refresh()

        assert started.done and started.failed


class TestWaiting:
    def test_polls_until_a_link_appears(self, started, mocked_responses, fake_clock):
        mocked_responses.add("GET", f"{EXPORT}/req-1", json={"id": "req-1"})
        mocked_responses.add("GET", f"{EXPORT}/req-1", json={"id": "req-1"})
        mocked_responses.add(
            "GET", f"{EXPORT}/req-1", json={"id": "req-1", "downloadLink": BLOB})

        started.wait(sleep=fake_clock.sleep, clock=fake_clock)

        assert started.download_link == BLOB
        # three polls, sleeping only between them
        assert len(fake_clock.sleeps) == 2

    def test_polling_backs_off(self, started, mocked_responses, fake_clock):
        """
        An export is a doc-content write, and that bucket allows five requests
        per ten seconds -- polling hard makes the export slower, not faster.
        """
        for _ in range(3):
            mocked_responses.add("GET", f"{EXPORT}/req-1", json={"id": "req-1"})
        mocked_responses.add(
            "GET", f"{EXPORT}/req-1", json={"id": "req-1", "downloadLink": BLOB})

        started.wait(sleep=fake_clock.sleep, clock=fake_clock)

        assert fake_clock.sleeps == sorted(fake_clock.sleeps)
        assert fake_clock.sleeps[1] > fake_clock.sleeps[0]

    def test_it_gives_up_rather_than_looping_forever(self, started, mocked_responses,
                                                     fake_clock):
        mocked_responses.add("GET", f"{EXPORT}/req-1", json={"id": "req-1"})

        with pytest.raises(err.ExportTimeout) as caught:
            started.wait(timeout=10, sleep=fake_clock.sleep, clock=fake_clock)

        assert caught.value.request_id == "req-1"

    def test_a_failed_export_raises_with_the_reason(self, started, mocked_responses,
                                                    fake_clock):
        mocked_responses.add(
            "GET", f"{EXPORT}/req-1",
            json={"id": "req-1", "error": "the page is too large"})

        with pytest.raises(err.ExportFailed, match="too large"):
            started.wait(sleep=fake_clock.sleep, clock=fake_clock)


class TestFetching:
    @pytest.fixture
    def ready(self, started, mocked_responses):
        mocked_responses.add(
            "GET", f"{EXPORT}/req-1",
            json={"id": "req-1", "downloadLink": BLOB})
        return started.refresh()

    def test_read_returns_the_exported_bytes(self, ready, mocked_responses):
        mocked_responses.add("GET", BLOB, body=b"# Launch\n")

        assert ready.read() == b"# Launch\n"

    def test_text_decodes(self, ready, mocked_responses):
        mocked_responses.add("GET", BLOB, body="# Launch\n".encode())

        assert ready.text() == "# Launch\n"

    def test_the_token_never_reaches_the_content_host(self, ready, mocked_responses):
        """The link is on a blob host, not the API. Same rule as attachments."""
        mocked_responses.add("GET", BLOB, body=b"bytes")
        ready.read()

        assert "Authorization" not in mocked_responses.calls[-1].request.headers

    def test_an_expired_link_is_re_polled_once(self, ready, mocked_responses):
        """
        Download links expire quickly, and the fix is a fresh link rather than a
        failure -- so a rejection triggers exactly one re-poll before giving up.
        """
        fresh = BLOB + "?fresh"
        mocked_responses.add("GET", BLOB, status=403, body="expired")
        mocked_responses.add(
            "GET", f"{EXPORT}/req-1",
            json={"id": "req-1", "downloadLink": fresh})
        mocked_responses.add("GET", fresh, body=b"# Launch\n")

        assert ready.read() == b"# Launch\n"

    def test_it_does_not_re_poll_forever(self, ready, mocked_responses):
        mocked_responses.add("GET", BLOB, status=403, body="expired")
        mocked_responses.add(
            "GET", f"{EXPORT}/req-1", json={"id": "req-1", "downloadLink": BLOB})
        mocked_responses.add("GET", BLOB, status=403, body="expired again")

        with pytest.raises(err.Forbidden):
            ready.read()

    def test_reading_before_it_is_ready_says_so(self, started):
        with pytest.raises(err.ExportFailed, match="wait"):
            started.read()


class TestBlockingConvenience:
    def test_an_already_finished_export_costs_no_sleep(self, started,
                                                       mocked_responses, fake_clock):
        mocked_responses.add(
            "GET", f"{EXPORT}/req-1", json={"id": "req-1", "downloadLink": BLOB})

        started.wait(sleep=fake_clock.sleep, clock=fake_clock)

        assert fake_clock.sleeps == []

    def test_export_does_all_three_steps(self, page, mocked_responses):
        mocked_responses.add("POST", EXPORT, status=202, json={"id": "req-1"})
        mocked_responses.add(
            "GET", f"{EXPORT}/req-1", json={"id": "req-1", "downloadLink": BLOB})
        mocked_responses.add("GET", BLOB, body=b"# Launch\n")

        assert page.export_text() == "# Launch\n"
