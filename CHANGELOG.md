# Changelog

## 0.9.0

Pages, robust writes, and an object model that survives the API changing.

### Breaking

- **Writes return a `Mutation` rather than a dict.** Every mutating endpoint
  answers 202: the edit is queued, not applied. `Mutation` carries the request
  id, with `.wait()` to block until the API reports it dealt with. Note
  `completed` means the API stopped working on the edit, not that it did what
  you asked -- the status endpoint has no failure field, only an optional
  `warning`.
- **`Cell.value = x` no longer waits**, and `Cell.set()` does not by default.
  Measured against a real doc, a row update reports complete after ~41 seconds
  and a page create after ~60, so waiting per write made editing a column of
  rows a matter of hours. Issue writes, collect the mutations, wait once with a
  `MutationGroup`. Assignment updates the object optimistically, so a read-back
  shows what you wrote rather than what Coda stored -- those differ, because
  values are coerced to the column's format.
- **Object identity is `(class, id)`.** `__hash__` was previously built from
  every field, so hashing a table with a `filter`, or any row read with
  `valueFormat=rich`, raised `TypeError`: the classes advertised a hashability
  they never had. `row_before == row_after` is now `True` after an edit -- it is
  the same row. Compare `.values` when you mean values.
- **`Section` is `Page`.** The API renamed it years ago; the URLs had already
  followed. `Section` still resolves, warns, and is the same object, so
  `isinstance` keeps working.
- **`list_folders` and `get_folder` no longer take a doc id.** They built
  `/docs/{docId}/folders`, which is not an endpoint the API has ever had, so
  both could only 404. Folders belong to a workspace. Passing a doc id raises,
  rather than being silently reinterpreted as a workspace id.
- **`Row.to_dict()` omits columns the row has no value for** instead of raising
  `KeyError`, and never invents a value. A row can legitimately lack a column --
  `visibleOnly` narrows columns as well as rows -- and `None` cannot be told
  apart from a cell that is genuinely empty. It raises if the row shares no
  column at all with its table, which usually means `useColumnNames`.
- **`Table.to_dict()` reads `simpleWithArrays`**, not the API's `simple`
  default, which joins array values into a comma-delimited string. That is
  ambiguous, not merely untidy: `["a", "b"]` and `["a, b"]` become the same
  string.
- **`MAX_GET_LIMIT` is no longer applied as a silent cap.** Asking for 300
  results quietly became a request for 200 with no way to tell.
- **`USE_HTTPX` is removed.** It swapped the HTTP library by import alias, which
  only worked while the surface was four verbs, and had no test coverage.
- `@handle_response` is gone; requests go through one chokepoint.

### Added

- **Pages**: full CRUD, content listing, and the two-step export that is the
  only route to Markdown or HTML. `PageTree` rebuilds the hierarchy from a
  single listing, since every page carries both its parent and its children.
- **Typed cell values** for the five JSON-LD types the API documents --
  `ImageValue`, `PersonValue`, `LinkValue`, `MoneyValue`, `RowValue` -- with the
  payload kept on `.raw` and an unrecognised `@type` becoming `UnknownValue`
  rather than an error. `MoneyValue.amount` is a `Decimal`.
- **Attachment fetching**: `ImageValue.read()` / `.open()`, which attach no
  credentials and take no credential argument. There is deliberately no
  `save()`: the filename is chosen by whoever can edit the doc.
- **Retry**, with codaio deciding what may be replayed and the caller deciding
  how hard to try. A 429 is always safe to replay; a read-timeout on an unsafe
  write raises `AmbiguousWrite` and never is.
- **Sharing**: `Permission`, the `Principal` family, `AclSettings`,
  `AclMetadata`, `Document.share()`. `access` is keyword-only with no default.
- Folders, formulas, controls, doc `PATCH`, `sourceDoc` copying, bulk row
  delete, `push_button`, `mutationStatus`, lazy `iter_rows`/`iter_pages`, and
  the `valueFormat`/`sortBy`/`visibleOnly` row parameters.
- `Column.format` and `parent` are no longer discarded, so a column's type and
  a page's place in the tree are readable without guessing.

### Fixed

- **A 429 or 500 partway through a paginated walk was returned as data.** The
  merge never checked status, so `list_docs()` could hand back a truncated
  result set with the error merged in and raise nothing.
- **`Document.list_sections()` failed against every real doc.** Unknown fields
  were fatal, and a real page carries `subtitle`, `contentType`, `isHidden` and
  `children`. Unknown fields are now kept and reachable via `.field()`.
- **`Cell.value`'s write loop never terminated for coerced values.** It re-read
  the row until the value matched what was sent, unbounded -- but Coda turns
  `"$12.34"` into `12.34`. It waits on `mutationStatus` now.
- Error bodies that are not JSON no longer raise `JSONDecodeError` and lose the
  status code.
- `Table.sorts` and `columns_storage` were shared between every table.
- `Row.to_dict()` was cubic in column count.
- `Row.__setitem__` mutated a throwaway `Cell`, so a read-back showed the old
  value.
- `list_views` sent `tableTypes` twice.

### Notes

- **A new page cannot be used as a parent until its creation has been applied.**
  The 202 hands back the id immediately, but referencing it sooner is refused
  with `400 Invalid parentPageId`. Measured: it becomes usable at exactly the
  moment `mutationStatus` reports the creation complete, around 46 seconds, with
  no earlier window. Building a page tree is therefore sequential per level,
  though pages within a level can be created together and waited on once.
- **Writes take roughly a minute to be applied**, despite the API documenting
  "several seconds" -- around 41 for a row update and 60 for a page creation.
  This is why nothing waits by default.
- **Tables and columns cannot be created through this API**, only read. Copying
  a doc with `sourceDoc` is the sanctioned way to get a doc with tables in it.
  A conformance test fails if that ever changes.
- Two opt-in suites, neither in CI: `pytest -m conformance` compares codaio to
  the published OpenAPI document, and `pytest -m integration` runs against a
  real doc with a doc-scoped token.

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
