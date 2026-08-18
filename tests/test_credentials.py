import sys

import pytest

from codaio import Coda, credentials, err

TOKEN = "test-token-must-never-be-printed"
PLAINTEXT_BACKEND = "keyrings.alt.file.PlaintextKeyring"


class TestProfiles:
    def test_default_profile_precedence(self, monkeypatch):
        assert credentials.default_keyring_profile() == "default"
        monkeypatch.setenv("CODA_KEYRING_PROFILE", "from-env")
        assert credentials.default_keyring_profile() == "from-env"
        assert credentials.default_keyring_profile("explicit") == "explicit"

    @pytest.mark.parametrize(
        "keyring_profile,expected",
        [
            ("research", "CODA_API_KEY_RESEARCH"),
            ("my-docs", "CODA_API_KEY_MY_DOCS"),
            ("a.b c", "CODA_API_KEY_A_B_C"),
        ],
    )
    def test_profile_env_var_slugging(self, keyring_profile, expected):
        assert credentials.keyring_profile_env_var(keyring_profile) == expected

    def test_default_profile_ignores_suffixed_variable(self, monkeypatch):
        # The plain CODA_API_KEY covers the default profile; honouring a
        # CODA_API_KEY_DEFAULT too would be a confusing second spelling.
        monkeypatch.setenv("CODA_API_KEY_DEFAULT", "should-be-ignored")
        with pytest.raises(err.NoApiKey) as excinfo:
            credentials.get_api_key()
        assert "CODA_API_KEY_DEFAULT" not in str(excinfo.value)


class TestPrecedence:
    def test_explicit_argument_wins(self, monkeypatch):
        monkeypatch.setenv("CODA_API_KEY", "from-env")
        resolution = credentials.get_api_key_with_source("explicit")
        assert resolution == ("explicit", "explicit argument")

    def test_env_var(self, monkeypatch):
        monkeypatch.setenv("CODA_API_KEY", TOKEN)
        assert credentials.get_api_key_with_source() == (TOKEN, "CODA_API_KEY")

    def test_per_profile_env_beats_plain_env(self, monkeypatch):
        monkeypatch.setenv("CODA_API_KEY", "plain")
        monkeypatch.setenv("CODA_API_KEY_RESEARCH", "specific")
        resolution = credentials.get_api_key_with_source(keyring_profile="research")
        assert resolution == ("specific", "CODA_API_KEY_RESEARCH")

    def test_plain_env_used_when_no_profile_specific_one(self, monkeypatch):
        monkeypatch.setenv("CODA_API_KEY", "plain")
        resolution = credentials.get_api_key_with_source(keyring_profile="research")
        assert resolution == ("plain", "CODA_API_KEY")

    def test_env_beats_keyring(self, monkeypatch, fake_keyring):
        fake_keyring.set_password("codaio", "default", "from-keyring")
        monkeypatch.setenv("CODA_API_KEY", "from-env")
        assert credentials.get_api_key_with_source().source == "CODA_API_KEY"

    def test_keyring_used_when_env_empty(self, fake_keyring):
        fake_keyring.set_password("codaio", "research", TOKEN)
        resolution = credentials.get_api_key_with_source(keyring_profile="research")
        assert resolution == (TOKEN, "keyring[codaio/research]")

    def test_profiles_are_independent(self, fake_keyring):
        fake_keyring.set_password("codaio", "research", "key-a")
        fake_keyring.set_password("codaio", "teaching", "key-b")
        assert credentials.get_api_key(keyring_profile="research") == "key-a"
        assert credentials.get_api_key(keyring_profile="teaching") == "key-b"

    def test_profile_from_environment(self, monkeypatch, fake_keyring):
        fake_keyring.set_password("codaio", "research", TOKEN)
        monkeypatch.setenv("CODA_KEYRING_PROFILE", "research")
        assert credentials.get_api_key() == TOKEN


class TestService:
    def test_default_service_precedence(self, monkeypatch):
        assert credentials.default_keyring_service() == "codaio"
        monkeypatch.setenv("CODA_KEYRING_SERVICE", "from-env")
        assert credentials.default_keyring_service() == "from-env"
        assert credentials.default_keyring_service("explicit") == "explicit"

    def test_reads_entry_under_a_custom_service(self, fake_keyring):
        fake_keyring.set_password("other_tool", "research", TOKEN)
        resolution = credentials.get_api_key_with_source(
            keyring_profile="research", keyring_service="other_tool"
        )
        assert resolution == (TOKEN, "keyring[other_tool/research]")

    def test_services_are_independent(self, fake_keyring):
        fake_keyring.set_password("codaio", "research", "key-a")
        fake_keyring.set_password("other_tool", "research", "key-b")
        assert credentials.get_api_key(keyring_profile="research") == "key-a"
        assert (
            credentials.get_api_key(keyring_profile="research", keyring_service="other_tool")
            == "key-b"
        )

    def test_coda_accepts_service(self, fake_keyring):
        fake_keyring.set_password("other_tool", "research", TOKEN)
        coda = Coda(keyring_profile="research", keyring_service="other_tool")
        assert coda.api_key == TOKEN
        assert coda.keyring_service == "other_tool"

    def test_error_names_the_service_actually_used(self):
        with pytest.raises(err.NoApiKey) as excinfo:
            credentials.get_api_key(keyring_profile="research", keyring_service="other_tool")
        assert "keyring set other_tool research" in str(excinfo.value)


