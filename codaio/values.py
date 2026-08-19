"""
The structured values a cell can hold, and how to read and write them.

With ``valueFormat=rich`` the API stops flattening cells to strings and returns
JSON-LD objects instead: an image cell becomes an ``ImageObject``, a relation
becomes a ``StructuredValue`` pointing at another row, and so on. That is the
lossless read, and it is the one worth using -- the default ``simple`` format
joins array values into a comma-delimited string, which cannot be taken apart
again once a value contains a comma.

This module turns those objects into classes with named attributes, so asking
whether a cell holds an image is ``isinstance(value, ImageValue)`` rather than
checking ``value["@type"] == "ImageObject"`` and hoping the cell was not a list
this time. Every value keeps the payload it was built from on ``.raw``, and a
``@type`` this module has never heard of becomes an :class:`UnknownValue` rather
than an error -- new value types are the API's business, not a reason to fail.

Deliberately standalone: it imports :mod:`codaio.err` and :mod:`codaio.http` and
nothing else from the library, so the client and the object model can depend on
it without a cycle, and so it can be used on its own.
"""

from __future__ import annotations

import datetime as dt
import decimal
import re
from typing import IO, Any, ClassVar, Dict, List, Optional

import attr

from codaio import err
from codaio.http import fetch_untrusted


@attr.s(auto_attribs=True, eq=False, repr=False)
class CodaValue:
    """A structured cell value. `.raw` is exactly what the API sent."""

    raw: Dict[str, Any] = attr.ib(factory=dict)

    #: The JSON-LD `@type` this class represents.
    LD_TYPE: ClassVar[Optional[str]] = None

    @classmethod
    def from_json(cls, js: Dict) -> "CodaValue":
        return cls(raw=dict(js))

    def to_json(self) -> Dict:
        """The payload as received, for sending back unchanged."""
        return dict(self.raw)

    def __getitem__(self, key):
        return self.raw[key]

    def get(self, key, default=None):
        return self.raw.get(key, default)

    def __eq__(self, other):
        if not isinstance(other, CodaValue):
            return NotImplemented
        return type(self) is type(other) and self.raw == other.raw

    def __hash__(self):
        return hash((type(self).__name__, tuple(sorted(self.raw.items(), key=str))))

    def __repr__(self):
        return f"{type(self).__name__}({self.raw!r})"


@attr.s(auto_attribs=True, eq=False, repr=False)
class ImageValue(CodaValue):
    """
    An image or attachment cell.

    >>> photo = parse_value({
    ...     "@type": "ImageObject", "name": "dogs.jpg",
    ...     "url": "https://codahosted.io/dogs.jpg", "status": "live",
    ... })
    >>> photo.name
    'dogs.jpg'

    Reading the bytes is a request to a content host, not to the API, so no
    credentials are sent and codaio does not choose where they go -- the name
    was typed by whoever can edit the doc:

    .. code-block:: python

        destination = my_safe_path_for(photo.name)
        destination.write_bytes(photo.read())
    """

    LD_TYPE: ClassVar[str] = "ImageObject"

    name: str = None
    url: str = None
    width: float = None
    height: float = None
    #: "live", "deleted" or "failed".
    status: str = None

    @classmethod
    def from_json(cls, js: Dict) -> "ImageValue":
        return cls(
            raw=dict(js),
            name=js.get("name"),
            url=js.get("url"),
            width=js.get("width"),
            height=js.get("height"),
            status=js.get("status"),
        )

    def open(self, *, timeout: float = 30.0) -> IO[bytes]:
        """
        A streaming file-like over the attachment's bytes.

        No credentials are sent: see :func:`codaio.http.fetch_untrusted`. The
        file lives on a content host rather than the API, and the bearer token
        has no business being sent there.
        """
        return self._fetch(timeout=timeout, stream=True).raw

    def read(self, *, timeout: float = 30.0) -> bytes:
        """
        The attachment's bytes.

        codaio does the request and nothing else -- it does not choose a
        filename, and it does not write anything to disk. The `name` on this
        value was typed by whoever edits the doc, so deciding where bytes go and
        what they are called is the caller's business, with the caller's rules.
        """
        return self._fetch(timeout=timeout, stream=False).content

    def _fetch(self, *, timeout: float, stream: bool):
        if not self.url:
            raise err.AttachmentUnavailable(
                f"image {self.name!r} has no url to fetch"
            )
        if self.status == "deleted":
            raise err.AttachmentUnavailable(
                f"image {self.name!r} has been deleted from the doc"
            )
        return fetch_untrusted(self.url, timeout=timeout, stream=stream)

    def __repr__(self):
        return f"ImageValue(name={self.name!r}, url={self.url!r})"


