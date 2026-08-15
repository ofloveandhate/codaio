"""
Resolution of the Coda API token.

The token is looked up through a short chain, first hit wins::

    explicit argument -> CODA_API_KEY_<PROFILE> -> CODA_API_KEY -> OS keyring

Storing the token in the OS keyring keeps it *encrypted at rest*: on Linux
gnome-keyring writes the collection to ``~/.local/share/keyrings`` encrypted
with a key derived from your login password. That means it is not readable
with ``cat`` and not usable if it gets swept into a backup. It does not mean
the token never touches disk, and it is no protection against a process
running as you while the session is unlocked.

Different Coda docsets usually want different tokens. Use a *profile* for
that -- the profile name is the keyring entry's username under the service
``codaio``, so what you type is what shows up in Seahorse::

    keyring set codaio research
    Coda(keyring_profile="research")

This module deliberately does not import :mod:`codaio.coda`, both to avoid a
circular import and so other tools can depend on the resolution logic alone.
"""

from __future__ import annotations

import hashlib
import os
import re
import warnings
from typing import List, NamedTuple, Optional

from codaio import err

DEFAULT_KEYRING_SERVICE = "codaio"
SERVICE = DEFAULT_KEYRING_SERVICE  # backwards-compatible alias
DEFAULT_KEYRING_PROFILE = "default"

ENV_API_KEY = "CODA_API_KEY"
ENV_KEYRING_PROFILE = "CODA_KEYRING_PROFILE"
ENV_ENDPOINT = "CODA_API_ENDPOINT"
ENV_DOTENV = "CODAIO_DOTENV"
ENV_KEYRING_SERVICE = "CODA_KEYRING_SERVICE"
ENV_ALLOW_INSECURE_KEYRING = "CODAIO_ALLOW_INSECURE_KEYRING"

DEFAULT_ENDPOINT = "https://coda.io/apis/v1"

TOKEN_URL = "https://coda.io/account"

# Backends that actually protect the token at rest. Checked as an explicit
# allowlist rather than by inspecting `priority`, which is a classproperty
# that raises on some backends and which ranks the genuinely-encrypted
# keyrings.alt EncryptedKeyring below several plaintext ones.
SECURE_BACKENDS = frozenset(
    {
        "keyring.backends.SecretService.Keyring",
        "keyring.backends.libsecret.Keyring",
        "keyring.backends.kwallet.DBusKeyring",
        "keyring.backends.kwallet.DBusKeyringKWallet4",
        "keyring.backends.macOS.Keyring",
        "keyring.backends.Windows.WinVaultKeyring",
        "keyrings.alt.file.EncryptedKeyring",
    }
)

# Known-bad backends, mapped to why. `keyring` falls back to these silently
# when no real secret store is available, which is the failure this module
# exists to prevent: they look like a keyring and are not encrypted.
INSECURE_BACKENDS = {
    "keyring.backends.fail.Keyring": "no keyring backend is available on this system",
    "keyring.backends.null.Keyring": "the null backend discards everything written to it",
    "keyrings.alt.file.PlaintextKeyring": (
        "stores tokens base64-encoded, NOT encrypted"
    ),
    "keyrings.alt.Windows.RegistryKeyring": "stores tokens in plaintext in the registry",
    "keyrings.alt.Google.DocsKeyring": "stores tokens in a Google Doc",
}

_insecure_backend_warned = False


class KeyringStatus(NamedTuple):
    """What the ``keyring`` package resolves to on this machine."""

    available: bool
    backend: Optional[str]
    secure: bool
    reason: str


class Attempt(NamedTuple):
    """One link of the chain that did not produce a token, and why."""

    mechanism: str
    detail: str


class Resolution(NamedTuple):
    """A token plus where it came from. ``source`` never contains the token."""

    api_key: str
    source: str


def default_keyring_profile(keyring_profile: Optional[str] = None) -> str:
    """Explicit argument, else ``CODA_KEYRING_PROFILE``, else ``"default"``."""
    return (
        keyring_profile
        or os.environ.get(ENV_KEYRING_PROFILE)
        or DEFAULT_KEYRING_PROFILE
    )