class TestExhaustedChain:
    def test_raises_no_api_key(self):
        with pytest.raises(err.NoApiKey):
            credentials.get_api_key()

    def test_message_names_every_mechanism(self):
        with pytest.raises(err.NoApiKey) as excinfo:
            credentials.get_api_key(keyring_profile="research")
        message = str(excinfo.value)
        for mechanism in (
            "explicit argument",
            "CODA_API_KEY_RESEARCH",
            "CODA_API_KEY",
            "OS keyring",
        ):
            assert mechanism in message
        assert "keyring set codaio research" in message

    def test_empty_keyring_entry_is_not_a_token(self, fake_keyring):
        fake_keyring.set_password("codaio", "default", "")
        with pytest.raises(err.NoApiKey):
            credentials.get_api_key()


class TestKeyringFailuresDoNotEscape:
    @pytest.mark.parametrize(
        "exc", [RuntimeError("dbus is dead"), Exception("locked")]
    )
    def test_lookup_errors_become_attempts(self, fake_keyring, exc):
        # A dead dbus or a locked collection must not propagate out of
        # resolution; it becomes one more exhausted link in the chain.
        fake_keyring.raises = exc
        with pytest.raises(err.NoApiKey) as excinfo:
            credentials.get_api_key()
        assert "lookup failed" in str(excinfo.value)

    def test_lookup_error_does_not_shadow_a_working_env_var(
        self, monkeypatch, fake_keyring
    ):
        fake_keyring.raises = RuntimeError("dbus is dead")
        monkeypatch.setenv("CODA_API_KEY", TOKEN)
        assert credentials.get_api_key() == TOKEN

    def test_missing_keyring_package_is_not_an_error(self):
        assert sys.modules["keyring"] is None
        with pytest.raises(err.NoApiKey) as excinfo:
            credentials.get_api_key()
        assert "keyring package not installed" in str(excinfo.value)


class TestBackendSafety:
    def test_secret_service_is_secure(self, fake_keyring):
        status = credentials.keyring_status()
        assert status.available and status.secure

    @pytest.mark.parametrize("fake_keyring", [PLAINTEXT_BACKEND], indirect=True)
    def test_plaintext_backend_is_insecure(self, fake_keyring):
        status = credentials.keyring_status()
        assert status.available
        assert not status.secure
        assert "NOT encrypted" in status.reason

    @pytest.mark.parametrize("fake_keyring", [PLAINTEXT_BACKEND], indirect=True)
    def test_reading_insecure_backend_warns_exactly_once(self, fake_keyring):
        fake_keyring.set_password("codaio", "default", TOKEN)
        with pytest.warns(UserWarning, match="not encrypted"):
            assert credentials.get_api_key() == TOKEN
        # A second read must not warn again.
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert credentials.get_api_key() == TOKEN

    def test_unknown_backend_is_treated_as_insecure(self, monkeypatch):
        from tests.conftest import FakeKeyring

        fake = FakeKeyring("some.vendor.MysteryKeyring")
        monkeypatch.setitem(sys.modules, "keyring", fake)
        assert not credentials.keyring_status().secure

    def test_chainer_unwraps_to_first_working_backend(self, monkeypatch):
        class Fail:
            pass

        Fail.__module__ = "keyring.backends.fail"
        Fail.__qualname__ = "Keyring"

        class Real:
            pass

        Real.__module__ = "keyring.backends.SecretService"
        Real.__qualname__ = "Keyring"

        class Chainer:
            backends = [Fail(), Real()]

        class Mod:
            @staticmethod
            def get_keyring():
                return Chainer()

        monkeypatch.setitem(sys.modules, "keyring", Mod)
        assert credentials.keyring_status().secure

    def test_this_machines_real_backend_is_classified(self, monkeypatch):
        """
        The allowlist matches backends by exact dotted name, so a rename
        upstream -- or a wrong guess about a platform we don't develop on --
        would silently downgrade a real keyring to "unrecognized" and refuse
        to store. Run against whatever backend this machine actually has.
        """
        monkeypatch.delitem(sys.modules, "keyring", raising=False)
        pytest.importorskip("keyring")

        status = credentials.keyring_status()
        assert status.backend is not None
        assert status.backend in (
            credentials.SECURE_BACKENDS | set(credentials.INSECURE_BACKENDS)
        ), (
            f"{status.backend} is not in either list, so codaio would refuse "
            f"to store here. Add it to SECURE_BACKENDS or INSECURE_BACKENDS."
        )


