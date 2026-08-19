"""
Sharing, which is read-only here unless you deliberately enable writes.

Adding a permission -- even `anyone`/`readonly` for a moment -- makes the doc
readable by strangers. That must never happen as a side effect of running a test
suite, so the writes are behind their own environment variable and revoke in a
`finally`.

That `finally` is the one exception to this suite's no-cleanup rule. A leftover
page is clutter you ignore until the next manual copy of the doc; a leftover
permission is an exposure. Cleanup earns its keep where skipping it is a
security problem, and nowhere else.
"""

import os

import pytest

from codaio import AccessType, Principal, err

pytestmark = pytest.mark.integration


class TestReadingWhoHasAccess:
    """
    All three skip when the token cannot see sharing, which a doc-scoped one
    cannot: the ACL endpoints answer 401 for it, not 403. That is worth knowing
    -- 401 usually means "your credentials are wrong" rather than "your
    credentials are fine and insufficient", so a caller could easily read it as
    a broken token.
    """

    def test_metadata_says_what_this_token_may_do(self, live_doc, capabilities):
        if not capabilities["acl_read"]:
            pytest.skip(
                "this token cannot read the doc's sharing metadata -- see the "
                "capability report; a doc-scoped token answers 401 here"
            )

        metadata = live_doc.acl_metadata()

        print(f"\ncan_share={metadata.can_share} can_copy={metadata.can_copy}")
        assert metadata.can_share is not None

    def test_permissions_parse(self, live_doc, capabilities):
        if not capabilities["acl_read"]:
            pytest.skip("this token cannot read the doc's permissions")

        permissions = live_doc.permissions()
        for permission in permissions:
            assert permission.access in AccessType.ALL
            assert permission.principal is not None
        print(f"\n{len(permissions)} permissions, principals: "
              f"{sorted({type(p.principal).__name__ for p in permissions})}")

    def test_settings_parse(self, live_doc, capabilities):
        if not capabilities["acl_read"]:
            pytest.skip("this token cannot read the doc's sharing settings")

        settings = live_doc.acl_settings()
        assert settings.allow_copying is not None


class TestGuards:
    """These cost nothing and never touch the network."""

    def test_access_must_be_stated(self, live_doc):
        with pytest.raises(TypeError):
            live_doc.share("nobody@example.com")

    def test_none_cannot_be_granted(self, live_doc):
        with pytest.raises(err.InvalidQuery):
            live_doc.share("nobody@example.com", access=AccessType.NONE)

    def test_a_domain_is_not_mistaken_for_an_address(self, live_doc):
        with pytest.raises(err.InvalidQuery):
            live_doc.share("example.com", access="readonly")


@pytest.mark.skipif(
    not os.environ.get("CODAIO_TEST_ACL_WRITES"),
    reason="set CODAIO_TEST_ACL_WRITES=1 to let the suite change who can see the doc",
)
class TestGrantingAndRevoking:
    def test_grant_then_revoke(self, live_doc, capabilities):
        if not capabilities["can_share"]:
            pytest.skip("this token cannot change sharing")

        target = os.environ.get("CODAIO_TEST_SHARE_WITH")
        if not target:
            pytest.skip(
                "set CODAIO_TEST_SHARE_WITH to an address you control; this test "
                "will not share with anyone by default"
            )

        live_doc.share(Principal.email(target), access="readonly",
                       suppress_email=True)
        try:
            granted = [
                p for p in live_doc.permissions()
                if getattr(p.principal, "email", None) == target
            ]
            assert granted, "the permission was not created"
        finally:
            for permission in live_doc.permissions():
                if getattr(permission.principal, "email", None) == target:
                    live_doc.unshare(permission)

        assert not [
            p for p in live_doc.permissions()
            if getattr(p.principal, "email", None) == target
        ]