def keyring_profile_env_var(keyring_profile: str) -> str:
    """Per-profile override variable, e.g. ``research`` -> ``CODA_API_KEY_RESEARCH``."""
    slug = re.sub(r"[^0-9A-Za-z]+", "_", keyring_profile).strip("_").upper()
    return f"{ENV_API_KEY}_{slug}"


def fingerprint(api_key: str) -> str:
    """
    A short, stable, non-reversible identifier for a token.

    Safe to print or log, and used instead of showing the first or last few
    characters: Coda tokens are UUID-shaped, so leaking any real characters
    narrows a brute-force search, while this leaks none. Two machines holding
    the same token produce the same fingerprint, which is the point -- it
    answers "is this the same token I put on the server?".

    On SHA-256 here, since scanners flag it: this is not password hashing.
    Nothing is stored and nothing is verified against it, so there is no
    digest for an attacker to crack offline, which is the threat slow KDFs
    like bcrypt and argon2 exist to address. Those are slow because *human*
    passwords are low entropy and therefore guessable; a Coda API token is
    high-entropy random, so it is not reachable by brute force at any hash
    speed. Same construction as an SSH key fingerprint.

    Deliberately truncated. Collisions need roughly 2**24 tokens before they
    are likely, which is far past any plausible number of profiles, and a
    collision would only ever mean two tokens looked alike in a status line.

    Do not reuse this for anything that verifies a secret.
    """
    return "sha256:" + hashlib.sha256(api_key.encode()).hexdigest()[:12]


def _import_keyring():
    """Return the `keyring` module, or None if it isn't installed."""
    try:
        import keyring
    except ImportError:
        return None
    # A test fixture may plant None in sys.modules to force "not installed".
    return keyring


def _unwrap_backend(backend):
    """Chainer backends delegate; report the first member that could work."""
    members = getattr(backend, "backends", None)
    if not members:
        return backend
    for member in members:
        name = f"{type(member).__module__}.{type(member).__qualname__}"
        if name != "keyring.backends.fail.Keyring":
            return member
    # Every member is the fail backend. Report that rather than the chainer,
    # so the reason says "no keyring backend available" instead of the much
    # less useful "unrecognized backend".
    return members[0]


def keyring_status() -> KeyringStatus:
    """Describe the active keyring backend and whether it protects the token."""
    keyring = _import_keyring()
    if keyring is None:
        return KeyringStatus(
            available=False,
            backend=None,
            secure=False,
            reason="keyring package not installed (pip install keyring)",
        )

    try:
        backend = _unwrap_backend(keyring.get_keyring())
    except Exception as exc:  # pragma: no cover - backend discovery is fragile
        return KeyringStatus(
            available=False,
            backend=None,
            secure=False,
            reason=f"could not determine keyring backend: {exc}",
        )

    name = f"{type(backend).__module__}.{type(backend).__qualname__}"

    if name in SECURE_BACKENDS:
        return KeyringStatus(True, name, True, "encrypted at rest")
    if name in INSECURE_BACKENDS:
        return KeyringStatus(True, name, False, INSECURE_BACKENDS[name])
    return KeyringStatus(
        available=True,
        backend=name,
        secure=False,
        reason="unrecognized keyring backend; assuming it does not encrypt at rest",
    )


def _warn_insecure_backend_once(status: KeyringStatus) -> None:
    global _insecure_backend_warned
    if _insecure_backend_warned:
        return
    _insecure_backend_warned = True
    warnings.warn(
        f"Reading the Coda token from an insecure keyring backend "
        f"({status.backend}): {status.reason}. The token is not encrypted "
        f"at rest. Prefer the {ENV_API_KEY} environment variable on machines "
        f"without a real secret store.",
        UserWarning,
        stacklevel=3,
    )


def default_keyring_service(keyring_service: Optional[str] = None) -> str:
    """Explicit argument, else ``CODA_KEYRING_SERVICE``, else ``"codaio"``."""
    return (
        keyring_service
        or os.environ.get(ENV_KEYRING_SERVICE)
        or DEFAULT_KEYRING_SERVICE
    )


