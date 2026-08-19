"""
Who can see a doc, and what they may do with it.

Sharing is the one part of this API where a mistake gives someone access they
should not have, so the surface here is deliberately blunt about it: `access` is
keyword-only with no default anywhere, a principal has to say what kind of thing
it is, and the access level that means "revoke" cannot be used to grant.

The principal types are a discriminated union -- an email address, a group, a
whole domain, a workspace, or literally anyone -- and, as everywhere else in
codaio, one this version has not heard of is kept rather than refused.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, Optional

import attr

from codaio import err  # noqa: F401
from codaio._endpoints import (  # noqa: F401
    ACCESS_TYPES,
    GRANTABLE_ACCESS_TYPES,
    check_grantable,  # re-exported: it belongs with these constants, and lives
                      # below the client so the client can use it too
)

#: What a permission grants. `none` is only ever *read* -- see `AccessType.NONE`.
READONLY = "readonly"
WRITE = "write"
COMMENT = "comment"
NONE = "none"


class AccessType:
    """
    The access levels a permission can carry.

    >>> AccessType.READONLY
    'readonly'

    `NONE` appears when reading permissions but cannot be granted -- the API's
    add-permission body excludes it, and revoking is a delete rather than a
    grant of nothing.

    >>> AccessType.GRANTABLE
    ('comment', 'readonly', 'write')
    """

    READONLY: ClassVar[str] = READONLY
    WRITE: ClassVar[str] = WRITE
    COMMENT: ClassVar[str] = COMMENT
    NONE: ClassVar[str] = NONE

    #: Everything a permission may be read as.
    ALL: ClassVar[tuple] = ACCESS_TYPES
    #: Everything that may be granted. Deliberately excludes NONE.
    GRANTABLE: ClassVar[tuple] = GRANTABLE_ACCESS_TYPES


class PrincipalType:
    """
    The kinds of thing a doc can be shared with.

    >>> PrincipalType.EMAIL, PrincipalType.ANYONE
    ('email', 'anyone')
    """

    EMAIL: ClassVar[str] = "email"
    GROUP: ClassVar[str] = "group"
    DOMAIN: ClassVar[str] = "domain"
    WORKSPACE: ClassVar[str] = "workspace"
    ANYONE: ClassVar[str] = "anyone"
    INTERNAL_ACCESS: ClassVar[str] = "internalAccess"


@attr.s(auto_attribs=True, eq=False, repr=False)
class Principal:
    """
    Someone or something a doc can be shared with.

    Build one with the constructors rather than by hand, so the payload is the
    shape the API expects:

    >>> Principal.email("alice@example.com").to_json()
    {'type': 'email', 'email': 'alice@example.com'}
    >>> Principal.anyone().to_json()
    {'type': 'anyone'}
    """

    type: str = None
    raw: Dict[str, Any] = attr.ib(factory=dict, repr=False)

    @classmethod
    def from_json(cls, js: Dict) -> "Principal":
        """
        Build the most specific principal the payload's `type` names.

        >>> Principal.from_json({"type": "domain", "domain": "example.com"})
        DomainPrincipal(domain='example.com')

        A type this version does not model is kept rather than refused:

        >>> Principal.from_json({"type": "somethingNew", "detail": 1})
        UnknownPrincipal(type='somethingNew')
        """
        js = js or {}
        target = _PRINCIPAL_TYPES.get(js.get("type"), UnknownPrincipal)
        return target._build(js)

    @classmethod
    def _build(cls, js: Dict) -> "Principal":
        return cls(type=js.get("type"), raw=dict(js))

    def to_json(self) -> Dict:
        """The payload to send when granting this principal access."""
        return dict(self.raw)

    # -- constructors ------------------------------------------------------

    @staticmethod
    def email(address: str) -> "EmailPrincipal":
        """One person, by email address."""
        return EmailPrincipal(
            type=PrincipalType.EMAIL, email=address,
            raw={"type": PrincipalType.EMAIL, "email": address},
        )

    @staticmethod
    def group(group_id: str, group_name: str = None) -> "GroupPrincipal":
        """A group, by id."""
        raw = {"type": PrincipalType.GROUP, "groupId": group_id}
        if group_name is not None:
            raw["groupName"] = group_name
        return GroupPrincipal(
            type=PrincipalType.GROUP, group_id=group_id, group_name=group_name, raw=raw
        )

    @staticmethod
    def domain(domain: str) -> "DomainPrincipal":
        """Everyone with an email address at this domain."""
        return DomainPrincipal(
            type=PrincipalType.DOMAIN, domain=domain,
            raw={"type": PrincipalType.DOMAIN, "domain": domain},
        )

    @staticmethod
    def workspace(workspace_id: str) -> "WorkspacePrincipal":
        """Everyone in a workspace."""
        return WorkspacePrincipal(
            type=PrincipalType.WORKSPACE, workspace_id=workspace_id,
            raw={"type": PrincipalType.WORKSPACE, "workspaceId": workspace_id},
        )

    @staticmethod
    def anyone() -> "AnyonePrincipal":
        """
        Anyone at all, which is to say the public.

        Worth pausing over: a doc shared this way is readable by anyone who
        finds the link.
        """
        return AnyonePrincipal(
            type=PrincipalType.ANYONE, raw={"type": PrincipalType.ANYONE}
        )

    def __eq__(self, other):
        if not isinstance(other, Principal):
            return NotImplemented
        return type(self) is type(other) and self.raw == other.raw

    def __hash__(self):
        return hash((type(self).__name__, tuple(sorted(self.raw.items(), key=str))))

    def __repr__(self):
        return f"{type(self).__name__}(type={self.type!r})"


@attr.s(auto_attribs=True, eq=False, repr=False)
class EmailPrincipal(Principal):
    """One person, by email address."""

    email: str = None

    @classmethod
    def _build(cls, js):
        return cls(type=js.get("type"), email=js.get("email"), raw=dict(js))

    def __repr__(self):
        return f"EmailPrincipal(email={self.email!r})"


@attr.s(auto_attribs=True, eq=False, repr=False)
class GroupPrincipal(Principal):
    """A group of people."""

    group_id: str = None
    group_name: str = None

    @classmethod
    def _build(cls, js):
        return cls(type=js.get("type"), group_id=js.get("groupId"),
                   group_name=js.get("groupName"), raw=dict(js))

    def __repr__(self):
        return f"GroupPrincipal(group_name={self.group_name!r})"


@attr.s(auto_attribs=True, eq=False, repr=False)
class DomainPrincipal(Principal):
    """Everyone with an email address at a domain."""

    domain: str = None

    @classmethod
    def _build(cls, js):
        return cls(type=js.get("type"), domain=js.get("domain"), raw=dict(js))

    def __repr__(self):
        return f"DomainPrincipal(domain={self.domain!r})"


@attr.s(auto_attribs=True, eq=False, repr=False)
class WorkspacePrincipal(Principal):
    """Everyone in a workspace."""

    workspace_id: str = None

    @classmethod
    def _build(cls, js):
        return cls(type=js.get("type"), workspace_id=js.get("workspaceId"),
                   raw=dict(js))

    def __repr__(self):
        return f"WorkspacePrincipal(workspace_id={self.workspace_id!r})"


@attr.s(auto_attribs=True, eq=False, repr=False)
class AnyonePrincipal(Principal):
    """The public."""

    def __repr__(self):
        return "AnyonePrincipal()"


@attr.s(auto_attribs=True, eq=False, repr=False)
class InternalAccessPrincipal(Principal):
    """Everyone inside the organisation, at some level the payload names."""

    internal_access_type: str = None

    @classmethod
    def _build(cls, js):
        return cls(type=js.get("type"),
                   internal_access_type=js.get("internalAccessType"), raw=dict(js))

    def __repr__(self):
        return f"InternalAccessPrincipal(level={self.internal_access_type!r})"


@attr.s(auto_attribs=True, eq=False, repr=False)
class UnknownPrincipal(Principal):
    """A principal type this version of codaio does not model. Kept, not refused."""

    def __repr__(self):
        return f"UnknownPrincipal(type={self.type!r})"


_PRINCIPAL_TYPES = {
    PrincipalType.EMAIL: EmailPrincipal,
    PrincipalType.GROUP: GroupPrincipal,
    PrincipalType.DOMAIN: DomainPrincipal,
    PrincipalType.WORKSPACE: WorkspacePrincipal,
    PrincipalType.ANYONE: AnyonePrincipal,
    PrincipalType.INTERNAL_ACCESS: InternalAccessPrincipal,
}


def as_principal(value) -> Principal:
    """
    Take a `Principal`, a payload, or an email address.

    A bare string is read as an email address, since that is overwhelmingly what
    sharing means -- but only if it looks like one, so a typo becomes an error
    rather than a share with something unintended.

    >>> as_principal("alice@example.com")
    EmailPrincipal(email='alice@example.com')
    """
    if isinstance(value, Principal):
        return value
    if isinstance(value, dict):
        return Principal.from_json(value)
    if isinstance(value, str):
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise err.InvalidQuery(
                f"{value!r} is not an email address. Pass a Principal if you mean "
                f"something else: Principal.domain(...), Principal.group(...), "
                f"Principal.workspace(...) or Principal.anyone()."
            )
        return Principal.email(value)
    raise err.InvalidQuery(
        f"expected a Principal, a payload or an email address, got {value!r}"
    )


@attr.s(auto_attribs=True, eq=False, repr=False)
class Permission:
    """
    One grant of access to a doc.

    >>> permission = Permission.from_json({
    ...     "id": "p-1", "access": "readonly",
    ...     "principal": {"type": "email", "email": "alice@example.com"},
    ... })
    >>> permission.access, permission.principal.email
    ('readonly', 'alice@example.com')
    """

    id: str = None
    access: str = None
    principal: Principal = None
    raw: Dict[str, Any] = attr.ib(factory=dict, repr=False)

    @classmethod
    def from_json(cls, js: Dict) -> "Permission":
        """Build a permission from an API payload."""
        return cls(
            id=js.get("id"),
            access=js.get("access"),
            principal=Principal.from_json(js.get("principal") or {}),
            raw=dict(js),
        )

    def __repr__(self):
        return f"Permission(access={self.access!r}, principal={self.principal!r})"


@attr.s(auto_attribs=True, eq=False, repr=False)
class AclMetadata:
    """
    What the current token is allowed to do about sharing.

    Worth reading before trying to share: a token may be able to read a doc
    without being able to change who else can.

    >>> meta = AclMetadata.from_json({
    ...     "canShare": False, "canShareWithWorkspace": False,
    ...     "canShareWithOrg": False, "canCopy": True,
    ... })
    >>> meta.can_share, meta.can_copy
    (False, True)
    """

    can_share: bool = None
    can_share_with_workspace: bool = None
    can_share_with_org: bool = None
    can_copy: bool = None
    raw: Dict[str, Any] = attr.ib(factory=dict, repr=False)

    @classmethod
    def from_json(cls, js: Dict) -> "AclMetadata":
        """Build from an API payload."""
        return cls(
            can_share=js.get("canShare"),
            can_share_with_workspace=js.get("canShareWithWorkspace"),
            can_share_with_org=js.get("canShareWithOrg"),
            can_copy=js.get("canCopy"),
            raw=dict(js),
        )

    def __repr__(self):
        return f"AclMetadata(can_share={self.can_share!r}, can_copy={self.can_copy!r})"


@attr.s(auto_attribs=True, eq=False, repr=False)
class AclSettings:
    """
    A doc's sharing settings, as opposed to its individual permissions.

    >>> settings = AclSettings.from_json({
    ...     "allowEditorsToChangePermissions": True, "allowCopying": False,
    ...     "allowViewersToRequestEditing": True,
    ... })
    >>> settings.allow_copying
    False
    """

    allow_editors_to_change_permissions: bool = None
    allow_copying: bool = None
    allow_viewers_to_request_editing: bool = None
    raw: Dict[str, Any] = attr.ib(factory=dict, repr=False)

    @classmethod
    def from_json(cls, js: Dict) -> "AclSettings":
        """Build from an API payload."""
        return cls(
            allow_editors_to_change_permissions=js.get(
                "allowEditorsToChangePermissions"),
            allow_copying=js.get("allowCopying"),
            allow_viewers_to_request_editing=js.get("allowViewersToRequestEditing"),
            raw=dict(js),
        )

    def __repr__(self):
        return (
            f"AclSettings(allow_copying={self.allow_copying!r}, "
            f"allow_editors_to_change_permissions="
            f"{self.allow_editors_to_change_permissions!r})"
        )


def optional(value) -> Optional[bool]:
    """Pass a bool through, leaving None alone. Used for tri-state settings."""
    return value
