"""
Fixtures for the tests that run against a real Coda doc.

Three things shape this suite, and all three are deliberate.

**The token must be doc-scoped, and the suite refuses anything broader.** A Doc
Maker token can create and delete docs across a whole workspace, which is far
more authority than a test suite should hold. `GET /whoami` reports whether a
token is restricted, so startup checks it and aborts if not. A powerful token is
a failure here, not a convenience.

**Nothing is cleaned up.** Teardown runs on the unhappy path, is the least
tested code in any suite, and a half-completed sweep leaves the doc worse than
no sweep at all. The test doc is disposable by hand instead: duplicate it in the
Coda UI when it gets cluttered and point `CODAIO_TEST_DOC` at the copy.

**So tests must tolerate leftovers.** Assert on what this run created, by the id
the create call returned. Never look something up by name, never assert a count,
never assert a listing is exhaustive. A test that counts pages passes once.

Note what a doc-scoped token cannot do, since it is more than write access:
`GET /docs` answers 403, so such a token cannot enumerate docs and the doc id
has to be configured rather than discovered.
"""

import os
import textwrap

import pytest

import codaio
from codaio import err

DOC_ENV = "CODAIO_TEST_DOC"
PROFILE_ENV = "CODA_KEYRING_PROFILE"
ALLOW_UNSCOPED_ENV = "CODAIO_TEST_ALLOW_UNSCOPED"

SETUP = f"""
    The integration suite needs a Coda doc of its own and a token restricted to
    it. Neither is created for you: a suite that could create docs would need
    exactly the broad authority this one refuses to hold.

    1. Make a doc in Coda to test against, and note its id -- the part after
       /d/_d in its URL.

    2. Make an API token at https://coda.io/account restricted to that doc,
       with read/write access.

    3. Put the token in your keyring under its own profile, so it is never the
       one your everyday work uses:

           python -m keyring set codaio codaio-test

    4. Point the suite at both:

           export {PROFILE_ENV}=codaio-test
           export {DOC_ENV}=<the doc id>

    Then:

           python -m pytest -m integration
"""


def _configured(name):
    value = os.environ.get(name)
    if not value:
        pytest.fail(
            f"{name} is not set, so there is nothing to test against.\n"
            + textwrap.dedent(SETUP),
            pytrace=False,
        )
    return value


@pytest.fixture(scope="session")
def live_coda():
    """
    A client for the test doc, refusing a token with more reach than it needs.

    Note this fixture does the opposite of the usual thing: it fails when the
    credentials are *too* powerful.
    """
    _configured(PROFILE_ENV)
    coda = codaio.Coda()

    try:
        whoami = coda.account()
    except err.Unauthorized as exc:
        pytest.fail(
            f"the stored token was rejected: {exc}\n" + textwrap.dedent(SETUP),
            pytrace=False,
        )

    if not whoami.get("scoped"):
        if os.environ.get(ALLOW_UNSCOPED_ENV):
            import warnings

            warnings.warn(
                f"{ALLOW_UNSCOPED_ENV} is set, so running with a token that is "
                f"not restricted to one doc. This token can reach everything "
                f"the account can.",
                UserWarning,
            )
        else:
            pytest.fail(
                f"the token for profile {os.environ[PROFILE_ENV]!r} is not "
                f"restricted to a single doc, and this suite writes to whatever "
                f"it is pointed at.\n\n"
                f"    Make a restricted token at https://coda.io/account and "
                f"store it with:\n\n"
                f"        python -m keyring set codaio "
                f"{os.environ[PROFILE_ENV]}\n\n"
                f"    Set {ALLOW_UNSCOPED_ENV}=1 to override, having decided "
                f"that is what you want.",
                pytrace=False,
            )

    return coda


@pytest.fixture(scope="session")
def live_doc(live_coda):
    """The test doc. Everything this suite writes goes inside it."""
    doc_id = _configured(DOC_ENV)
    try:
        return codaio.Document(doc_id, coda=live_coda)
    except err.HTTPError as exc:
        pytest.fail(
            f"could not open doc {doc_id!r}: {exc}\n"
            f"    Check {DOC_ENV}, and that the token covers this doc.",
            pytrace=False,
        )


@pytest.fixture(scope="session")
def capabilities(live_coda, live_doc):
    """
    What this token can actually do, established rather than assumed.

    Exactly what a restricted token may do is worth finding out empirically:
    scoping limits which docs a token touches, not what the account may do
    inside them, and the two are easy to conflate.
    """
    try:
        metadata = live_doc.acl_metadata()
        acl = {"can_share": bool(metadata.can_share),
               "can_copy": bool(metadata.can_copy)}
    except err.HTTPError:
        acl = {"can_share": False, "can_copy": False}

    found = {
        "acl_read": acl != {"can_share": False, "can_copy": False},
        **acl,
        "acl_writes_enabled": bool(os.environ.get("CODAIO_TEST_ACL_WRITES")),
    }
    print("\ntoken capabilities:", found)
    return found


@pytest.fixture(scope="session")
def run_stamp():
    r"""
    When this run happened, for stamping everything it leaves behind.

    Since nothing is cleaned up, a doc accumulates the debris of every run that
    has ever touched it. Without a stamp those are indistinguishable, and there
    is no way to tell what a given run did from what the run before it did.

    Local time, to the second, so it reads naturally in the Coda UI and sorts.

    >>> import re
    >>> re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", _stamp()) is not None
    True
    """
    return _stamp()


def _stamp():
    import datetime as dt

    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture(scope="session")
def scratch_page(live_doc, run_stamp):
    """
    A page to hang this run's pages under, so churn is separable from fixtures.

    Named for the run, which makes tidying up a doc a matter of deleting one
    dated page rather than picking through a pile of identically-named ones.

    Created once per session and left behind, like everything else here.
    """
    # Waits, unlike most writes here: everything else in the session hangs off
    # this page, so it has to exist before they run.
    mutation = live_doc.create_page(f"codaio run {run_stamp}")
    mutation.wait()
    page_id = mutation.id
    if not page_id:
        pytest.skip("the API did not report the new page's id")
    print(f"\nthis run's pages are under 'codaio run {run_stamp}'")
    return live_doc.get_page(page_id)


@pytest.fixture(scope="session")
def a_table(live_doc):
    """
    Some table in the doc, whichever comes first.

    Deliberately not looked up by name: the point of the doc is that it has
    tables in it, not that they are called anything in particular.
    """
    tables = live_doc.list_tables()
    if not tables:
        pytest.fail(
            "the test doc has no tables. Tables cannot be created through the "
            "API, so add one by hand -- ideally with a mix of column types, "
            "including a multiselect whose options contain a comma.",
            pytrace=False,
        )
    return tables[0]
