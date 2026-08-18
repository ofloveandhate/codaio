from importlib.metadata import PackageNotFoundError, version as _version

from .credentials import (  # noqa
    get_api_key,
    get_api_key_with_source,
    keyring_status,
)
from .client import Coda  # noqa
from .objects.base import (  # noqa
    CodaObject,
    ColumnFormat,
    ColumnReference,
    FolderReference,
    PageReference,
    Reference,
    TableReference,
    WorkspaceReference,
)
from .objects.document import Document, Folder  # noqa
from .objects.page import Section  # noqa
from .objects.table import Cell, Column, Row, Table  # noqa
from .values import (  # noqa
    CodaValue,
    ImageValue,
    LinkValue,
    MoneyValue,
    PersonValue,
    RowValue,
    UnknownValue,
    parse_value,
    serialize,
    unwrap_rich_text,
)

try:
    __version__ = _version("codaio")
except PackageNotFoundError:  # not installed, e.g. running straight from a source checkout
    __version__ = "0.0.0"
