import sys

import pytest

from codaio import Coda, credentials, err

TOKEN = "test-token-must-never-be-printed"
PLAINTEXT_BACKEND = "keyrings.alt.file.PlaintextKeyring"


class TestProfiles:
    def test_default_profile_precedence(self, monkeypatch):
        assert credentials.default_profile() == "default"
        monkeypatch.setenv("CODA_PROFILE", "from-env")
        assert credentials.default_profile() == "from-env"
        assert credentials.default_profile("explicit") == "explicit"

    @pytest.mark.parametrize(
        "profile,expected",
        [
            ("research", "CODA_API_KEY_RESEARCH"),
            ("my-docs", "CODA_API_KEY_MY_DOCS"),
            ("a.b c", "CODA_API_KEY_A_B_C"),
        ],
    )
    def test_profile_env_var_slugging(self, profile, expected):
        assert credentials.profile_env_var(profile) == expected

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
        resolution = credentials.get_api_key_with_source(profile="research")
        assert resolution == ("specific", "CODA_API_KEY_RESEARCH")

    def test_plain_env_used_when_no_profile_specific_one(self, monkeypatch):
        monkeypatch.setenv("CODA_API_KEY", "plain")
        resolution = credentials.get_api_key_with_source(profile="research")
        assert resolution == ("plain", "CODA_API_KEY")

    def test_env_beats_keyring(self, monkeypatch, fake_keyring):
        fake_keyring.set_password("codaio", "default", "from-keyring")
        monkeypatch.setenv("CODA_API_KEY", "from-env")
        assert credentials.get_api_key_with_source().source == "CODA_API_KEY"

    def test_keyring_used_when_env_empty(self, fake_keyring):
        fake_keyring.set_password("codaio", "research", TOKEN)
        resolution = credentials.get_api_key_with_source(profile="research")
        assert resolution == (TOKEN, "keyring[research]")

    def test_profiles_are_independent(self, fake_keyring):
        fake_keyring.set_password("codaio", "research", "key-a")
        fake_keyring.set_password("codaio", "teaching", "key-b")
        assert credentials.get_api_key(profile="research") == "key-a"
        assert credentials.get_api_key(profile="teaching") == "key-b"

    def test_profile_from_environment(self, monkeypatch, fake_keyring):
        fake_keyring.set_password("codaio", "research", TOKEN)
        monkeypatch.setenv("CODA_PROFILE", "research")
        assert credentials.get_api_key() == TOKEN


class TestExhaustedChain:
    def test_raises_no_api_key(self):
        with pytest.raises(err.NoApiKey):
            credentials.get_api_key()

    def test_message_names_every_mechanism(self):
        with pytest.raises(err.NoApiKey) as excinfo:
            credentials.get_api_key(profile="research")
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
    def test_store_refuses_insecure_backend(self, fake_keyring):
        with pytest.raises(err.InsecureKeyringBackend) as excinfo:
            credentials.store_api_key(TOKEN, profile="research")
        assert PLAINTEXT_BACKEND in str(excinfo.value)
        assert fake_keyring.store == {}

    @pytest.mark.parametrize("fake_keyring", [PLAINTEXT_BACKEND], indirect=True)
    def test_store_override_argument(self, fake_keyring):
        credentials.store_api_key(
            TOKEN, profile="research", allow_insecure_backend=True
        )
        assert fake_keyring.store[("codaio", "research")] == TOKEN

    @pytest.mark.parametrize("fake_keyring", [PLAINTEXT_BACKEND], indirect=True)
    def test_store_override_env_var(self, monkeypatch, fake_keyring):
        monkeypatch.setenv("CODAIO_ALLOW_INSECURE_KEYRING", "1")
        credentials.store_api_key(TOKEN, profile="research")
        assert fake_keyring.store[("codaio", "research")] == TOKEN

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

    def test_store_without_keyring_package_raises(self):
        with pytest.raises(err.InsecureKeyringBackend):
            credentials.store_api_key(TOKEN)

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


class TestStoreAndDelete:
    def test_round_trip(self, fake_keyring):
        credentials.store_api_key(TOKEN, profile="research")
        assert credentials.get_api_key(profile="research") == TOKEN
        assert credentials.delete_api_key(profile="research") is True
        assert credentials.delete_api_key(profile="research") is False

    def test_refuses_empty_token(self, fake_keyring):
        with pytest.raises(ValueError):
            credentials.store_api_key("")

    def test_delete_without_keyring_package(self):
        assert credentials.delete_api_key() is False


class TestNoTokenLeaks:
    def _assert_clean(self, text):
        assert TOKEN not in text

    def test_fingerprint_hides_token(self):
        self._assert_clean(credentials.fingerprint(TOKEN))

    def test_fingerprint_is_stable_and_distinguishing(self):
        assert credentials.fingerprint(TOKEN) == credentials.fingerprint(TOKEN)
        assert credentials.fingerprint(TOKEN) != credentials.fingerprint("other")

    def test_store_description_hides_token(self, fake_keyring):
        self._assert_clean(credentials.store_api_key(TOKEN, profile="research"))

    def test_error_message_hides_token(self, monkeypatch):
        monkeypatch.setenv("CODA_API_KEY_OTHER", TOKEN)
        with pytest.raises(err.NoApiKey) as excinfo:
            credentials.get_api_key(profile="research")
        self._assert_clean(str(excinfo.value))

    def test_coda_repr_hides_token(self):
        self._assert_clean(repr(Coda(TOKEN, profile="research")))

    def test_resolution_source_hides_token(self, monkeypatch):
        monkeypatch.setenv("CODA_API_KEY", TOKEN)
        self._assert_clean(credentials.get_api_key_with_source().source)


class TestEnvBool:
    @pytest.mark.parametrize("raw", ["0", "false", "False", "no", "off", "", "  "])
    def test_falsey(self, monkeypatch, raw):
        monkeypatch.setenv("USE_HTTPX", raw)
        assert credentials.env_bool("USE_HTTPX", False) is False

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "y", "on"])
    def test_truthy(self, monkeypatch, raw):
        monkeypatch.setenv("USE_HTTPX", raw)
        assert credentials.env_bool("USE_HTTPX", False) is True

    def test_unset_uses_default(self):
        assert credentials.env_bool("USE_HTTPX", True) is True


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
        assert Coda.from_environment(profile="research").api_key == TOKEN

    def test_profile_recorded_on_instance(self, fake_keyring):
        fake_keyring.set_password("codaio", "research", TOKEN)
        coda = Coda(profile="research")
        assert coda.profile == "research"
        assert coda.source == "keyring[research]"

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