def _from_keyring(
    keyring_service: str, keyring_profile: str, attempts: List[Attempt]
) -> Optional[str]:
    keyring = _import_keyring()
    if keyring is None:
        attempts.append(
            Attempt(
                "OS keyring",
                "keyring package not installed (pip install keyring)",
            )
        )
        return None

    status = keyring_status()
    try:
        value = keyring.get_password(keyring_service, keyring_profile)
    except Exception as exc:
        # Never let a keyring problem break resolution -- a locked collection
        # or a dead dbus surfaces as anything from KeyringError to RuntimeError.
        attempts.append(Attempt("OS keyring", f"lookup failed: {exc}"))
        return None

    if not value:
        attempts.append(
            Attempt(
                "OS keyring",
                f"no entry for '{keyring_service}'/'{keyring_profile}' "
                f"(python -m keyring set {keyring_service} {keyring_profile})",
            )
        )
        return None

    if not status.secure:
        _warn_insecure_backend_once(status)
    return value


def _no_api_key_error(
    keyring_service: str, keyring_profile: str, attempts: List[Attempt]
) -> err.NoApiKey:
    width = max(len(a.mechanism) for a in attempts)
    tried = "\n".join(f"  {a.mechanism:<{width}}  - {a.detail}" for a in attempts)
    env_var = ENV_API_KEY if keyring_profile == DEFAULT_KEYRING_PROFILE else keyring_profile_env_var(keyring_profile)
    return err.NoApiKey(
        f"No Coda API token found for profile '{keyring_profile}'. Tried:\n"
        f"{tried}\n\n"
        f"Fix with either:\n"
        f"  python -m keyring set {keyring_service} {keyring_profile}\n"
        f"  export {env_var}=...\n"
        f"Get a token at {TOKEN_URL}"
    )


def get_api_key_with_source(
    api_key: Optional[str] = None,
    *,
    keyring_profile: Optional[str] = None,
    keyring_service: Optional[str] = None,
) -> Resolution:
    """
    Resolve the token and report where it came from.

    :param api_key: an explicit token, which always wins if given.
    :param keyring_profile: which stored token to use. This is the keyring entry's
        *username*; see :func:`default_keyring_profile`.
    :param keyring_service: the keyring entry's *service* name. Defaults to
        ``"codaio"``; override it to read an entry stored by something else.
    :raises codaio.err.NoApiKey: if no mechanism supplied a token.
    """
    keyring_profile = default_keyring_profile(keyring_profile)
    keyring_service = default_keyring_service(keyring_service)
    attempts: List[Attempt] = []

    if api_key:
        return Resolution(api_key, "explicit argument")
    attempts.append(Attempt("explicit argument", "not provided"))

    # The plain CODA_API_KEY already covers the default profile; a
    # CODA_API_KEY_DEFAULT alongside it would just be a confusing second
    # spelling of the same thing.
    if keyring_profile != DEFAULT_KEYRING_PROFILE:
        per_profile = keyring_profile_env_var(keyring_profile)
        value = os.environ.get(per_profile)
        if value:
            return Resolution(value, per_profile)
        attempts.append(Attempt(per_profile, "not set"))

    value = os.environ.get(ENV_API_KEY)
    if value:
        return Resolution(value, ENV_API_KEY)
    attempts.append(Attempt(ENV_API_KEY, "not set"))

    value = _from_keyring(keyring_service, keyring_profile, attempts)
    if value:
        return Resolution(value, f"keyring[{keyring_service}/{keyring_profile}]")

    raise _no_api_key_error(keyring_service, keyring_profile, attempts)


def get_api_key(
    api_key: Optional[str] = None,
    *,
    keyring_profile: Optional[str] = None,
    keyring_service: Optional[str] = None,
) -> str:
    """Resolve the token. See :func:`get_api_key_with_source`."""
    return get_api_key_with_source(
        api_key, keyring_profile=keyring_profile, keyring_service=keyring_service
    ).api_key


