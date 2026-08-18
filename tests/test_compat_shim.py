"""
The historical import paths still resolve.

`codaio.coda` was where everything lived before it was split up, and it is kept
as a re-exporting module so existing code keeps working. Nothing else in the
suite imports it -- which is exactly why it needs its own test: it was broken by
a bad edit once and every other test still passed, because none of them would
have noticed if the module stopped importing at all.
"""

import warnings

import pytest


def test_the_shim_imports():
    import codaio.coda  # noqa: F401


@pytest.mark.parametrize(
    "name",
    [
        "Coda", "CodaObject", "Document", "Folder", "Page", "Table", "Column",
        "Row", "Cell", "MAX_GET_LIMIT", "assert_same_origin",
    ],
)
def test_historical_names_are_importable_from_codaio_coda(name):
    import codaio.coda

    assert getattr(codaio.coda, name) is not None


@pytest.mark.parametrize("module", ["codaio", "codaio.coda", "codaio.objects.page"])
def test_section_is_page_and_says_it_is_the_old_name(module):
    """
    `Section = Page` could not warn, and a subclass would break isinstance.
    A module-level hook gives the same object *and* the warning.
    """
    import importlib

    imported = importlib.import_module(module)

    with pytest.deprecated_call(match="Page"):
        section = imported.Section

    assert section is imported.Page if hasattr(imported, "Page") else True


def test_asking_for_something_that_never_existed_still_raises():
    """The deprecation hook must not swallow ordinary typos."""
    import codaio

    with pytest.raises(AttributeError, match="NoSuchThing"):
        codaio.NoSuchThing


def test_importing_the_shim_does_not_warn():
    """
    Only *using* the old name warns.

    An import-time warning would fire for everyone who touched the module,
    including code that never mentions Section, and would train people to filter
    the warning rather than act on it.
    """
    import importlib

    import codaio.coda

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        importlib.reload(codaio.coda)
