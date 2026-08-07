from importlib.metadata import PackageNotFoundError, version as _version

from .coda import Cell, Coda, Column, Document, Row, Table  # noqa

try:
    __version__ = _version("codaio")
except PackageNotFoundError:  # not installed, e.g. running straight from a source checkout
    __version__ = "0.0.0"
