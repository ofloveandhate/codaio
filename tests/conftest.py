import pytest
from pathlib import Path
import os
import re
import sys
import responses
import json
from codaio import Coda, Document
from codaio.http import RetryPolicy
from codaio import credentials

BASE_URL = "https://coda.io/apis/v1"

# Every environment variable the credential resolver consults.
CREDENTIAL_ENV_VARS = (
    "CODA_API_KEY",
    "CODA_PROFILE",
    "CODA_API_ENDPOINT",
    "CODAIO_DOTENV",
    "CODAIO_ALLOW_INSECURE_KEYRING",
)


@pytest.fixture(autouse=True)
def isolate_credentials(monkeypatch, request):
    """
    Keep the suite away from real credentials.

    Without this a developer with a populated keyring gets different results
    than CI. Planting None in sys.modules makes `import keyring` raise
    ImportError, so the default posture is "no keyring installed" and
    reaching a real Secret Service is impossible rather than just unlikely.
    Tests that want a keyring opt in via the `fake_keyring` fixture.

    The integration suite is the one exception: it exists to talk to a real doc
    with a real token, so isolating it from both would leave it testing nothing.
    It is opt-in and never runs by accident, which is what makes that safe.
    """
    if request.node.get_closest_marker("integration"):
        return

    for var in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    for var in [v for v in os.environ if re.fullmatch(r"CODA_API_KEY_\w+", v)]:
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setitem(sys.modules, "keyring", None)
    monkeypatch.setattr(credentials, "_insecure_backend_warned", False)


class FakeKeyring:
    """Minimal in-memory stand-in for the parts of `keyring` we use."""

    def __init__(self, backend_name):
        self.store = {}
        self.backend_name = backend_name
        self.raises = None

    def get_password(self, service, username):
        if self.raises:
            raise self.raises
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        del self.store[(service, username)]

    def get_keyring(self):
        module, _, qualname = self.backend_name.rpartition(".")
        return type(qualname, (), {"__module__": module})()


@pytest.fixture
def fake_keyring(request, monkeypatch):
    """
    Install an in-memory keyring. Parametrise with a backend dotted name:

        @pytest.mark.parametrize(
            "fake_keyring", ["keyrings.alt.file.PlaintextKeyring"], indirect=True
        )
    """
    backend = getattr(request, "param", "keyring.backends.SecretService.Keyring")
    fake = FakeKeyring(backend)
    monkeypatch.setitem(sys.modules, "keyring", fake)
    return fake


@pytest.fixture
def fake_clock():
    """
    A clock and a sleep that only advance it, recording what was asked for.

    Nothing in the suite may actually sleep, so anything that waits -- the retry
    policy, mutation polling, export polling -- takes its `sleep` and `clock` as
    arguments and gets these instead.
    """

    class Clock:
        def __init__(self):
            self.now = 0.0
            self.sleeps = []

        def __call__(self):
            return self.now

        def sleep(self, seconds):
            self.sleeps.append(seconds)
            self.now += seconds

    return Clock()


@pytest.fixture
def coda():
    """
    A client that does not retry, so one call means one request.

    Retrying is the default in real use, but a test that registers one response
    and gets five requests is a test about the retry policy, not about whatever
    it was trying to check. Use `retrying_coda` when the retrying *is* the point.
    """
    API_KEY = "ANY_KEY"
    return Coda(API_KEY, retry=None)


@pytest.fixture
def retrying_coda(fake_clock):
    """A client that retries on the default schedule, without ever waiting."""
    return Coda(
        "ANY_KEY",
        retry=RetryPolicy(jitter=False, sleep=fake_clock.sleep, clock=fake_clock),
    )


@pytest.fixture
def mocked_responses():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        yield rsps


@pytest.fixture
def mock_unauthorized_response(mock_json_response):
    def _mock_unauthorized_response(method):
        url = BASE_URL + "/"
        json_file = "unauthorized.json"
        mock_json_response(url, json_file, status=401, method=method)

    return _mock_unauthorized_response


@pytest.fixture
def mock_json_response(mocked_responses):
    """
    register mocked json responses.

    For a url, return the content of a json file found in the /test/data/ folder.
    """

    def _mock_json_response_from_file(
        url, filename, method="GET", status=200, **kwargs
    ):
        test_directory = Path(__file__).parent.resolve()
        relative_data_directory = "data"
        json_path = Path(test_directory / relative_data_directory / filename)
        with open(json_path) as json_file:
            json_content = json.load(json_file)

        method_map = {
            # `responses` dropped the public UNSET sentinel; None is the
            # modern way to say "match any method".
            "ANY": None,
            "GET": responses.GET,
            "POST": responses.POST,
            "PUT": responses.PUT,
            "DELETE": responses.DELETE,
            "PATCH": responses.PATCH,
            "HEAD": responses.HEAD,
        }

        mocked_responses.add(
            method_map.get(method), url, json=json_content, status=status, **kwargs
        )

    return _mock_json_response_from_file


@pytest.fixture
def mock_json_responses(mock_json_response):
    """
    register multiple json responses.

    Responses should be passed as a list of (url, filename, kwargs) tupples.
    """

    def _mock_json_responses(json_responses, base_url=None):
        for url, filename, kwargs in json_responses:
            mock_json_response(base_url + url, filename, **kwargs)

    return _mock_json_responses


@pytest.fixture
def main_document(coda, mock_json_response):
    mock_json_response(BASE_URL + "/docs/doc_id/", "get_doc.json")
    return Document("doc_id", coda=coda)


@pytest.fixture
def main_table(main_document, mock_json_response):
    mock_json_response(BASE_URL + "/docs/doc_id/tables/table_id", "get_table.json")
    return main_document.get_table("table_id")
