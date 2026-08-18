from importlib.metadata import PackageNotFoundError, version as _version

from .credentials import (  # noqa
    get_api_key,
    get_api_key_with_source,
    keyring_status,
)
from .coda import Cell, Coda, Column, Document, Row, Table  # noqa

try:
    __version__ = _version("codaio")
except PackageNotFoundError:  # not installed, e.g. running straight from a source checkout
    __version__ = "0.0.0"
