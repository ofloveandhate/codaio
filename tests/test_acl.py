"""
Sharing: who can see a doc, and the guards around changing that.

This is the one part of the library where a mistake gives somebody access they
should not have, so the tests here are mostly about the things codaio refuses to
do rather than the things it does.
"""

import json as _json

import pytest

from codaio import (
    AccessType,
    AclSettings,
    AnyonePrincipal,
    DomainPrincipal,
    EmailPrincipal,
    Principal,
    UnknownPrincipal,
    err,
)
from codaio.objects.acl import as_principal
from tests.conftest import BASE_URL

ACL = BASE_URL + "/docs/doc_id/acl"


class TestAccessIsAlwaysExplicit:
    def test_share_has_no_default_access(self, main_document):
        """
        Keyword-only and no default, deliberately: a defaulting mistake here
        hands data to the wrong person.
        """
        with pytest.raises(TypeError, match="access"):
            main_document.share("alice@example.com")

    def test_none_cannot_be_granted(self, main_document, mocked_responses):
        """
        `none` is a level permissions are *read* as. Taking access away is a
        delete, not a grant of nothing -- and the API's add body excludes it.
        """
        with pytest.raises(err.InvalidQuery, match="delete the permission"):
            main_document.share("alice@example.com", access=AccessType.NONE)

        assert not [c for c in mocked_responses.calls if c.request.method == "POST"]

    def test_an_unknown_access_level_is_refused(self, main_document):
        with pytest.raises(err.InvalidQuery, match="readonly"):
            main_document.share("alice@example.com", access="admin")

    def test_grantable_excludes_none(self):
        assert AccessType.NONE not in AccessType.GRANTABLE
        assert AccessType.NONE in AccessType.ALL


class TestPrincipals:
    def test_a_bare_string_is_an_email(self):
        assert as_principal("alice@example.com") == Principal.email("alice@example.com")

    def test_a_string_that_is_not_an_email_is_refused(self):
        """
        Otherwise a typo -- or a domain someone meant to share with -- would be
        sent as an email address and quietly do the wrong thing.
        """
        with pytest.raises(err.InvalidQuery, match="Principal.domain"):
            as_principal("example.com")

    def test_the_other_kinds_have_to_be_spelled_out(self):
        assert Principal.domain("example.com").to_json() == {
            "type": "domain", "domain": "example.com"}
        assert Principal.anyone().to_json() == {"type": "anyone"}
        assert Principal.workspace("ws-1").to_json() == {
            "type": "workspace", "workspaceId": "ws-1"}

    @pytest.mark.parametrize(
        "payload,expected",
        [
            ({"type": "email", "email": "a@b.com"}, EmailPrincipal),
            ({"type": "domain", "domain": "b.com"}, DomainPrincipal),
            ({"type": "anyone"}, AnyonePrincipal),
            ({"type": "somethingNew"}, UnknownPrincipal),
        ],
        ids=["email", "domain", "anyone", "unknown"],
    )
    def test_reading_dispatches_on_type(self, payload, expected):
        assert isinstance(Principal.from_json(payload), expected)

    def test_an_unmodelled_principal_keeps_its_payload(self):
        principal = Principal.from_json({"type": "somethingNew", "detail": 1})

        assert principal.raw == {"type": "somethingNew", "detail": 1}


class TestSharing:
    def test_it_sends_the_principal_and_access(self, main_document, mocked_responses):
        mocked_responses.add("POST", ACL + "/permissions", json={})
        main_document.share("alice@example.com", access="readonly")

        assert _json.loads(mocked_responses.calls[-1].request.body) == {
            "access": "readonly",
            "principal": {"type": "email", "email": "alice@example.com"},
        }

    def test_suppress_email_is_passed_through(self, main_document, mocked_responses):
        mocked_responses.add("POST", ACL + "/permissions", json={})
        main_document.share("a@b.com", access="write", suppress_email=True)

        assert _json.loads(
            mocked_responses.calls[-1].request.body)["suppressEmail"] is True

    def test_a_share_that_emails_is_never_replayed(self, retrying_coda,
                                                   mocked_responses):
        """
        Without suppressEmail a retry sends a second invitation, which is a
        visible act rather than a repeated one. So a 500 is not retried.
        """
        url = BASE_URL + "/docs/d1/acl/permissions"
        for _ in range(3):
            mocked_responses.add("POST", url, status=500, json={"message": "boom"})

        with pytest.raises(err.ServerError):
            retrying_coda.add_permission(
                "d1", access="readonly",
                principal={"type": "email", "email": "a@b.com"})

        assert len(mocked_responses.calls) == 1

    def test_a_silent_share_may_be_replayed(self, retrying_coda, mocked_responses):
        url = BASE_URL + "/docs/d1/acl/permissions"
        mocked_responses.add("POST", url, status=500, json={"message": "boom"})
        mocked_responses.add("POST", url, json={"id": "p-1"})

        retrying_coda.add_permission(
            "d1", access="readonly", suppress_email=True,
            principal={"type": "email", "email": "a@b.com"})

        assert len(mocked_responses.calls) == 2


class TestReading:
    def test_permissions_are_typed(self, main_document, mocked_responses):
        mocked_responses.add(
            "GET", ACL + "/permissions",
            json={"items": [
                {"id": "p-1", "access": "readonly",
                 "principal": {"type": "email", "email": "alice@example.com"}},
                {"id": "p-2", "access": "write",
                 "principal": {"type": "anyone"}},
            ]})
        permissions = main_document.permissions()

        assert [p.access for p in permissions] == ["readonly", "write"]
        assert permissions[0].principal.email == "alice@example.com"
        assert isinstance(permissions[1].principal, AnyonePrincipal)

    def test_metadata_says_what_this_token_may_do(self, main_document,
                                                  mocked_responses):
        mocked_responses.add(
            "GET", ACL + "/metadata",
            json={"canShare": False, "canShareWithWorkspace": False,
                  "canShareWithOrg": False, "canCopy": True})
        metadata = main_document.acl_metadata()

        assert metadata.can_share is False
        assert metadata.can_copy is True

    def test_settings_are_typed(self, main_document, mocked_responses):
        mocked_responses.add(
            "GET", ACL + "/settings",
            json={"allowEditorsToChangePermissions": True, "allowCopying": False,
                  "allowViewersToRequestEditing": True})
        settings = main_document.acl_settings()

        assert isinstance(settings, AclSettings)
        assert settings.allow_copying is False


class TestRevoking:
    def test_unshare_accepts_a_permission_or_an_id(self, main_document,
                                                   mocked_responses):
        mocked_responses.add("DELETE", ACL + "/permissions/p-1", json={})
        main_document.unshare("p-1")

        assert mocked_responses.calls[-1].request.method == "DELETE"
        assert mocked_responses.calls[-1].request.url.endswith("/permissions/p-1")


class TestSettings:
    def test_only_what_is_passed_is_changed(self, main_document, mocked_responses):
        mocked_responses.add("PATCH", ACL + "/settings", json={})
        mocked_responses.add("GET", ACL + "/settings", json={"allowCopying": False})
        main_document.update_acl_settings(allow_copying=False)

        patch = [c for c in mocked_responses.calls if c.request.method == "PATCH"][0]
        assert _json.loads(patch.request.body) == {"allowCopying": False}

    def test_changing_nothing_is_refused(self, coda, mocked_responses):
        with pytest.raises(err.InvalidQuery, match="nothing to change"):
            coda.update_acl_settings("d1")

        assert not mocked_responses.calls
