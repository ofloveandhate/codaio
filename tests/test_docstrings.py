"""
Every public thing says how to use it, and the examples are run.

Two rules, both enforced rather than hoped for.

Public callables carry a docstring. A library whose reader has to open the
source to find out what a method does has not really got an interface.

Examples are executable where they can be. A `>>>` example is collected by
pytest (see `addopts` in pyproject.toml) and fails when it stops being true;
anything needing a live client is written as a `.. code-block::` instead, which
doctest ignores and Sphinx still renders. The point is that no example in this
library is asserted without being checked.
"""

import ast
import inspect
import pathlib

import pytest

import codaio
from codaio.objects.mutation import Mutation, MutationGroup
from codaio.objects.page import ContentItem, PageExport, PageTree

CODAIO = pathlib.Path(codaio.__file__).parent

PUBLIC_CLASSES = [
    codaio.Coda, codaio.Document, codaio.Folder, codaio.Table, codaio.Column,
    codaio.Row, codaio.Cell, codaio.Page, PageTree, PageExport, ContentItem,
    Mutation, MutationGroup, codaio.ImageValue, codaio.PersonValue,
    codaio.LinkValue, codaio.MoneyValue, codaio.RowValue, codaio.UnknownValue,
]


def public_callables(cls):
    for name, member in vars(cls).items():
        if name.startswith("_"):
            continue
        target = member.fget if isinstance(member, property) else member
        if callable(target):
            yield name, target


@pytest.mark.parametrize("cls", PUBLIC_CLASSES, ids=lambda c: c.__name__)
def test_public_callables_are_documented(cls):
    undocumented = [
        name for name, target in public_callables(cls)
        if not (inspect.getdoc(target) or "").strip()
    ]
    assert not undocumented, (
        f"{cls.__name__} has public members with no docstring: {undocumented}"
    )


@pytest.mark.parametrize("cls", PUBLIC_CLASSES, ids=lambda c: c.__name__)
def test_public_classes_are_documented(cls):
    assert (inspect.getdoc(cls) or "").strip(), f"{cls.__name__} has no docstring"


def unmarked_example_lines(doc):
    """
    Indented code in a docstring that is neither a doctest nor a marked block.

    Two forms are legitimate and rendered by Sphinx: an explicit
    `.. code-block::` directive, and an RST literal block introduced by a line
    ending in `::`. Anything else that looks like a call but sits outside both
    is code nobody checks and nobody renders as code.
    """
    lines = doc.splitlines()
    in_marked_block = False
    offenders = []

    for index, line in enumerate(lines):
        stripped = line.strip()
        indented = line.startswith("    ")

        if not stripped:
            continue
        if not indented:
            # back out to the margin ends any block
            in_marked_block = stripped.endswith("::") or ".. code-block::" in stripped
            continue
        if ".. code-block::" in stripped or stripped.endswith("::"):
            in_marked_block = True
            continue
        if in_marked_block or stripped.startswith((">>>", "...", "#", "*", "-", ":")):
            continue

        looks_like_a_call = (
            stripped.endswith((")", "]"))
            and "(" in stripped
            and " " not in stripped.split("(")[0]
        )
        if looks_like_a_call:
            offenders.append(stripped)

    return offenders


def test_no_example_is_shown_without_being_run_or_marked():
    """
    A `>>>` line is collected and checked. Prose that shows code without one has
    to be a marked block, so it is obvious which examples are verified.

    This catches the tempting middle ground -- indented code in a docstring that
    looks executable, is never executed, and quietly stops being true.
    """
    offenders = []
    for path in sorted(CODAIO.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                continue
            doc = ast.get_docstring(node)
            if doc:
                offenders.extend(
                    f"{path.name}: {line}" for line in unmarked_example_lines(doc)
                )

    assert not offenders, (
        "these look like examples but are neither doctests nor marked blocks:\n  "
        + "\n  ".join(offenders)
    )


def test_the_guard_notices_an_unmarked_example():
    """The detector above is worth nothing if it cannot fail."""
    assert unmarked_example_lines("Prose.\n\n    table.rows()\n") == ["table.rows()"]
    assert unmarked_example_lines("Prose::\n\n    table.rows()\n") == []
    assert unmarked_example_lines(
        "Prose.\n\n.. code-block:: python\n\n    table.rows()\n"
    ) == []
    assert unmarked_example_lines("Prose.\n\n    >>> table.rows()\n") == []


def test_the_doctests_actually_run():
    """
    A guard on the configuration rather than the code.

    Doctests are only checked because `--doctest-modules` is in pyproject.toml.
    If that were dropped, every example in the library would silently stop being
    verified and nothing else would fail.
    """
    import tomllib

    config = tomllib.loads(
        (CODAIO.parent / "pyproject.toml").read_text()
    )["tool"]["pytest"]["ini_options"]

    assert "--doctest-modules" in config["addopts"]
    assert "codaio" in config["testpaths"]