def store_api_key(
    api_key: str,
    *,
    keyring_profile: Optional[str] = None,
    keyring_service: Optional[str] = None,
    allow_insecure_backend: bool = False,
) -> str:
    """
    Write a token into the OS keyring.

    ``python -m keyring set <service> <profile>`` does the same thing from a
    shell; this exists so other tools can migrate stored credentials
    programmatically.

    Refuses to write to a backend that does not encrypt at rest, since doing
    so would silently defeat the point of using a keyring at all.

    :returns: a human-readable description of where the token was stored.
    :raises codaio.err.InsecureKeyringBackend: on a backend that is unsafe or
        unavailable, unless ``allow_insecure_backend`` is set.
    """
    if not api_key:
        raise ValueError("refusing to store an empty API key")

    keyring_profile = default_keyring_profile(keyring_profile)
    keyring_service = default_keyring_service(keyring_service)
    keyring = _import_keyring()
    if keyring is None:
        raise err.InsecureKeyringBackend(
            "keyring package not installed; install it with "
            '"pip install keyring"' 
        )

    status = keyring_status()
    override = allow_insecure_backend or env_bool(ENV_ALLOW_INSECURE_KEYRING, False)
    if not status.secure and not override:
        raise err.InsecureKeyringBackend(
            f"refusing to store the Coda token in {status.backend}: "
            f"{status.reason}. Storing it here would not be encrypted at rest. "
            f"Use the {keyring_profile_env_var(keyring_profile)} environment variable instead, "
            f"or pass allow_insecure_backend=True to override."
        )

    keyring.set_password(keyring_service, keyring_profile, api_key)
    return (
        f"keyring[{keyring_service}/{keyring_profile}] via {status.backend} "
        f"({fingerprint(api_key)})"
    )


def delete_api_key(
    *, keyring_profile: Optional[str] = None, keyring_service: Optional[str] = None
) -> bool:
    """Remove a stored token. Returns False if there was nothing to remove."""
    keyring_profile = default_keyring_profile(keyring_profile)
    keyring_service = default_keyring_service(keyring_service)
    keyring = _import_keyring()
    if keyring is None:
        return False
    try:
        if not keyring.get_password(keyring_service, keyring_profile):
            return False
        keyring.delete_password(keyring_service, keyring_profile)
    except Exception:
        return False
    return True


def resolve_endpoint(href: Optional[str] = None) -> str:
    """Explicit argument, else ``CODA_API_ENDPOINT``, else the public API."""
    return href or os.environ.get(ENV_ENDPOINT) or DEFAULT_ENDPOINT


_TRUTHY = frozenset({"1", "true", "t", "yes", "y", "on"})
_FALSEY = frozenset({"", "0", "false", "f", "no", "n", "off"})


def env_bool(name: str, default: bool = False) -> bool:
    """
    Read a boolean environment variable.

    Written out rather than using ``bool(os.environ.get(...))`` because that
    would make ``USE_HTTPX=0`` truthy, which is how the previous envparse
    behaviour would have silently changed on removal.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUTHY:
        return True
    if value in _FALSEY:
        return False
    return default


def load_dotenv(path: Optional[str] = None, *, override: bool = False) -> bool:
    """
    Load a ``.env`` file into the environment. Opt-in.

    Earlier versions of `codaio` did this implicitly at import time, reading
    a ``.env`` from the current working directory and mutating the whole
    process environment as a side effect of ``import codaio``. That is gone;
    call this explicitly, or set ``CODAIO_DOTENV=1``.

    :returns: whether a file was loaded.
    """
    try:
        from dotenv import load_dotenv as _load
    except ImportError:
        warnings.warn(
            "python-dotenv is not installed; .env file not loaded. "
            "Install it with \"pip install 'codaio[dotenv]'\".",
            UserWarning,
            stacklevel=2,
        )
        return False
    return bool(_load(dotenv_path=path, override=override))


def maybe_load_dotenv() -> bool:
    """Honour ``CODAIO_DOTENV`` at import time. A path is used as the filename."""
    raw = os.environ.get(ENV_DOTENV)
    if not raw:
        return False
    if env_bool(ENV_DOTENV, False):
        return load_dotenv()
    # Anything that isn't a recognised boolean is treated as a path.
    if raw.strip().lower() in _FALSEY:
        return False
    return load_dotenv(raw)