class TestNoTokenLeaks:
    def _assert_clean(self, text):
        assert TOKEN not in text

    def test_error_message_hides_token(self, monkeypatch):
        monkeypatch.setenv("CODA_API_KEY_OTHER", TOKEN)
        with pytest.raises(err.NoApiKey) as excinfo:
            credentials.get_api_key(keyring_profile="research")
        self._assert_clean(str(excinfo.value))

    def test_coda_repr_hides_token(self):
        self._assert_clean(repr(Coda(TOKEN, keyring_profile="research")))

    def test_resolution_source_hides_token(self, monkeypatch):
        monkeypatch.setenv("CODA_API_KEY", TOKEN)
        self._assert_clean(credentials.get_api_key_with_source().source)


class TestTokenNotInAttrsFields:
    """
    `attr.asdict()` reads fields directly and ignores repr=False, so a token
    stored in an attrs field is exposed by asdict -- including via a Document
    recursing into the Coda it holds. The token therefore lives outside the
    fields entirely.
    """

    def test_asdict_of_coda_has_no_token(self):
        import attr

        assert TOKEN not in str(attr.asdict(Coda(TOKEN)))

    def test_asdict_of_document_has_no_token(self, main_document):
        # asdict recurses into the Coda the Document holds, so check against
        # whatever token that Coda actually resolved rather than planting one.
        import attr

        token = main_document.coda.api_key
        assert token, "fixture should have resolved some token"
        assert token not in str(attr.asdict(main_document))

    def test_api_key_is_still_readable(self):
        assert Coda(TOKEN).api_key == TOKEN

    def test_api_key_is_still_writable(self):
        coda = Coda("original")
        coda.api_key = TOKEN
        assert coda.api_key == TOKEN
        assert coda.authorization == {"Authorization": f"Bearer {TOKEN}"}

    def test_positional_construction_unchanged(self):
        assert Coda(TOKEN).api_key == TOKEN


class TestPaginationOrigin:
    """
    `requests` strips Authorization on cross-host *redirects*, but a
    `nextPageLink` is read from the response body and fetched directly, so it
    bypasses that protection and would hand the token to any host the API
    names.
    """

    @pytest.mark.parametrize(
        "next_page",
        [
            "https://attacker.example/steal",
            "http://coda.io/apis/v1/docs",  # scheme downgrade
            "https://coda.io.attacker.example/apis/v1/docs",  # suffix trick
            "https://coda.io:8443/apis/v1/docs",  # different port
        ],
    )
    def test_cross_origin_next_page_is_refused(
        self, coda, mocked_responses, next_page
    ):
        mocked_responses.add(
            "GET",
            "https://coda.io/apis/v1/docs",
            json={"items": [{"a": 1}], "nextPageLink": next_page},
        )
        with pytest.raises(err.UntrustedHost):
            coda.list_docs()

    def test_same_origin_next_page_is_followed(self, coda, mocked_responses):
        base = "https://coda.io/apis/v1/docs"
        mocked_responses.add(
            "GET", base, json={"items": [{"a": 1}], "nextPageLink": base + "?page=2"}
        )
        mocked_responses.add("GET", base + "?page=2", json={"items": [{"b": 2}]})
        assert coda.list_docs()["items"] == [{"a": 1}, {"b": 2}]

    def test_default_port_is_equivalent_to_implicit(self):
        from codaio.http import assert_same_origin

        assert_same_origin(
            "https://coda.io:443/apis/v1/docs", "https://coda.io/apis/v1"
        )


class TestEnvBool:
    @pytest.mark.parametrize("raw", ["0", "false", "False", "no", "off", "", "  "])
    def test_falsey(self, monkeypatch, raw):
        monkeypatch.setenv("CODAIO_DOTENV", raw)
        assert credentials.env_bool("CODAIO_DOTENV", False) is False

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "y", "on"])
    def test_truthy(self, monkeypatch, raw):
        monkeypatch.setenv("CODAIO_DOTENV", raw)
        assert credentials.env_bool("CODAIO_DOTENV", False) is True

    def test_unset_uses_default(self):
        assert credentials.env_bool("CODAIO_DOTENV", True) is True


