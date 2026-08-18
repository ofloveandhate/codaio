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

**Poetry and nox do not work here and fixing them is out of scope.** (`poetry.lock` is from
2023 and resolved for `^3.9`.) Use the `codaio` micromamba env instead:

```bash
micromamba run -n codaio python -m pytest              # full suite (~187 tests, <1s)
micromamba run -n codaio python -m pytest tests/test_credentials.py::test_name -v
micromamba run -n codaio python -m pytest -k keyring
micromamba run -n codaio python -m pytest --cov=codaio --cov-report=term-missing
```

The env was created as:

```bash
micromamba create -n codaio -c conda-forge python=3.12 \
    requests attrs python-dateutil inflection decorator pytest responses keyring
```

Lint / format / docs (matching CI and the declared dev deps):

```bash
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics   # the CI gate
black codaio tests
make html          # sphinx: source/ -> build/, autodoc only
```

CI (`.github/workflows/test_and_deploy.yml`) runs `pip install -e .` + pytest on Python
3.10–3.13, with no `CODA_API_KEY` and no Secret Service — deliberately, so a leaked
credential in the runner can't mask a bug in the test isolation fixtures.

## Architecture

Three modules, and the layering between them matters.

### `codaio/credentials.py` — token resolution

Standalone by design: **it must not import `codaio.coda`** (circular import, and other tools
depend on the resolution logic alone). Resolution chain, first hit wins:

1. explicit argument → 2. `CODA_API_KEY_<PROFILE>` → 3. `CODA_API_KEY` → 4. OS keyring
   (`keyring_service`/`keyring_profile`, default `codaio`/`default`).

Env vars are checked *before* the keyring on purpose: reading a locked keyring can pop a
blocking desktop password prompt. Failure raises `err.NoApiKey` listing every `Attempt`.

This module only ever **reads** tokens. There is no function that stores one and none that
takes a token as an argument — that is the security posture, not an oversight. Storing is
`python -m keyring set codaio <profile>`'s job. `SECURE_BACKENDS` / `INSECURE_BACKENDS` are
explicit allowlists because `keyring`'s own `priority` ranks encrypted backends below
plaintext ones; `keyring_status()` reports what a machine resolves to.

### `codaio/coda.py` — raw client, then object model

**`Coda`** is a thin 1:1 wrapper over the REST endpoints; every method returns a plain dict.

- `get`/`post`/`put`/`delete` return a raw `requests.Response` (or a *list* of them for a
  paginated GET). The `@handle_response` decorator converts that to a dict, concatenates
  `items` across pages, and maps status codes onto `codaio.err` exceptions. A new HTTP
  method needs that decorator or callers get a `Response` object.
- Pagination follows `nextPageLink` **from the response body**, which sidesteps `requests`'
  cross-host `Authorization` stripping — hence `assert_same_origin()` guarding every hop.
  Don't remove it.
- The token is never held in an attrs field: `__attrs_post_init__` resolves it, stores it on
  `self._token`, and sets `self._api_key = None`, because `attr.asdict()` reads fields
  directly and ignores `repr=False`, so `attr.asdict(some_document)` would recurse into the
  `Coda` and expose it. Preserve this whenever touching `Coda`'s attrs.
- `USE_HTTPX=1` swaps `requests` for `httpx` at import time (eventlet compatibility).

**Object model** — `Document` → `Table` → `Row`/`Column` → `Cell`, all `attr.s` classes.
`CodaObject.from_json` camelCase→snake_case via `inflection` and drops `parent`/`format`,
so **attrs field names must be the snake_case of the API's JSON keys** or construction
raises `TypeError`. Every object holds a back-reference (`document`, `table`, `row`) and
routes all I/O through `document.coda`, so there is exactly one HTTP path.

Two conveniences that are easy to miss: `meta_to_dict()` exists on each level and chains via
`super()` + PEP 584 `|`, deliberately excluding the parent object unless asked; and
`Cell.value`'s setter *polls* `row.refresh()` until the write is visible, because Coda's API
is eventually consistent — that makes cell assignment blocking and slow.

`Document.from_credentials(doc_id, keyring_profile=...)` is the preferred entry point;
`from_environment` is the older name kept working.

## Tests

Fully mocked with `responses` — no network, no token, safe to run anywhere.

- `tests/conftest.py::isolate_credentials` is **autouse**: it clears every credential env var
  and plants `None` in `sys.modules["keyring"]` so `import keyring` raises `ImportError`.
  The default posture is "no keyring installed"; tests that want one opt in via the
  `fake_keyring` fixture (parametrise it indirectly with a backend dotted name).
- Reuse `mock_json_response` / `mock_json_responses` (they serve files from `tests/data/`)
  and the `main_document` / `main_table` fixtures rather than introduce a second mocking
  style.
- `tests/__init__.py` puts the repo root on `sys.path`, so `codaio` need not be installed.

## Conventions

Line length 99 (`setup.cfg`; CI's non-blocking flake8 pass uses 127), Sphinx `:param:`
docstrings. `black` is a declared dev dependency and the README carries its badge, but the
fork's newer code (the `meta_to_dict` methods especially) is not black-clean — match the
surrounding file rather than reformatting on the way past, so diffs stay reviewable.
Substantive changes get a `CHANGELOG.md` entry under the version heading.
