# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`codaio` is a Python wrapper for the [Coda.io](https://coda.io) v1 API. This checkout is
**silviana's fork** of `licht1stein/codaio` (`ofloveandhate/codaio`).

- It is **not published to PyPI** — the name there belongs to the upstream project and stops
  at 0.6.12. Releases are annotated git tags (`vX.Y.Z`); downstream pins
  `codaio @ git+https://github.com/ofloveandhate/codaio@v0.8.0`. Cutting a release means
  bumping `version` in `pyproject.toml`, merging, then tagging the merge commit.
- Never open PRs against upstream. Pass `--repo ofloveandhate/codaio --base master` to `gh`.
- The README's "Using CI to deploy to PyPi" section is upstream leftovers; the CI workflow
  has no deploy step.

## Commands

**Poetry and nox do not work here and fixing them is out of scope.** `poetry.lock` is from
2023 and resolved for `^3.9`, and `noxfile.py` was deleted. Use an environment you build
yourself:

```bash
python -m pip install -e .
python -m pip install pytest responses          # what CI installs
```

```bash
python -m pytest                                     # mocked suite + doctests
python -m pytest tests/test_credentials.py::test_name -v
python -m pytest -k keyring
python -m pytest --cov=codaio --cov-report=term-missing
```

Two suites do not run by default. See `docs/source/testing.rst` for the whole picture,
including how to set up a doc and a scoped token for the live one.

```bash
python -m pytest -m conformance     # compares codaio to the published OpenAPI spec
python -m pytest -m integration     # runs against a real Coda doc
```

Lint / format / docs (matching CI and the declared dev deps):

```bash
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics   # the CI gate
black codaio tests
cd docs && make html   # sphinx: docs/source -> docs/build, autodoc only
```

CI (`.github/workflows/test_and_deploy.yml`) runs `pip install -e .` + pytest on Python
3.10–3.13, with no `CODA_API_KEY` and no Secret Service — deliberately, so a leaked
credential in the runner can't mask a bug in the test isolation fixtures.

## Architecture

Layered, and the layering is enforced by `tests/test_module_boundaries.py`. Each layer may
only import from the ones below it, so a circular import cannot be introduced by accident.

```
err.py                     nothing
credentials.py             err                     token resolution, usable alone
http.py                    err                     retry, origin guards, status mapping
_endpoints.py              err, http               one description of every endpoint
values.py                  err, http               typed cell values
client.py                  the above               the raw API client
objects/                   the above               the object model
coda.py                    everything              a re-exporting compatibility shim
```

`objects/document.py` is the one object-model module allowed to import the client at
runtime, because `Document.from_credentials` builds one. The client never imports the
object model back — that direction is what keeps this acyclic, and is its own test.

### Rules that look arbitrary and are not

**The token is never in an attrs field.** `Coda.__attrs_post_init__` resolves it, stores it
on `self._token`, and sets `self._api_key = None`, because `attr.asdict()` reads fields
directly and ignores `repr=False` — so `attr.asdict(some_document)` would recurse into the
`Coda` and expose it.

**`assert_same_origin` guards every pagination hop.** `nextPageLink` comes from the response
*body*, so `requests`' cross-host stripping of the Authorization header never applies.

**`fetch_untrusted` takes no credential argument.** Attachment and export URLs live on a
content host. It is not an origin comparison but an absolute rule: there is no code path by
which a token could be attached. `tests/test_attachments.py` exists solely to keep it that
way.

**Unknown JSON fields are kept, never fatal.** `CodaObject.from_json` partitions rather than
splatting; anything unmodelled lands on `.raw` and is reachable via `.field()`. Coda's own
spec has enum values missing from its discriminator mapping (`email`, `link`, `reaction`),
so a strict client fails on an ordinary table.

**Identity is `(class, id)`, stated rather than derived.** Deriving `__hash__` from all
fields raised `TypeError` for any table with a `filter` or any row read as `rich`.

**Whether a request may be replayed is codaio's decision, not the caller's.** It depends on
the arguments, not just the verb — an upsert without `keyColumns`, a page update with an
`elementId`, anything addressed by name rather than id. `_endpoints.py` holds the
classification and the retry layer reads it; `tests/test_retry.py` fails if it stops being
load-bearing.

### Writes are accepted, not applied

Every mutating endpoint answers 202. Measured against a real doc, a row update is applied
after ~41s and a page creation after ~60 — not the "several seconds" the API documents. So:

- object-model writes return a `Mutation`; nothing waits by default;
- batching is the only sensible pattern — issue the writes, collect them in a
  `MutationGroup`, wait once, because writes are applied concurrently;
- a new page cannot be used as a `parentPageId` until its creation completes, at exactly
  that moment and not before.

### What the API cannot do

Tables and columns are read-only; only rows are writable. Copying a doc with `sourceDoc` is
the sanctioned route to a doc with tables in it. A conformance test fails if this changes.

## Tests

See `docs/source/testing.rst` for the full picture. In short: the default suite is fully
mocked and offline, and two further suites are opt-in and never run by accident.

The thing worth internalising is what the mocked suite **cannot** prove. It shows codaio
calls the URL codaio *meant* to, which is self-consistency, not correctness — `list_folders`
called `/docs/{docId}/folders`, an endpoint that has never existed, and every test passed.
`pytest -m conformance` is the only check against something codaio did not write itself.

- `tests/conftest.py::isolate_credentials` is **autouse**: it clears every credential env var
  and plants `None` in `sys.modules["keyring"]` so `import keyring` raises `ImportError`.
  Integration-marked tests are exempt, since they exist to use real credentials.
- Reuse `mock_json_response` / `mock_json_responses` (serving `tests/data/`) and the
  `main_document` / `main_table` fixtures rather than introduce a second mocking style.
- Nothing may actually sleep: anything that waits takes injectable `sleep`/`clock`, and the
  `fake_clock` fixture supplies them.
- Fixtures in `tests/data/` are realistic payloads and each carries one deliberately
  unmodelled key, so tolerance is exercised by ordinary tests. Do not trim them back — they
  were stripped once, and that is why a broken method survived for years.
- Docstring examples run as tests. `>>>` is executed and must stay true; anything needing a
  live client is a code block. `tests/test_docstrings.py` enforces the distinction and fails
  on an undocumented public member.

## Conventions

Line length 99 (`setup.cfg`; CI's non-blocking flake8 pass uses 127), Sphinx `:param:`
docstrings. `black` is a declared dev dependency and the README carries its badge, but the
fork's newer code (the `meta_to_dict` methods especially) is not black-clean — match the
surrounding file rather than reformatting on the way past, so diffs stay reviewable.
Substantive changes get a `CHANGELOG.md` entry under the version heading.
