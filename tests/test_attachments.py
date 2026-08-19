"""
Fetching attachment bytes -- and, above all, doing it without the API token.

**This file exists to stop one specific leak. Do not delete it as redundant with
the value tests.** Attachment URLs and export download links point at a content
host, not the API, and they arrive inside a response body where nothing in
`requests` will strip an Authorization header for you. Sending the bearer token
to whatever host a doc's contents happen to name would hand it to anyone who can
edit that doc.

The guarantee is stronger than comparing origins: `fetch_untrusted` takes no
credential argument at all, so there is no code path by which one could be
supplied. These tests assert the observable half of that -- no Authorization
header reaches the content host, including across a redirect.
"""

import pytest

from codaio import err
from codaio.http import fetch_untrusted
from codaio.values import parse_value

BLOB = "https://codahosted.io/blobs/attachment.png"
ELSEWHERE = "https://someone-elses-host.example/redirected.png"

IMAGE = {"@context": "http://schema.org", "@type": "ImageObject",
         "name": "photo.png", "url": BLOB, "status": "live"}


def image(**overrides):
    return parse_value({**IMAGE, **overrides})


class TestTheTokenNeverLeaves:
    def test_no_authorization_header_reaches_the_content_host(self, mocked_responses):
        mocked_responses.add("GET", BLOB, body=b"\x89PNG bytes", status=200)

        image().read()

        sent = mocked_responses.calls[0].request.headers
        assert "Authorization" not in sent

    def test_no_authorization_header_survives_a_redirect(self, mocked_responses):
        """
        A content host that redirects elsewhere must not collect the token either.

        `requests` strips the header across hosts on a redirect, but only if it
        was ever set -- and the point here is that it never is.
        """
        mocked_responses.add("GET", BLOB, status=302, headers={"Location": ELSEWHERE})
        mocked_responses.add("GET", ELSEWHERE, body=b"bytes", status=200)

        image().read()

        assert len(mocked_responses.calls) == 2
        for call in mocked_responses.calls:
            assert "Authorization" not in call.request.headers

    def test_the_helper_takes_no_credential_argument(self):
        """
        The structural half of the guarantee, asserted so a later refactor cannot
        quietly add a way to pass one in.
        """
        import inspect

        parameters = set(inspect.signature(fetch_untrusted).parameters)
        assert parameters == {"url", "timeout", "stream"}


class TestOnlyHttps:
    def test_a_plaintext_content_link_is_refused(self, mocked_responses):
        """Refused rather than silently downgraded."""
        with pytest.raises(err.UntrustedHost, match="https"):
            image(url="http://codahosted.io/attachment.png").read()

        assert not mocked_responses.calls

    def test_a_scheme_less_url_is_refused(self, mocked_responses):
        with pytest.raises(err.UntrustedHost):
            image(url="codahosted.io/attachment.png").read()


class TestReading:
    def test_read_returns_the_bytes(self, mocked_responses):
        mocked_responses.add("GET", BLOB, body=b"\x89PNG bytes", status=200)

        assert image().read() == b"\x89PNG bytes"

    def test_open_streams(self, mocked_responses):
        mocked_responses.add("GET", BLOB, body=b"streamed bytes", status=200)

        with image().open() as handle:
            assert handle.read() == b"streamed bytes"

    def test_an_http_error_from_the_content_host_is_raised(self, mocked_responses):
        mocked_responses.add("GET", BLOB, status=404, body="gone")

        with pytest.raises(err.NotFound):
            image().read()

    def test_an_expired_link_surfaces_as_forbidden(self, mocked_responses):
        """Download links expire quickly; the caller needs to know which failure this is."""
        mocked_responses.add("GET", BLOB, status=403, body="expired")

        with pytest.raises(err.Forbidden):
            image().read()


class TestUnavailableAttachments:
    def test_a_deleted_image_says_so_instead_of_fetching(self, mocked_responses):
        with pytest.raises(err.AttachmentUnavailable, match="deleted"):
            image(status="deleted").read()

        assert not mocked_responses.calls

    def test_an_image_with_no_url_says_so(self, mocked_responses):
        with pytest.raises(err.AttachmentUnavailable, match="no url"):
            image(url=None).read()


class TestCodaioDoesNotTouchTheFilesystem:
    def test_no_saving_helper_is_offered(self):
        """
        Deliberate: the `name` on an attachment is typed by whoever can edit the
        doc, so it is attacker-controlled input to any path it is used to build.
        Choosing a destination is the caller's decision, with the caller's rules.
        """
        value = image()

        assert not hasattr(value, "save")
        assert not hasattr(value, "download")