@attr.s(auto_attribs=True, eq=False, repr=False)
class PersonValue(CodaValue):
    """A person cell."""

    LD_TYPE: ClassVar[str] = "Person"

    name: str = None
    email: str = None

    @classmethod
    def from_json(cls, js: Dict) -> "PersonValue":
        return cls(raw=dict(js), name=js.get("name"), email=js.get("email"))

    def __repr__(self):
        return f"PersonValue(name={self.name!r}, email={self.email!r})"


@attr.s(auto_attribs=True, eq=False, repr=False)
class LinkValue(CodaValue):
    """A hyperlink cell."""

    LD_TYPE: ClassVar[str] = "WebPage"

    name: str = None
    url: str = None

    @classmethod
    def from_json(cls, js: Dict) -> "LinkValue":
        return cls(raw=dict(js), name=js.get("name"), url=js.get("url"))

    def __repr__(self):
        return f"LinkValue(name={self.name!r}, url={self.url!r})"


@attr.s(auto_attribs=True, eq=False, repr=False)
class MoneyValue(CodaValue):
    """
    A currency cell.

    `amount` is a `Decimal`. The API sends it as either a number or a string,
    and putting money through a float loses cents for no benefit.

    >>> cost = parse_value(
    ...     {"@type": "MonetaryAmount", "currency": "USD", "amount": "12.99"}
    ... )
    >>> cost.currency, cost.amount
    ('USD', Decimal('12.99'))

    Exact whether the API sent a string or a number:

    >>> parse_value({"@type": "MonetaryAmount", "amount": 0.1}).amount
    Decimal('0.1')
    """

    LD_TYPE: ClassVar[str] = "MonetaryAmount"

    currency: str = None
    amount: decimal.Decimal = None

    @classmethod
    def from_json(cls, js: Dict) -> "MoneyValue":
        amount = js.get("amount")
        if amount is not None and not isinstance(amount, decimal.Decimal):
            try:
                amount = decimal.Decimal(str(amount))
            except decimal.InvalidOperation:
                amount = None
        return cls(raw=dict(js), currency=js.get("currency"), amount=amount)

    def __repr__(self):
        return f"MoneyValue(currency={self.currency!r}, amount={self.amount!r})"


@attr.s(auto_attribs=True, eq=False, repr=False)
class RowValue(CodaValue):
    """
    A reference to a row in another table -- a lookup or relation cell.

    Named for the API's own schema. It is a *pointer to* a row, not a
    :class:`codaio.Row`; :meth:`resolve` fetches the row itself.

    >>> link = parse_value({
    ...     "@type": "StructuredValue", "additionalType": "row",
    ...     "name": "Apple", "rowId": "i-tuVwxYz", "tableId": "grid-pqRst-U",
    ...     "url": "https://coda.io/d/x", "tableUrl": "https://coda.io/d/x#t",
    ... })
    >>> link.name, link.row_id, link.table_id
    ('Apple', 'i-tuVwxYz', 'grid-pqRst-U')

    Following it needs the document it lives in:

    .. code-block:: python

        row = link.resolve(doc)
    """

    LD_TYPE: ClassVar[str] = "StructuredValue"

    name: str = None
    url: str = None
    row_id: str = None
    table_id: str = None
    table_url: str = None
    additional_type: str = None

    @classmethod
    def from_json(cls, js: Dict) -> "RowValue":
        return cls(
            raw=dict(js),
            name=js.get("name"),
            url=js.get("url"),
            row_id=js.get("rowId"),
            table_id=js.get("tableId"),
            table_url=js.get("tableUrl"),
            additional_type=js.get("additionalType"),
        )

    def resolve(self, document):
        """Fetch the row this points at. Needs the `Document` it belongs to."""
        if not (self.table_id and self.row_id):
            raise err.RowNotFound(
                f"reference {self.name!r} does not name a table and row to fetch"
            )
        return document.get_table(self.table_id).get_row_by_id(self.row_id)

    def __repr__(self):
        return f"RowValue(name={self.name!r}, row_id={self.row_id!r})"


@attr.s(auto_attribs=True, eq=False, repr=False)
class UnknownValue(CodaValue):
    """
    A structured value whose `@type` this version of codaio does not model.

    Not an error. The API is free to introduce value types, and a client that
    refuses to build one is broken the day that happens. The payload is intact
    on `.raw` and reachable by key.
    """

    type: str = None

    @classmethod
    def from_json(cls, js: Dict) -> "UnknownValue":
        return cls(raw=dict(js), type=js.get("@type"))

    def __repr__(self):
        return f"UnknownValue(type={self.type!r}, raw={self.raw!r})"


