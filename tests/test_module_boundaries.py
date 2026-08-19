"""
Which module may import which.

`credentials` has always been deliberately standalone -- it resolves the API
token and nothing else, so other tools can depend on that logic without dragging
in a client, and so it cannot form an import cycle. The same reasoning now
applies to `http` and `values`, which sit below the client rather than beside it.

Written as a test because a convention stated only in a docstring gets broken by
the first person in a hurry, and the failure -- a circular import -- surfaces far
from its cause.
"""

import ast
import pathlib

import pytest

CODAIO = pathlib.Path(__file__).parent.parent / "codaio"

# module -> what it is allowed to import from within codaio, at runtime
ALLOWED = {
    "credentials.py": {"codaio.err"},
    "err.py": set(),
    "http.py": {"codaio.err"},
    "values.py": {"codaio.err", "codaio.http"},
    "_endpoints.py": {"codaio.err", "codaio.http"},
}


def runtime_imports(path: pathlib.Path):
    """codaio modules imported at runtime, ignoring `if TYPE_CHECKING` blocks."""
    tree = ast.parse(path.read_text())

    guarded = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            named = (
                (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING")
                or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")
            )
            if named:
                for inner in ast.walk(node):
                    guarded.add(id(inner))

    found = set()
    for node in ast.walk(tree):
        if id(node) in guarded:
            continue
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("codaio"):
            if node.module == "codaio":
                # `from codaio import err` -- the names are submodules
                found.update(f"codaio.{alias.name}" for alias in node.names)
            else:
                found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(
                alias.name for alias in node.names if alias.name.startswith("codaio")
            )
    return found


@pytest.mark.parametrize("name", sorted(ALLOWED), ids=sorted(ALLOWED))
def test_module_imports_stay_within_their_layer(name):
    imports = runtime_imports(CODAIO / name)

    assert imports <= ALLOWED[name], (
        f"codaio/{name} imports {sorted(imports - ALLOWED[name])}, which is above "
        f"its layer. Allowed: {sorted(ALLOWED[name]) or 'nothing from codaio'}."
    )


def test_values_does_not_reach_the_client_or_the_object_model():
    """
    Stated separately because it is the one that will be tempting to break.

    `RowValue.resolve` needs a `Document` -- it takes one as an argument rather
    than importing one, which is what keeps this module usable on its own.
    """
    imports = runtime_imports(CODAIO / "values.py")

    assert not any(
        module.startswith("codaio.objects") or module == "codaio.client"
        for module in imports
    )


#: `document.py` is the one object-model module that legitimately constructs a
#: client: `Document.from_credentials` exists precisely to build one for you.
#: That is not a cycle, because the client never imports the object model back --
#: which is the property the next test actually guards.
MAY_IMPORT_THE_CLIENT = {"document.py"}


def test_only_document_reaches_the_client_at_runtime():
    """
    Everywhere else, the client is a type annotation and nothing more.

    A runtime import of the client from, say, `table.py` would work today and
    become a cycle the moment the client needed anything from the object model.
    """
    for path in sorted((CODAIO / "objects").glob("*.py")):
        if path.name in MAY_IMPORT_THE_CLIENT:
            continue
        imports = runtime_imports(path)
        assert "codaio.client" not in imports, (
            f"codaio/objects/{path.name} imports the client at runtime; a "
            f"TYPE_CHECKING import keeps the annotation without the risk."
        )


def test_the_client_never_imports_the_object_model():
    """The direction that matters: this is what makes the layering acyclic."""
    imports = runtime_imports(CODAIO / "client.py")

    assert not any(module.startswith("codaio.objects") for module in imports), (
        "codaio/client.py imports the object model, which closes the loop with "
        "codaio/objects/document.py and makes the import order fragile."
    )
