"""
The base every API object shares, and the references they point at each other by.

Two things here are worth understanding before adding a class.

**Unknown fields are never fatal.** Building an object used to splat every JSON
key into the constructor, so a field the API had added since the class was
written raised `TypeError` and the whole call failed. That is a bad trade for a
client: a new field is the API's business, and a library that breaks on one is
useless the moment the service moves. Keys with no declared field are kept on
`.raw` and reachable through `.field()`, so nothing is lost and nothing breaks.

**Identity is `(class, id)`, and is stated rather than derived.** attrs was
previously asked to build `__hash__` out of every field, which meant hashing a
table whose `filter` is a dict raised `TypeError: unhashable type` -- the classes
advertised a hashability they never actually had. An id is what the API means by
identity, so that is what identity uses.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any, Dict, Tuple

import attr
import inflection

from codaio import err

if TYPE_CHECKING:  # pragma: no cover
    from codaio.objects.document import Document

#: Emit `err.UnknownFieldWarning` when the API returns a field codaio does not
#: model. Off by default -- it is a prompt to model something, not a problem --
#: but turning it on is how you find out the API has grown.
WARN_ON_UNKNOWN_FIELDS = False


def _underscore(key: str) -> str:
    """`browserLink` -> `browser_link`, leaving anything unusual alone."""
    return inflection.underscore(key)


@attr.s(auto_attribs=True, eq=False, repr=False)
class Reference:
    """
    A pointer to another object, in the shape the API embeds it.

    The API includes these all over the place -- a table's `parent` page, a
    column's `parent` table, a page's `children`. They carry an id and a name but
    not the full object, so they are cheap to return and can be followed on
    demand with :meth:`resolve`.

    >>> reference = Reference.from_json(
    ...     {"id": "canvas-1", "type": "page", "name": "Launch"}
    ... )
    >>> reference
    PageReference(id='canvas-1', name='Launch')

    Fetching what it points at needs the document it lives in:

    .. code-block:: python

        page = table.parent.resolve(doc)
    """

    id: str = None
    type: str = None
    href: str = attr.ib(default=None, repr=False)
    browser_link: str = attr.ib(default=None, repr=False)
    name: str = None
    raw: Dict[str, Any] = attr.ib(factory=dict, repr=False)

    #: Keys of an embedded reference, in the API's spelling.
    _FIELDS = ("id", "type", "href", "browserLink", "name")

    @classmethod
    def from_json(cls, js: Dict) -> "Reference":
        """Build the most specific reference type the payload's `type` names."""
        target = _REFERENCE_TYPES.get(js.get("type"), cls) if cls is Reference else cls
        known = {_underscore(k): v for k, v in js.items() if k in Reference._FIELDS}
        return target(raw=dict(js), **known)

    def resolve(self, document: "Document" = None):
        """
        Fetch the object this points at.

        Subclasses that know how override this; the base cannot, because a bare
        reference does not say what kind of thing it is.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not know how to resolve itself; use the "
            f"id ({self.id!r}) with the appropriate lookup."
        )

    def __eq__(self, other):
        if type(self) is not type(other):
            return NotImplemented
        return self.id == other.id

    def __hash__(self):
        return hash((type(self).__name__, self.id))

    def __repr__(self):
        return f"{type(self).__name__}(id={self.id!r}, name={self.name!r})"


@attr.s(auto_attribs=True, eq=False, repr=False)
class PageReference(Reference):
    def resolve(self, document: "Document" = None):
        return document.get_page(self.id)


@attr.s(auto_attribs=True, eq=False, repr=False)
class TableReference(Reference):
    def resolve(self, document: "Document" = None):
        return document.get_table(self.id)


@attr.s(auto_attribs=True, eq=False, repr=False)
class ColumnReference(Reference):
    pass


@attr.s(auto_attribs=True, eq=False, repr=False)
class FolderReference(Reference):
    pass


@attr.s(auto_attribs=True, eq=False, repr=False)
class WorkspaceReference(Reference):
    pass


_REFERENCE_TYPES = {
    "page": PageReference,
    "table": TableReference,
    "column": ColumnReference,
    "folder": FolderReference,
    "workspace": WorkspaceReference,
}


def ref(value):
    """Converter: JSON object, `Reference`, or None -> `Reference` or None."""
    if value is None or isinstance(value, Reference):
        return value
    if isinstance(value, dict):
        return Reference.from_json(value)
    return value


def refs(value) -> Tuple[Reference, ...]:
    """Converter: a list of embedded references -> a tuple of `Reference`."""
    if value is None:
        return ()
    return tuple(ref(item) for item in value)


@attr.s(auto_attribs=True, eq=False, repr=False)
class ColumnFormat:
    """
    A column's type, and whatever formatting options came with it.

    Deliberately one tolerant class rather than a subclass per format. The API's
    own union has 24 type names but only 16 shapes, and six of those names
    (`text`, `canvas`, `image`, `attachments`, `packObject`, `other`) share the
    same near-empty one -- while `email`, `link` and `reaction` appear in the
    enum without being in the discriminator mapping at all, so a strict client
    would fail to build anything for them. `type` plus the untouched payload
    answers every question worth asking without inheriting that mess.
    """

    type: str = None
    is_array: bool = False
    raw: Dict[str, Any] = attr.ib(factory=dict, repr=False)

    @classmethod
    def from_json(cls, js: Dict) -> "ColumnFormat":
        return cls(
            type=js.get("type"),
            is_array=bool(js.get("isArray", False)),
            raw=dict(js),
        )

    def __getitem__(self, key):
        """Any formatting option the API sent, by its own name."""
        return self.raw[key]

    def get(self, key, default=None):
        return self.raw.get(key, default)

    def __repr__(self):
        return f"ColumnFormat(type={self.type!r}, is_array={self.is_array!r})"


def column_format(value):
    """Converter: the `format` payload -> `ColumnFormat`."""
    if value is None or isinstance(value, ColumnFormat):
        return value
    if isinstance(value, dict):
        return ColumnFormat.from_json(value)
    return value


@attr.s(auto_attribs=True, eq=False, repr=False)
class CodaObject:
    """
    Base for every object the API returns.

    Subclasses declare the fields they care about. Anything else the API sends is
    kept on :attr:`raw` rather than discarded or fatal, so a payload that grows a
    field still builds, and the new field is readable through :meth:`field`
    before codaio has any opinion about it.
    """

    id: str = None
    type: str = attr.ib(default=None, repr=False)
    # Not universal: a folder has no href, so this cannot be required.
    href: str = attr.ib(default=None, repr=False)
    document: "Document" = attr.ib(default=None, repr=False, kw_only=True)
    #: The payload exactly as received, in the API's own spelling. Treat as
    #: read-only; it is what makes a round trip lossless.
    raw: Dict[str, Any] = attr.ib(factory=dict, repr=False, kw_only=True)

    # -- construction ------------------------------------------------------

    @classmethod
    def _init_names(cls) -> Dict[str, str]:
        """{snake_case name: init argument name}, computed once per class."""
        cached = cls.__dict__.get("_INIT_NAMES")
        if cached is None:
            cached = {
                a.name.lstrip("_"): a.name.lstrip("_")
                for a in attr.fields(cls)
                if a.init
            }
            cls._INIT_NAMES = cached
        return cached

    @classmethod
    def from_json(cls, js: Dict, *, document: "Document" = None, **extra):
        """
        Build an instance, keeping whatever this class does not model.

        Keys are matched by their snake_case spelling. Anything unmatched goes to
        `.raw` only -- which is the whole point: the API adding a field must not
        be able to break a caller who never asked about it.

        >>> from codaio import Page
        >>> page = Page.from_json({
        ...     "id": "canvas-1", "type": "page", "name": "Launch",
        ...     "isHidden": False, "aFieldFromNextYear": {"kept": True},
        ... })
        >>> page.name, page.is_hidden
        ('Launch', False)
        >>> page.unknown_fields
        {'aFieldFromNextYear': {'kept': True}}
        """
        known = cls._init_names()
        kwargs = {}
        unknown = []
        for key, value in js.items():
            name = _underscore(key)
            if name in known and name not in ("raw", "document"):
                kwargs[name] = value
            else:
                unknown.append(key)

        if unknown and WARN_ON_UNKNOWN_FIELDS:
            warnings.warn(
                f"{cls.__name__}: the API returned fields codaio does not model: "
                f"{sorted(unknown)}. They are kept, and readable via .field().",
                err.UnknownFieldWarning,
                stacklevel=2,
            )

        kwargs.update(extra)
        return cls(**kwargs, document=document, raw=dict(js))

    # -- reading what was sent --------------------------------------------

    def field(self, key: str, default=None):
        """
        Any field of the payload, declared or not, by either spelling.

        `page.field("subtitle")` and `page.field("isHidden")` both work, so a
        field codaio has not modelled yet is still reachable without digging
        through `.raw` by hand.

        >>> from codaio import Page
        >>> page = Page.from_json(
        ...     {"id": "canvas-1", "type": "page", "isHidden": True, "brandNew": 7}
        ... )
        >>> page.field("isHidden"), page.field("is_hidden")
        (True, True)
        >>> page.field("brandNew")
        7
        >>> page.field("nothing_like_this", "fallback")
        'fallback'
        """
        if key in self.raw:
            return self.raw[key]
        for raw_key, value in self.raw.items():
            if _underscore(raw_key) == key:
                return value
        return default

    @property
    def unknown_fields(self) -> Dict[str, Any]:
        """
        Payload keys this class has no field for.

        Empty when codaio is up to date with the API. Non-empty is a to-do list,
        not an error.

        >>> from codaio import Table
        >>> Table.from_json({"id": "grid-1", "type": "table"}).unknown_fields
        {}
        """
        known = self._init_names()
        return {
            key: value
            for key, value in self.raw.items()
            if _underscore(key) not in known
        }

    def to_json(self) -> Dict[str, Any]:
        """The payload as received. Lossless, including fields codaio ignores."""
        return dict(self.raw)

    # -- identity ----------------------------------------------------------

    def __eq__(self, other):
        """
        Two objects of the same class with the same id are the same object.

        Deliberately not a field-by-field comparison. A row re-read after an edit
        is still that row, and comparing by content would also drag in the back
        references -- so whether two rows were equal would depend on which client
        fetched them, which is not a question anyone means to ask.

        >>> from codaio import Page, Table
        >>> before = Page.from_json({"id": "canvas-1", "type": "page", "name": "A"})
        >>> after = Page.from_json({"id": "canvas-1", "type": "page", "name": "B"})
        >>> before == after
        True

        Compare what you actually mean when you mean content:

        >>> before.name == after.name
        False

        Nothing of a different class matches, even sharing an id:

        >>> Page.from_json({"id": "x"}) == Table.from_json({"id": "x"})
        False
        """
        if type(self) is not type(other):
            return NotImplemented
        return self.id == other.id

    def __hash__(self):
        return hash((type(self).__name__, self.id))

    def __repr__(self):
        name = getattr(self, "name", None)
        if name is None:
            return f"{type(self).__name__}(id={self.id!r})"
        return f"{type(self).__name__}(id={self.id!r}, name={name!r})"

    # -- metadata ----------------------------------------------------------

    def meta_to_dict(self, incl_doc=False) -> Dict:
        """ return the metdata about this CodaObject as a dict.
        Expected that derived types will also call this function."""

        meta = {'id': self.id, 'type': self.type, 'href': self.href}
        if incl_doc:
            meta['doc'] = self.document

        return meta