VALUE_CLASSES = (ImageValue, PersonValue, LinkValue, MoneyValue, RowValue)
_BY_LD_TYPE = {cls.LD_TYPE: cls for cls in VALUE_CLASSES}


def parse_value(value):
    """
    Turn a raw cell value into typed values, leaving scalars alone.

    Lists are walked, since an array-valued cell holds one of these per entry.
    A string, number or boolean is returned unchanged -- wrapping those would
    make every ordinary cell awkward to use for no gain.

    >>> parse_value("just text")
    'just text'
    >>> person = parse_value(
    ...     {"@type": "Person", "name": "Alice", "email": "alice@example.com"}
    ... )
    >>> person.name, person.email
    ('Alice', 'alice@example.com')

    An array-valued cell holds one of these per entry:

    >>> images = parse_value([
    ...     {"@type": "ImageObject", "name": "a.png", "url": "https://x/a.png"},
    ...     {"@type": "ImageObject", "name": "b.png", "url": "https://x/b.png"},
    ... ])
    >>> [image.name for image in images]
    ['a.png', 'b.png']

    A type this version does not model is kept rather than refused:

    >>> value = parse_value({"@type": "SomethingNew", "detail": 42})
    >>> value.type, value["detail"]
    ('SomethingNew', 42)
    """
    if isinstance(value, list):
        return [parse_value(item) for item in value]
    if isinstance(value, dict):
        return _BY_LD_TYPE.get(value.get("@type"), UnknownValue).from_json(value)
    return value


def serialize(value):
    """
    Turn a Python value into something JSON can carry, for sending to the API.

    Every write goes through this. Without it a `datetime` in a cell reaches
    `json.dumps` and raises `TypeError: Object of type datetime is not JSON
    serializable`, which is a poor way to find out.

    >>> import datetime as dt
    >>> serialize(dt.date(2020, 1, 2))
    '2020-01-02'

    Money keeps its precision, because a float would not:

    >>> import decimal
    >>> serialize(decimal.Decimal("12.99"))
    '12.99'

    A typed value goes back exactly as the API sent it, so a value read and
    written again is unchanged:

    >>> image = parse_value({"@type": "ImageObject", "url": "https://x/a.png"})
    >>> serialize(image)
    {'@type': 'ImageObject', 'url': 'https://x/a.png'}

    Containers are walked:

    >>> serialize({"when": dt.date(2020, 1, 2), "how_many": [1, 2]})
    {'when': '2020-01-02', 'how_many': [1, 2]}
    """
    if isinstance(value, CodaValue):
        return value.to_json()
    if isinstance(value, (list, tuple, set)):
        return [serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items()}
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    return value


_FENCED = re.compile(r"\A```(.*)```\Z", re.DOTALL)


def unwrap_rich_text(text: str) -> str:
    """
    Strip the ``` fence that ``valueFormat=rich`` puts around unformatted text.

    Rich text comes back as Markdown, and a value with no formatting in it is
    returned fully escaped -- wrapped in triple backticks -- so that it round
    trips. That is rarely what you want to read.

    Best effort and not reversible: a cell whose Markdown genuinely *is* a fenced
    code block is indistinguishable from a wrapped plain string, so this is never
    applied automatically. Use it when you have decided you want text rather than
    Markdown.

    >>> unwrap_rich_text("```just some text```")
    'just some text'

    Formatted Markdown is left alone:

    >>> unwrap_rich_text("**bold** text")
    '**bold** text'

    And the ambiguous case, which is why nothing does this for you -- a cell
    whose Markdown really is a fenced code block looks exactly like a plain
    string that was wrapped:

    >>> unwrap_rich_text("```SUM(Total)```")
    'SUM(Total)'

    """
    if not isinstance(text, str):
        return text
    match = _FENCED.match(text)
    return match.group(1) if match else text


def is_rich(value) -> bool:
    """
    Whether this value came back as a structured object rather than a scalar.

    >>> is_rich("text")
    False
    >>> is_rich(parse_value({"@type": "Person", "name": "Alice"}))
    True
    """
    if isinstance(value, list):
        return any(is_rich(item) for item in value)
    return isinstance(value, (CodaValue, dict))


__all__: List[str] = [
    "CodaValue",
    "ImageValue",
    "PersonValue",
    "LinkValue",
    "MoneyValue",
    "RowValue",
    "UnknownValue",
    "VALUE_CLASSES",
    "parse_value",
    "serialize",
    "unwrap_rich_text",
    "is_rich",
]
