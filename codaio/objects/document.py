"""
`Document` and `Folder`.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List

import attr
from dateutil.parser import parse

from codaio import err
from codaio.client import Coda
from codaio.objects.base import CodaObject
from codaio.objects.page import Section
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
    coda: Coda = attr.ib(repr=False)

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
        data = self.coda.get(self.href + "/")
        if not data:
            raise err.DocumentNotFound(f"No document with id {self.id}")
        self.name = data["name"]
        self.owner = data["owner"]
        self.created_at = parse(data["createdAt"])
        self.updated_at = parse(data["updatedAt"])
        self.type = data["type"]
        self.browser_link = data["browserLink"]

    def list_sections(self, offset: int = None, limit: int = None) -> List[Section]:
        """
        Returns a list of `Section` objects for each section in the document.

        :param limit: Maximum number of results to return in this query.

        :param offset: An opaque token used to fetch the next page of results.

        :return:
        """
        return [
            Section.from_json(i, document=self)
            for i in self.coda.list_sections(self.id, offset=offset, limit=limit)[
                "items"
            ]
        ]

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
    A folder.

    Note it has no `href`, which is why the base makes that field optional --
    every other object the API returns carries one.
    """

    name: str = None
    browser_link: str = attr.ib(default=None, repr=False)
