"""
`Document` and `Folder`.
"""

from __future__ import annotations

import datetime as dt
import warnings
from typing import Dict, Iterator, List, Optional

import attr
from dateutil.parser import parse

from codaio import err
from codaio.client import Coda
from codaio.objects.base import CodaObject, Reference, WorkspaceReference, ref
from codaio.objects.acl import (
    AclMetadata,
    AclSettings,
    Permission,
    Principal,
    as_principal,
)
from codaio.objects.misc import Control, Formula
from codaio.objects.mutation import Mutation
from codaio.objects.page import Page, PageTree, _content_payload
from codaio.objects.table import Table


@attr.s(eq=False, repr=False)
class Document:
    """Main class for interacting with coda.io API using `codaio` objects."""

    id: str = attr.ib(repr=False)
    type: str = attr.ib(init=False, repr=False)
    href: str = attr.ib(init=False, repr=False)
    name: str = attr.ib(init=False)
    owner: str = attr.ib(init=False)
    created_at: dt.datetime = attr.ib(init=False, repr=False)
    updated_at: dt.datetime = attr.ib(init=False, repr=False)
    browser_link: str = attr.ib(init=False)
    folder: "Reference" = attr.ib(init=False, repr=False, default=None)
    workspace: "Reference" = attr.ib(init=False, repr=False, default=None)
    #: The payload this was built from, exactly as received.
    raw: Dict = attr.ib(init=False, repr=False, factory=dict)
    coda: Coda = attr.ib(repr=False)
    # A payload already in hand, so `from_json` can skip the fetch.
    _prefetched: Dict = attr.ib(default=None, repr=False, kw_only=True)

    def __eq__(self, other):
        """Same id, same doc -- see `CodaObject.__eq__` for why identity, not content."""
        if type(self) is not type(other):
            return NotImplemented
        return self.id == other.id

    def __hash__(self):
        return hash((type(self).__name__, self.id))

    def __repr__(self):
        return f"Document(id={self.id!r}, name={getattr(self, 'name', None)!r})"

    def meta_to_dict(self, incl_coda=False) -> Dict:
        """
        product a dict of the metadata for the Document.  Omits the Coda object by default.  
        """
        # meta_super = super().meta_to_dict() ## currently no super for the Document class...

        meta = {'id': self.id,
                'type': self.type,
                'href': self.href,
                'name': self.name,
                'owner': self.owner,
                'created_at': self.created_at,
                'updated_at': self.updated_at,
                'browser_link': self.browser_link}

        if incl_coda:
            meta['coda'] = self.coda

        return meta

    @classmethod
    def from_environment(cls, doc_id: str, keyring_profile: str = None):
        """
        Instantiates a `Document` with a stored API key.

        :param doc_id: ID of the doc. Example: "AbCDeFGH"

        :param keyring_profile: which stored token to use.

        :return:
        """
        return cls(id=doc_id, coda=Coda.from_environment(keyring_profile=keyring_profile))

    @classmethod
    def from_credentials(
        cls, doc_id: str, keyring_profile: str = None, keyring_service: str = None
    ):
        """
        Instantiates a `Document` using a stored API token.

        A more descriptive spelling of `from_environment`, for the common
        case of one API token per docset::

            doc = Document.from_credentials("AbCDeFGH", keyring_profile="research")

        :param doc_id: ID of the doc. Example: "AbCDeFGH"

        :param keyring_profile: the keyring entry's username.

        :param keyring_service: the keyring entry's service name; defaults
            to "codaio".

        :return:
        """
        return cls(
            id=doc_id,
            coda=Coda(
                keyring_profile=keyring_profile, keyring_service=keyring_service
            ),
        )

    def __attrs_post_init__(self):
        self.href = f"/docs/{self.id}"
        if self._prefetched is not None:
            self._absorb(self._prefetched)
            return
        data = self.coda.get(self.href + "/")
        if not data:
            raise err.DocumentNotFound(f"No document with id {self.id}")
        self._absorb(data)

    def _absorb(self, data: Dict) -> None:
        self.raw = dict(data)
        self.name = data.get("name")
        self.owner = data.get("owner")
        self.created_at = parse(data["createdAt"]) if data.get("createdAt") else None
        self.updated_at = parse(data["updatedAt"]) if data.get("updatedAt") else None
        self.type = data.get("type")
        self.browser_link = data.get("browserLink")
        self.folder = ref(data.get("folder"))
        self.workspace = ref(data.get("workspace"))

    @classmethod
    def from_json(cls, js: Dict, *, coda: Coda) -> "Document":
        """
        Build a document from a payload already in hand, with no request.

        Listing docs returns them in full, so constructing each one by fetching
        it again would be a request per doc for information already received.

        .. code-block:: python

            docs = [Document.from_json(item, coda=coda)
                    for item in coda.list_docs()["items"]]
        """
        return cls(id=js["id"], coda=coda, prefetched=js)

    @property
    def folder_id(self) -> Optional[str]:
        """The id of the folder this doc lives in, if the payload named one."""
        return self.folder.id if self.folder else None

    @property
    def workspace_id(self) -> Optional[str]:
        """The id of the workspace this doc lives in, if the payload named one."""
        return self.workspace.id if self.workspace else None

    def get_folder(self) -> "Folder":
        """
        Fetch the folder this doc lives in.

        .. code-block:: python

            print(doc.get_folder().name)
        """
        if not self.folder_id:
            raise err.FolderNotFound(
                f"doc {self.id!r} did not come with a folder; it may have been "
                f"built from a payload that omitted one."
            )
        return Folder.from_json(self.coda.get_folder(self.folder_id), coda=self.coda)

    def list_pages(self, offset: str = None, limit: int = None) -> List[Page]:
        """
        Returns a `Page` for every page in the document.

        The listing is flat. Use :meth:`page_tree` when you want the hierarchy.

        :param limit: Maximum number of results to return in this query.

        :param offset: An opaque token used to fetch the next page of results.

        :return:
        """
        return [
            Page.from_json(i, document=self)
            for i in self.coda.list_pages(self.id, offset=offset, limit=limit)["items"]
        ]

    def iter_pages(self, *, page_size: int = None) -> Iterator[Page]:
        """Walk the doc's pages, fetching lazily."""
        for item in self.coda.iter_items(
            f"/docs/{self.id}/pages", page_size=page_size
        ):
            yield Page.from_json(item, document=self)

    def get_page(self, page_id_or_name: str) -> Page:
        """
        Gets a `Page` by id or name.

        :param page_id_or_name: ID or name of the page. Names are discouraged --
            they are easily changed by users, and if several pages share one an
            arbitrary page is returned.

        :return:
        """
        js = self.coda.get_page(self.id, page_id_or_name)
        if not js:
            raise err.PageNotFound(f"{page_id_or_name}")
        return Page.from_json(js, document=self)

    def page_tree(self) -> PageTree:
        """
        The doc's page hierarchy, from a single listing.

        Each page carries both its parent and its children, so no extra requests
        are needed to work out the shape.

        .. code-block:: python

            for page, depth in doc.page_tree().walk():
                print("  " * depth + page.name)
        """
        return PageTree.from_pages(self.list_pages())

    def create_page(
        self,
        name: str = None,
        *,
        subtitle: str = None,
        icon_name: str = None,
        image_url: str = None,
        parent_page=None,
        content=None,
    ) -> Mutation:
        """
        Creates a page in this doc.

        :param name: the page's title.

        :param parent_page: a `Page`, a page reference, or a page id to nest
            this page under. **It has to exist already.** The 202 for a page
            creation hands back the new page's id immediately, but that id is not
            usable as a parent until the creation has actually been applied --
            measured at around 46 seconds, and usable at exactly the moment
            `mutationStatus` reports it completed, not before. Referencing it
            sooner is refused with `400 Invalid parentPageId: could not find a
            page with id ...`, consistently rather than intermittently.

            So building a page tree is inherently sequential: wait for each level
            before creating the next. Pages *within* a level can be created
            together and waited on once.

        :param content: a markdown string, or one of `CanvasContent`,
            `EmbedContent`, `SyncPageContent`.

        :return:

        .. code-block:: python

            doc.create_page("Notes", content="# Notes\n\nStarted today.").wait()

        Nest it under another page by passing that page or its id:

        .. code-block:: python

            handbook = doc.get_page("canvas-IjkLmnO")
            doc.create_page("Onboarding", parent_page=handbook).wait()

        A whole level at once, which costs one write's latency rather than one
        per page:

        .. code-block:: python

            writes = MutationGroup()
            for title in ("Onboarding", "Benefits", "Kit"):
                writes.add(doc.create_page(title, parent_page=handbook))
            writes.wait()
        """
        data = {}
        for key, value in (
            ("name", name), ("subtitle", subtitle), ("iconName", icon_name),
            ("imageUrl", image_url),
        ):
            if value is not None:
                data[key] = value
        if parent_page is not None:
            data["parentPageId"] = getattr(parent_page, "id", parent_page)
        payload = _content_payload(content)
        if payload is not None:
            data["pageContent"] = payload
        return Mutation.from_response(
            self.coda, self.coda.create_page(self.id, data)
        )

    # ------------------------------------------------------------------
    # Formulas and controls
    # ------------------------------------------------------------------

    def list_formulas(self, offset: str = None, limit: int = None) -> List[Formula]:
        """
        The doc's named formulas.

        .. code-block:: python

            for formula in doc.list_formulas():
                print(formula.name, formula.value)
        """
        return [
            Formula.from_json(i, document=self)
            for i in self.coda.list_formulas(self.id, offset=offset, limit=limit)["items"]
        ]

    def get_formula(self, formula_id_or_name: str) -> Formula:
        """Fetch one named formula by id or name."""
        return Formula.from_json(
            self.coda.get_formula(self.id, formula_id_or_name), document=self
        )

    def list_controls(self, offset: str = None, limit: int = None) -> List[Control]:
        """
        The doc's controls -- sliders, selects, buttons and the like.

        .. code-block:: python

            for control in doc.list_controls():
                print(control.name, control.control_type, control.value)
        """
        return [
            Control.from_json(i, document=self)
            for i in self.coda.list_controls(self.id, offset=offset, limit=limit)["items"]
        ]

    def get_control(self, control_id_or_name: str) -> Control:
        """Fetch one control by id or name."""
        return Control.from_json(
            self.coda.get_control(self.id, control_id_or_name), document=self
        )

    # ------------------------------------------------------------------
    # The doc itself
    # ------------------------------------------------------------------

    def update(self, *, title: str = None, icon_name: str = None) -> "Document":
        """
        Rename this doc or change its icon, and return it refreshed.

        .. code-block:: python

            doc.update(title="Q3 planning")
        """
        self.coda.update_doc(self.id, title=title, icon_name=icon_name)
        self._absorb(self.coda.get(self.href + "/"))
        return self

    def delete(self) -> Mutation:
        """
        Delete this doc.

        Queued rather than done on return, like other writes -- the API answers
        202. Wait on the result when that matters.

        .. code-block:: python

            doc.delete().wait()
        """
        return Mutation.from_response(self.coda, self.coda.delete_doc(self.id))

    # ------------------------------------------------------------------
    # Sharing
    # ------------------------------------------------------------------

    def acl_metadata(self) -> AclMetadata:
        """
        What this token may do about sharing the doc.

        Worth reading first: a token can be able to read a doc without being
        able to change who else can.

        .. code-block:: python

            if doc.acl_metadata().can_share:
                doc.share("alice@example.com", access="readonly")
        """
        return AclMetadata.from_json(self.coda.get_acl_metadata(self.id))

    def permissions(self, offset: str = None, limit: int = None) -> List[Permission]:
        """
        Who currently has access to this doc.

        .. code-block:: python

            for permission in doc.permissions():
                print(permission.access, permission.principal)
        """
        return [
            Permission.from_json(i)
            for i in self.coda.list_permissions(
                self.id, offset=offset, limit=limit
            )["items"]
        ]

    def share(self, principal, *, access: str, suppress_email: bool = None) -> Dict:
        """
        Grant access to this doc.

        `access` is keyword-only and has no default, deliberately. This is the
        one call in the library where a defaulting mistake gives somebody access
        they should not have, so it has to be said out loud.

        :param principal: an email address, or a :class:`codaio.Principal` for
            anything else -- a group, a domain, a workspace, or the public.

        :param access: "readonly", "write" or "comment".

        :param suppress_email: do not notify the recipient. Note that without it
            codaio will not retry an inconclusive request, since a replay would
            send a second invitation.

        .. code-block:: python

            doc.share("alice@example.com", access="readonly")
            doc.share(Principal.domain("example.com"), access="comment")

        Sharing with everyone means what it says -- anyone with the link:

        .. code-block:: python

            doc.share(Principal.anyone(), access="readonly")
        """
        return self.coda.add_permission(
            self.id,
            access=access,
            principal=as_principal(principal).to_json(),
            suppress_email=suppress_email,
        )

    def unshare(self, permission) -> Dict:
        """
        Revoke one grant of access.

        :param permission: a `Permission` or its id.

        .. code-block:: python

            for permission in doc.permissions():
                if permission.access == "write":
                    doc.unshare(permission)
        """
        permission_id = getattr(permission, "id", permission)
        return self.coda.delete_permission(self.id, permission_id)

    def search_principals(self, query: str = None) -> List[Principal]:
        """Find people and groups this doc could be shared with."""
        found = self.coda.search_principals(self.id, query=query)
        return [Principal.from_json(i) for i in found.get("items", [])]

    def acl_settings(self) -> AclSettings:
        """This doc's sharing settings, as opposed to its individual permissions."""
        return AclSettings.from_json(self.coda.get_acl_settings(self.id))

    def update_acl_settings(self, **settings) -> AclSettings:
        """
        Change this doc's sharing settings, and return them refreshed.

        .. code-block:: python

            doc.update_acl_settings(allow_copying=False)
        """
        self.coda.update_acl_settings(self.id, **settings)
        return self.acl_settings()

    def list_sections(self, offset: str = None, limit: int = None) -> List[Page]:
        """Deprecated. Use :meth:`list_pages`."""
        warnings.warn(
            "Document.list_sections is deprecated; use Document.list_pages.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.list_pages(offset=offset, limit=limit)

    def list_tables(self, offset: int = None, limit: int = None, data: Dict = None) -> List[Table]:
        """
        Returns a list of `Table` objects for each table in the document.

        :param limit: Maximum number of results to return in this query.

        :param offset: An opaque token used to fetch the next page of results.
        
        :param data: A dict of additional options/parameters to use in the query
        
        :return:
        """

        return [
            Table.from_json(i, document=self)
            for i in self.coda.list_tables(self.id, offset=offset, limit=limit, data=data)["items"]
        ]

    def get_table(self, table_id_or_name: str) -> Table:
        """
        Gets a Table object from table name or ID.

        :param table_id_or_name: ID or name of the table.
            Names are discouraged because they're easily prone to being changed by users.
            If you're using a name, be sure to URI-encode it. Example: "grid-pqRst-U"

        :return:
        """
        table_data = self.coda.get_table(self.id, table_id_or_name)
        if table_data:
            return Table.from_json(table_data, document=self)
        raise err.TableNotFound(f"{table_id_or_name}")


@attr.s(auto_attribs=True, eq=False, repr=False)
class Folder(CodaObject):
    """
    A folder in a workspace.

    Folders sit above docs rather than inside them, which is why they are
    reached through the client or a document rather than listed off a doc.

    Note it has no `href`, unlike every other object the API returns -- which is
    why the base class makes that field optional.

    .. code-block:: python

        folder = doc.folder.resolve(coda)
        for sibling in folder.docs():
            print(sibling.name)
    """

    name: str = None
    browser_link: str = attr.ib(default=None, repr=False)
    description: str = attr.ib(default=None, repr=False)
    icon: Dict = attr.ib(default=None, repr=False)
    can_edit: bool = attr.ib(default=None, repr=False)
    created_at: dt.datetime = attr.ib(
        default=None, converter=lambda x: parse(x) if x else None, repr=False
    )
    workspace: WorkspaceReference = attr.ib(default=None, converter=ref, repr=False)
    #: The client this folder was fetched with, so it can fetch more.
    coda: "Coda" = attr.ib(default=None, repr=False, kw_only=True)

    @classmethod
    def from_json(cls, js: Dict, *, coda: "Coda" = None, **extra) -> "Folder":
        """
        Build a folder from an API payload.

        >>> folder = Folder.from_json({
        ...     "id": "fl-1Ab234", "type": "folder", "name": "Research",
        ...     "workspace": {"id": "ws-abc", "type": "workspace"},
        ... })
        >>> folder.name, folder.workspace.id
        ('Research', 'ws-abc')
        """
        return super().from_json(js, coda=coda, **extra)

    @property
    def workspace_id(self) -> Optional[str]:
        """
        The id of the workspace this folder belongs to.

        >>> Folder.from_json({
        ...     "id": "fl-1", "type": "folder",
        ...     "workspace": {"id": "ws-abc", "type": "workspace"},
        ... }).workspace_id
        'ws-abc'
        """
        return self.workspace.id if self.workspace else None

    def docs(self, **kwargs) -> List["Document"]:
        """
        The docs in this folder.

        .. code-block:: python

            for doc in folder.docs():
                print(doc.name)
        """
        listing = self.coda.list_docs(folder_id=self.id, **kwargs)
        return [
            Document.from_json(item, coda=self.coda) for item in listing["items"]
        ]

    def rename(self, name: str = None, *, description: str = None) -> "Folder":
        """
        Rename this folder or change its description, and return it refreshed.

        .. code-block:: python

            folder.rename("Archived research")
        """
        self.coda.update_folder(self.id, name=name, description=description)
        return self.refresh()

    def delete(self) -> Dict:
        """Delete this folder."""
        return self.coda.delete_folder(self.id)

    def refresh(self) -> "Folder":
        """Re-read this folder from the API."""
        return Folder.from_json(self.coda.get_folder(self.id), coda=self.coda)
