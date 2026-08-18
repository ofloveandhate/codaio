# Changelog

## 0.8.0

### Breaking

- **`import codaio` no longer reads a `.env` file.** Previously the import
  itself called `envparse`'s `read_envfile()`, which read a `.env` from the
  *current working directory* and injected it into `os.environ` for the whole
  process, with warnings suppressed. This was undocumented and surprising.
  To opt back in, set `CODAIO_DOTENV=1` (or to a path) and install the
  `dotenv` extra, or call `codaio.credentials.load_dotenv()` explicitly.

### Added

- `codaio.credentials`, which resolves the API token through a short chain:
  explicit argument, then `CODA_API_KEY_<PROFILE>`, then `CODA_API_KEY`, then
  the OS keyring. Usable on its own, without importing the rest of `codaio`.
- **Profiles**, for keeping a different token per docset. `keyring` addresses
  an entry by a (service, username) pair, spelled `keyring_service` and
  `keyring_profile` here so it is clear they are keyring addressing rather
  than concepts `Coda` itself has:

      python -m keyring set codaio research
      Coda(keyring_profile="research")

  `keyring_service` defaults to `codaio`; set it to read an entry owned by
  another tool. `CODA_KEYRING_PROFILE` and `CODA_KEYRING_SERVICE` set the
  defaults from the environment.

- `Coda()` with no arguments, and `Coda(keyring_profile=..., keyring_service=...)`.
- `Document.from_credentials(doc_id, keyring_profile=..., keyring_service=...)`.
- `Coda.source` records which mechanism supplied the token.
- `keyring` is now a **required** dependency, not an extra, so a plain
  `pip install codaio` gives you the recommended setup with nothing else to
  remember. On Linux this pulls in SecretStorage and cryptography.
- Optional extras `dotenv` and `httpx`, both of which only matter if you set
  `CODAIO_DOTENV` or `USE_HTTPX` respectively. `httpx` declares a dependency
  the code has always soft-imported but never listed.
- `err.NoApiKey` is now actually raised, with a
  message naming every mechanism that was tried.

**codaio only ever reads the token.** There is deliberately no
`store_api_key`, no `fingerprint`, and nothing else that takes a token as an
argument — writing one is `python -m keyring set`'s job. A library function
that accepts a raw secret is a leak surface, and there is no reason to have
one when the `keyring` CLI already does the job.

The tradeoff, stated plainly: codaio can no longer refuse to *write* to a
keyring backend that does not encrypt at rest, because it no longer writes.
`python -m keyring set` has no such guard. What remains is the read path,
which warns once when it reads from an insecure backend, and
`keyring_status()`, which reports the backend and whether it is safe. Check
it before storing anything on an unfamiliar machine.

### Fixed

- **`Coda` was never hashable.** `@attr.s(hash=True)` included the
  `authorization` dict in the generated `__hash__`, so `hash(Coda(...))`
  raised `TypeError: unhashable type: 'dict'` for every instance.
  `authorization` is now a computed property and the class sets `hash=False`.
  Nothing that previously worked can break, since nothing could hash a `Coda`.
- `CODA_API_ENDPOINT` was read at *import* time as an attrs default, so
  changing the environment afterwards had no effect. It is now read when a
  `Coda` is constructed.
- Writing a token to a keyring backend that does not encrypt at rest is now
  refused rather than done silently. Without a Secret Service available,
  `keyring` falls back to backends that store secrets base64-encoded; that
  fallback looks encrypted and is not.
- The test suite runs again. `responses` removed its public `UNSET` sentinel,
  which broke 20 of 21 tests at fixture setup.

- The API token is no longer sent to a host the API merely *names*.
  `requests` strips the Authorization header when a redirect crosses hosts,
  but a paginated response supplies its `nextPageLink` in the response body
  and that link was fetched directly, sidestepping the protection. A hostile
  or buggy link received the bearer token; verified exploitable before the
  fix. Cross-origin links now raise `err.UntrustedHost`.
- The token is no longer stored in an attrs field. `attr.asdict()` reads
  fields directly and ignores `repr=False`, so `attr.asdict(some_document)`
  recursed into the `Coda` it holds and exposed the token, even though
  `repr()` and `meta_to_dict()` were clean. `Coda.api_key` still reads and
  writes as before.

### Removed

- The `envparse` dependency, unmaintained since 2018.
