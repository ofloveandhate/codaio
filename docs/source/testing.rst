Running the tests
=================

There are three suites, and they answer different questions. Only the first runs
by default.

.. code-block:: shell

    python -m pytest                    # mocked; no network, no token
    python -m pytest -m conformance     # against the published API spec
    python -m pytest -m integration     # against a real Coda doc

Together they cover three separate claims: that codaio does what codaio intends,
that what it intends matches the API Coda documents, and that the API behaves as
documented. Each needs its own suite because none of them implies the next.


The default suite
-----------------

.. code-block:: shell

    python -m pytest

Fully mocked with `responses <https://github.com/getsentry/responses>`_. It makes
no network calls and needs no API token, so it is safe to run anywhere, and it
finishes in under a second.

Docstring examples run as part of it. An example written with ``>>>`` is executed
and fails the build when it stops being true; anything that needs a live client
is written as a code block instead, which is skipped. That distinction is
enforced: a guard fails if a docstring shows indented code that is neither.

**What it cannot prove.** It asserts that codaio calls the URL codaio *meant* to
call. That is self-consistency, not correctness. A method pointing at an endpoint
that does not exist passes every test in it — which is not hypothetical, it is
how ``list_folders`` spent years calling ``/docs/{docId}/folders``, a path the
API has never had. Only the conformance suite can catch that.

For coverage:

.. code-block:: shell

    python -m pytest --cov=codaio --cov-report=term-missing


Conformance
-----------

.. code-block:: shell

    python -m pytest -m conformance

Fetches the OpenAPI document Coda publishes and compares codaio to it: that every
path it calls exists, every query parameter it sends is accepted, every enum it
hardcodes matches, every success code it expects is one the API returns, and
every field the spec guarantees has somewhere to go.

Needs network but no token. It takes a second or two.

**It fails rather than skips when offline.** A conformance check that quietly
does nothing is worse than none, because the green run says otherwise.

**It reports what codaio is missing, never what the API has added.** Unknown
fields are kept and reachable — that is the design — so a schema growing a field
is not a failure. What would be a failure is codaio dropping something the API
guarantees is present.

One test in it is a scope check rather than a conformance one: it fails if the
API ever allows creating tables or columns, which it does not today. That
constraint shapes what this library can do, so it is worth being told rather
than discovering years later.


Integration
-----------

.. code-block:: shell

    python -m pytest -m integration

Runs against a real Coda doc. This is the only suite that can show the API
behaving differently from its documentation — and it does: writes are applied in
about a minute rather than the "several seconds" the docs claim, which is why
nothing in codaio waits for a write by default.

Setup
~~~~~

Neither the doc nor the token is created for you. A suite that could create docs
would need exactly the broad authority this one refuses to hold.

1. Make a doc in Coda to test against, and note its id — the part after ``/d/_d``
   in its URL.

2. Make an API token at https://coda.io/account **restricted to that doc**, with
   read and write access.

3. Store it in your keyring under its own profile, so it is never the token your
   everyday work uses:

   .. code-block:: shell

       python -m keyring set codaio codaio-test

4. Point the suite at both:

   .. code-block:: shell

       export CODA_KEYRING_PROFILE=codaio-test
       export CODAIO_TEST_DOC=<the doc id>

Running it with either unset prints these steps rather than a stack trace.

The token must be doc-scoped
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The suite checks ``GET /whoami`` at startup and **refuses to run with a token
that is not restricted**. A workspace-wide token can create and delete docs
across the whole workspace, which is far more authority than a test suite should
hold; here a powerful token is a failure rather than a convenience.

``CODAIO_TEST_ALLOW_UNSCOPED=1`` overrides it, loudly, for the case where the API
reports something unexpected and you would otherwise be stuck.

Note that a doc-scoped token cannot enumerate docs at all — ``GET /docs`` answers
403 — so the doc id has to be configured rather than discovered. The sharing
endpoints answer 401 for such a token, which reads as "your credentials are
wrong" rather than "your credentials are fine and insufficient"; the sharing
tests skip accordingly.

Nothing is cleaned up
~~~~~~~~~~~~~~~~~~~~~

Deliberately. Cleanup code runs on the unhappy path, is the least exercised part
of any suite, and a half-completed sweep leaves the doc worse than no sweep at
all. The test doc is disposable by hand instead: duplicate it in the Coda UI when
it gets cluttered and point ``CODAIO_TEST_DOC`` at the copy.

Every run stamps what it leaves behind, so runs can be told apart:

.. code-block:: text

    codaio run 2026-08-19 16:34:36
      codaio 2026-08-19 16:34:36: read_back
      codaio 2026-08-19 16:34:36: appended
      ...

Tidying up is deleting one dated page. The suite prints which one it is working
under as it starts.

The consequence for anyone adding a test here: **assert on what the run created,
by the id the create call returned.** Never look something up by name, never
assert a count, never assert a listing is exhaustive. Those pass once and fail on
every run afterwards.

Sharing is read-only
~~~~~~~~~~~~~~~~~~~~

Adding a permission — even ``anyone``/``readonly`` for a moment — makes the doc
readable by strangers, and that must never happen as a side effect of running
tests. The sharing writes are behind their own flag:

.. code-block:: shell

    export CODAIO_TEST_ACL_WRITES=1
    export CODAIO_TEST_SHARE_WITH=<an address you control>

They revoke in a ``finally``, which is the one exception to the no-cleanup rule:
a leftover page is clutter you ignore until the next copy of the doc, whereas a
leftover permission is an exposure.

Why it takes minutes
~~~~~~~~~~~~~~~~~~~~

Around five, and almost all of it is waiting for Coda rather than anything
codaio does. A write is accepted in well under a second and applied about a
minute later, and some of the suite is genuinely sequential: a page cannot be
given content until it exists, and a page id is not usable as a
``parentPageId`` until its creation completes — at exactly that moment, not
before.

So the writes are issued in batches and waited on once per phase. Writes are
applied concurrently, so a batch costs about as long as its slowest member rather
than the sum. Waiting after each write instead turns the suite from five minutes
into thirteen; it is worth keeping that shape if you add to it.

What a doc should contain
~~~~~~~~~~~~~~~~~~~~~~~~~

Tests skip rather than fail when the doc lacks something, and say what would
exercise them. A doc gets the most out of the suite if it has:

* a table with a spread of column types — text, number, currency, date, duration,
  person, lookup, checkbox, select, canvas and link all get reported;
* **a multiselect with several options chosen, at least one containing a comma**,
  which is what demonstrates the default value format losing information;
* a File or image column with something attached, which exercises fetching bytes
  from the content host;
* a page or two with subpages, so the tree has a shape;
* optionally an embed or sync page, which nothing else covers.


The linting gate
----------------

The same one CI runs:

.. code-block:: shell

    flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics

Syntax errors and undefined names only. It is a gate, not a style check.


Building these docs
-------------------

.. code-block:: shell

    cd docs && make html

Worth doing before a release: the build is warning-free, so a warning means a
docstring has malformed reStructuredText in it, which is easy to introduce and
invisible otherwise.


What CI runs
------------

The mocked suite, on Python 3.10 through 3.13, plus the linting gate. Nothing
else: no token, no network beyond installing the package, and neither opt-in
suite.

That is deliberate. Conformance depends on Coda's servers being reachable, and
an upstream change to their API should not turn an unrelated pull request red.
Run it yourself when you suspect drift, and before cutting a release.
