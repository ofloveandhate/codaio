"""
codaio against the API the service actually publishes.

Everything else in the suite is mocked, which proves codaio calls the URL codaio
meant to call -- self-consistency, not correctness. An endpoint can be wrong for
years and every test still pass; `/docs/{docId}/folders` did exactly that. This
is the only check that compares codaio to something it did not write itself.

Deliberately not part of the default run, and deliberately **not skipped** when
offline. It is opt-in, so being asked to run means it was meant; a skip would
let the API drift away with nothing to show for it. There is no CI job either:
nothing about codaio's build should depend on Coda's servers being reachable.

    python -m pytest -m conformance

What it does not check is as important as what it does. It never asserts that a
schema has no fields codaio is unaware of -- keeping unknown fields is the whole
design. It reports what codaio is *missing*, never what the API has added.
"""

import json

import pytest

import codaio
from codaio import values
from codaio._endpoints import (
    ACCESS_TYPES,
    ENDPOINTS,
    GRANTABLE_ACCESS_TYPES,
    PAGE_EXPORT_FORMATS,
    ROW_SORT_ORDERS,
    VALUE_FORMATS,
)
from codaio.objects.acl import PrincipalType
from codaio.objects.misc import CONTROL_TYPES
from codaio.objects.page import PAGE_LINE_STYLES

pytestmark = pytest.mark.conformance

SPEC_URL = "https://coda.io/apis/v1/openapi.json"