class TestEndpoint:
    def test_default(self):
        assert credentials.resolve_endpoint() == "https://coda.io/apis/v1"

    def test_env_var(self, monkeypatch):
        monkeypatch.setenv("CODA_API_ENDPOINT", "https://example.test/v1")
        assert credentials.resolve_endpoint() == "https://example.test/v1"

    def test_explicit_wins(self, monkeypatch):
        monkeypatch.setenv("CODA_API_ENDPOINT", "https://example.test/v1")
        assert credentials.resolve_endpoint("https://other.test") == "https://other.test"

    def test_endpoint_is_read_at_instantiation_not_import(self, monkeypatch):
        # Previously this was an attrs default evaluated at import time, so
        # changing the environment afterwards had no effect.
        monkeypatch.setenv("CODA_API_ENDPOINT", "https://late.test/v1")
        assert Coda("k").href == "https://late.test/v1"


class TestCodaIntegration:
    def test_positional_api_key_still_works(self):
        assert Coda("ANY_KEY").api_key == "ANY_KEY"

    def test_coda_is_hashable(self):
        # Regression: `authorization` was a stored Dict field included in the
        # attrs-generated __hash__, so this raised TypeError for every instance.
        assert hash(Coda("k")) is not None

    def test_authorization_header_tracks_api_key(self):
        coda = Coda("k")
        assert coda.authorization == {"Authorization": "Bearer k"}

    def test_from_environment(self, monkeypatch):
        monkeypatch.setenv("CODA_API_KEY", TOKEN)
        assert Coda.from_environment().api_key == TOKEN

    def test_from_environment_with_profile(self, fake_keyring):
        fake_keyring.set_password("codaio", "research", TOKEN)
        assert Coda.from_environment(keyring_profile="research").api_key == TOKEN

    def test_profile_recorded_on_instance(self, fake_keyring):
        fake_keyring.set_password("codaio", "research", TOKEN)
        coda = Coda(keyring_profile="research")
        assert coda.keyring_profile == "research"
        assert coda.source == "keyring[codaio/research]"

    def test_no_credentials_raises(self):
        with pytest.raises(err.NoApiKey):
            Coda()


class TestDotenvNoLongerImplicit:
    def test_import_does_not_read_dotenv(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("CODA_API_KEY=should-not-load\n")
        monkeypatch.chdir(tmp_path)
        credentials.maybe_load_dotenv()
        import os

        assert "CODA_API_KEY" not in os.environ

    def test_falsey_dotenv_var_does_not_load(self, monkeypatch):
        monkeypatch.setenv("CODAIO_DOTENV", "0")
        assert credentials.maybe_load_dotenv() is False

    def test_unset_dotenv_var_does_not_load(self):
        assert credentials.maybe_load_dotenv() is False

    def test_truthy_dotenv_var_opts_back_in(self, monkeypatch, tmp_path):
        loaded = {}
        monkeypatch.setattr(
            credentials, "load_dotenv", lambda *a, **k: loaded.setdefault("args", (a, k))
        )
        monkeypatch.setenv("CODAIO_DOTENV", "1")
        credentials.maybe_load_dotenv()

        assert "args" in loaded, "CODAIO_DOTENV=1 should trigger a load"

    def test_a_path_in_the_dotenv_var_is_used_as_the_filename(
        self, monkeypatch, tmp_path
    ):
        seen = {}
        monkeypatch.setattr(
            credentials, "load_dotenv", lambda path=None, **k: seen.setdefault("path", path)
        )
        env_file = tmp_path / "custom.env"
        env_file.write_text("X=1\n")
        monkeypatch.setenv("CODAIO_DOTENV", str(env_file))
        credentials.maybe_load_dotenv()

        assert seen["path"] == str(env_file)

    def test_load_dotenv_warns_when_python_dotenv_is_absent(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "dotenv", None)
        with pytest.warns(UserWarning, match="python-dotenv is not installed"):
            assert credentials.load_dotenv() is False


class TestKeyringStatusEdges:
    def test_backend_discovery_failure_is_reported_not_raised(self, monkeypatch):
        class Exploding:
            @staticmethod
            def get_keyring():
                raise RuntimeError("dbus exploded")

        monkeypatch.setitem(sys.modules, "keyring", Exploding)
        status = credentials.keyring_status()

        assert status.available is False
        assert "dbus exploded" in status.reason

    def test_chainer_with_only_failing_backends_is_insecure(self, monkeypatch):
        class Fail:
            pass

        Fail.__module__ = "keyring.backends.fail"
        Fail.__qualname__ = "Keyring"

        class Chainer:
            backends = [Fail()]

        class Mod:
            @staticmethod
            def get_keyring():
                return Chainer()

        monkeypatch.setitem(sys.modules, "keyring", Mod)
        status = credentials.keyring_status()

        assert not status.secure
        assert "no keyring backend" in status.reason
