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
from .objects.page import (  # noqa
    CanvasContent,
    ContentItem,
    EmbedContent,
    Page,
    PageExport,
    PageTree,
    SyncPageContent,
)
from .objects.mutation import Mutation, MutationGroup  # noqa
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


def __getattr__(name):
    """
    `Section` is what a page used to be called here. It is `Page`.

    Provided through the module hook rather than as an assignment so that using
    it says so, while `Section is Page` keeps isinstance checks working.
    """
    if name == "Section":
        import warnings

        warnings.warn(
            "codaio.Section is deprecated; use codaio.Page. Section is Page, so "
            "isinstance checks keep working.",
            DeprecationWarning,
            stacklevel=2,
        )
        return Page
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