@pytest.fixture(scope="module")
def spec():
    """
    The published OpenAPI document.

    Fails rather than skips when it cannot be fetched. A conformance check that
    quietly does nothing is worse than none, because the green run says
    otherwise.
    """
    import requests

    try:
        response = requests.get(SPEC_URL, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        pytest.fail(
            f"could not fetch the published spec at {SPEC_URL}: {exc!r}. This "
            f"check is deliberately not skipped when offline -- a skip would let "
            f"the API drift away from codaio with nothing to show for it."
        )


def schemas(spec):
    return spec["components"]["schemas"]


def enum_of(spec, name):
    return set(schemas(spec)[name]["enum"])


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


def test_every_path_codaio_calls_exists(spec):
    """
    The check that would have caught `/docs/{docId}/folders`.

    A mocked test cannot: it asserts codaio built the URL codaio intended.
    """
    missing = [
        f"{name}: {endpoint.method} {endpoint.path}"
        for name, endpoint in sorted(ENDPOINTS.items())
        if endpoint.path not in spec["paths"]
    ]
    assert not missing, "codaio calls paths the API does not publish:\n  " + "\n  ".join(
        missing
    )


def test_every_method_codaio_uses_is_allowed(spec):
    wrong = []
    for name, endpoint in sorted(ENDPOINTS.items()):
        item = spec["paths"].get(endpoint.path)
        if item is None:
            continue  # reported by the test above
        if endpoint.method.lower() not in item:
            allowed = sorted(m.upper() for m in item if m in
                             ("get", "post", "put", "patch", "delete"))
            wrong.append(f"{name}: {endpoint.method} {endpoint.path} (allows {allowed})")
    assert not wrong, "codaio uses methods the API does not allow:\n  " + "\n  ".join(wrong)


def test_every_query_parameter_codaio_sends_is_accepted(spec):
    unknown = []
    for name, endpoint in sorted(ENDPOINTS.items()):
        item = spec["paths"].get(endpoint.path)
        if item is None:
            continue
        operation = item.get(endpoint.method.lower())
        if operation is None:
            continue

        accepted = set()
        for parameter in operation.get("parameters", []):
            if "$ref" in parameter:
                parameter = spec["components"]["parameters"][
                    parameter["$ref"].rsplit("/", 1)[-1]
                ]
            if parameter.get("in") == "query":
                accepted.add(parameter["name"])

        for sent in endpoint.params:
            if sent not in accepted:
                unknown.append(f"{name}: {sent} (accepts {sorted(accepted)})")

    assert not unknown, (
        "codaio sends query parameters the API does not document:\n  "
        + "\n  ".join(unknown)
    )


def test_success_codes_match(spec):
    """
    A 202 means accepted-and-queued rather than done, which is why writes return
    a `Mutation`. Getting this wrong would mean waiting on nothing, or not
    waiting when it matters.
    """
    wrong = []
    for name, endpoint in sorted(ENDPOINTS.items()):
        item = spec["paths"].get(endpoint.path)
        operation = (item or {}).get(endpoint.method.lower())
        if operation is None:
            continue
        published = {
            int(code) for code in operation.get("responses", {})
            if code.isdigit() and 200 <= int(code) < 300
        }
        if published and not set(endpoint.success) & published:
            wrong.append(f"{name}: codaio expects {endpoint.success}, API returns "
                         f"{sorted(published)}")
    assert not wrong, "success codes disagree:\n  " + "\n  ".join(wrong)


# --------------------------------------------------------------------------
# Enums codaio hardcodes
# --------------------------------------------------------------------------

ENUMS = {
    "ValueFormat": lambda: set(VALUE_FORMATS),
    "RowsSortBy": lambda: set(ROW_SORT_ORDERS),
    "PageContentOutputFormat": lambda: set(PAGE_EXPORT_FORMATS),
    "PageLineStyle": lambda: set(PAGE_LINE_STYLES),
    "ControlType": lambda: set(CONTROL_TYPES),
    "AccessType": lambda: set(ACCESS_TYPES),
    "AccessTypeNotNone": lambda: set(GRANTABLE_ACCESS_TYPES),
    "PrincipalType": lambda: {
        PrincipalType.EMAIL, PrincipalType.GROUP, PrincipalType.DOMAIN,
        PrincipalType.WORKSPACE, PrincipalType.ANYONE,
        PrincipalType.INTERNAL_ACCESS,
    },
    "LinkedDataType": lambda: {
        cls.LD_TYPE for cls in values.VALUE_CLASSES
    },
}


@pytest.mark.parametrize("schema_name", sorted(ENUMS), ids=sorted(ENUMS))
def test_hardcoded_enums_match_the_spec(spec, schema_name):
    """
    Where codaio writes out a set of values, it has to be the API's set.

    Both directions matter here, unlike with object fields: a value codaio does
    not know is one it will reject from a caller who is right, and a value the
    API dropped is one codaio still offers.
    """
    published = enum_of(spec, schema_name)
    ours = ENUMS[schema_name]()

    assert ours == published, (
        f"{schema_name}: codaio has {sorted(ours)}, the API publishes "
        f"{sorted(published)} (missing {sorted(published - ours)}, "
        f"stale {sorted(ours - published)})"
    )


# --------------------------------------------------------------------------
# Object fields
# --------------------------------------------------------------------------

#: codaio class -> the schema it models.
MODELLED = {
    codaio.Page: "Page",
    codaio.Table: "Table",
    codaio.Column: "ColumnDetail",
    codaio.Row: "RowDetail",
    codaio.Folder: "Folder",
    codaio.Formula: "Formula",
    codaio.Control: "Control",
    codaio.ImageValue: "ImageUrlValue",
    codaio.PersonValue: "PersonValue",
    codaio.LinkValue: "UrlValue",
    codaio.MoneyValue: "CurrencyValue",
    codaio.RowValue: "RowValue",
    codaio.Permission: "Permission",
    codaio.AclMetadata: "AclMetadata",
    codaio.AclSettings: "AclSettings",
}


def snake(name):
    import inflection

    return inflection.underscore(name)


#: JSON-LD plumbing rather than data. `@type` is what selects the class in the
#: first place, and both are kept verbatim on `.raw` and sent back unchanged, so
#: an attribute for either would only be a second copy.
LINKED_DATA_KEYS = {"@context", "@type"}


def declared_fields(cls):
    """
    Names a class exposes: attrs fields and properties alike.

    Properties count. `Formula.value` is one, because the payload is kept raw and
    typed on access -- a check that only looked at attrs fields would call it
    missing when it is the whole point of the class.
    """
    import attr

    names = {a.name.lstrip("_") for a in attr.fields(cls)}
    names |= {
        name for name, member in vars(cls).items()
        if isinstance(member, property)
    }
    for base in cls.__mro__:
        names |= {
            name for name, member in vars(base).items()
            if isinstance(member, property)
        }
    return names


def required_properties(spec, schema_name):
    """Required property names, following allOf so composed schemas work."""
    schema = schemas(spec)[schema_name]
    required, properties = set(schema.get("required", [])), {}
    for part in schema.get("allOf", []):
        if "$ref" in part:
            inner = schemas(spec)[part["$ref"].rsplit("/", 1)[-1]]
        else:
            inner = part
        required |= set(inner.get("required", []))
        properties.update(inner.get("properties", {}))
    properties.update(schema.get("properties", {}))
    return required, properties


@pytest.mark.parametrize(
    "cls", sorted(MODELLED, key=lambda c: c.__name__), ids=lambda c: c.__name__
)
def test_required_fields_are_modelled(spec, cls):
    """
    Every field the API guarantees has somewhere to go.

    Note the direction. This never complains that the API has a field codaio has
    not modelled -- unknown fields are kept and reachable, which is the whole
    point of the tolerant object model. It complains when codaio would drop
    something the API says is always there.
    """
    required, _ = required_properties(spec, MODELLED[cls])
    declared = declared_fields(cls)
    missing = sorted(
        name for name in required - LINKED_DATA_KEYS
        if snake(name) not in declared
    )

    assert not missing, (
        f"{cls.__name__} does not model required fields of {MODELLED[cls]}: "
        f"{missing}. They are still reachable via .field(), but they are "
        f"guaranteed to be present and deserve a real attribute."
    )


def test_the_spec_still_says_tables_cannot_be_created(spec):
    """
    The constraint the whole scope of this library rests on.

    If Coda ever adds table or column creation, this fails and the scope should
    be revisited -- which is a much better way to find out than noticing years
    later.
    """
    tables = spec["paths"].get("/docs/{docId}/tables", {})
    columns = spec["paths"].get("/docs/{docId}/tables/{tableIdOrName}/columns", {})

    writes = {m for m in ("post", "put", "patch", "delete")
              if m in tables or m in columns}

    assert not writes, (
        f"the API now allows {sorted(writes)} on tables or columns. codaio's "
        f"scope was set on the basis that it did not; revisit it."
    )


def test_report_what_codaio_does_not_cover(spec, record_property):
    """
    Not an assertion -- a report.

    Prints the endpoints the API publishes that codaio has no method for, so the
    gap is a decision rather than an oversight. Paths under deliberately
    out-of-scope areas are left out.
    """
    out_of_scope = ("/packs", "/go/", "/analytics", "/workspaces", "/organizations",
                    "/domains", "/categories", "/hooks", "/publish")
    covered = {(e.method.upper(), e.path) for e in ENDPOINTS.values()}

    uncovered = sorted(
        f"{method.upper():6} {path}"
        for path, item in spec["paths"].items()
        if not any(path.startswith(prefix) or prefix in path for prefix in out_of_scope)
        for method in item
        if method in ("get", "post", "put", "patch", "delete")
        and (method.upper(), path) not in covered
    )
    record_property("uncovered_endpoints", json.dumps(uncovered))
    print(f"\n{len(uncovered)} in-scope endpoints codaio does not implement:")
    for line in uncovered:
        print("   ", line)
