"""
The object model's tolerance of an API that keeps moving, and its identity.

Two properties are pinned here.

Unknown fields must never be fatal. Objects used to be built by splatting every
JSON key into the constructor, so a field the API had added since the class was
written raised TypeError and the whole call failed. `Document.list_sections()`
was broken this way against any real doc, while the suite stayed green because
the fixtures had been written by hand in the shape of a much older API.

Identity must work on real data. Every class asked attrs to build `__hash__`
from all its fields, so hashing a table whose `filter` is a dict raised
`TypeError: unhashable type` -- the classes advertised a hashability they did
not have.
"""

import json
import warnings
from pathlib import Path

import pytest

from codaio import err
from codaio.objects import base
from codaio.objects.base import CodaObject, PageReference, Reference, TableReference
from codaio.objects.page import Section
from codaio.objects.table import Column, Row, Table

DATA = Path(__file__).parent / "data"


def fixture(name):
    return json.load(open(DATA / name))


# A page as the API actually returns one today. Every key beyond the five the
# class used to know about was a TypeError before.
REALISTIC_PAGE = {
    "id": "canvas-IjkLmnO",
    "type": "page",
    "href": "https://coda.io/apis/v1/docs/AbCDeFGH/pages/canvas-IjkLmnO",
    "browserLink": "https://coda.io/d/_dAbCDeFGH/Launch-Status_sumnO",
    "name": "Launch Status",
    "subtitle": "See the status of launch-related tasks",
    "icon": {"name": "rocket", "type": "icon", "browserLink": "https://coda.io/r.png"},
    "image": {"type": "image", "browserLink": "https://x/c.png", "width": 800,
              "height": 600},
    "contentType": "canvas",
    "isHidden": False,
    "isEffectivelyHidden": False,
    "parent": {"id": "canvas-parent", "type": "page", "name": "Root",
               "href": "h", "browserLink": "b"},
    "children": [{"id": "canvas-child", "type": "page", "name": "Sub",
                  "href": "h", "browserLink": "b"}],
    "authors": [{"name": "Foo Bar", "email": "foobar@example.com"}],
    "createdAt": "2020-01-01T00:00:00.000Z",
    "updatedAt": "2020-01-02T00:00:00.000Z",
}


class TestUnknownFieldsAreKept:
    def test_a_field_codaio_does_not_model_does_not_raise(self):
        page = Section.from_json({**REALISTIC_PAGE, "wholeNewThing": 42})

        assert page.id == "canvas-IjkLmnO"
        assert page.unknown_fields == {"wholeNewThing": 42}

    def test_an_unknown_field_is_still_readable(self):
        page = Section.from_json({**REALISTIC_PAGE, "wholeNewThing": 42})

        assert page.field("wholeNewThing") == 42

    def test_field_accepts_either_spelling(self):
        page = Section.from_json(REALISTIC_PAGE)

        assert page.field("isHidden") is False
        assert page.field("is_hidden") is False

    def test_field_returns_the_default_when_absent(self):
        assert Section.from_json(REALISTIC_PAGE).field("nope", "fallback") == "fallback"

    def test_raw_is_the_payload_unchanged(self):
        page = Section.from_json(REALISTIC_PAGE)

        assert page.raw == REALISTIC_PAGE
        assert page.to_json() == REALISTIC_PAGE

    def test_raw_is_a_copy_not_the_caller_s_dict(self):
        payload = dict(REALISTIC_PAGE)
        page = Section.from_json(payload)
        payload["name"] = "mutated afterwards"

        assert page.raw["name"] == "Launch Status"

    def test_back_references_stay_out_of_raw(self):
        """`.raw` is what the API sent, so a Table object must not appear in it."""
        table = Table.from_json(fixture("get_table.json"))
        column = Column.from_json(fixture("get_column.json"), table=table)

        assert column.table is table
        assert "table" not in column.raw
        assert all(isinstance(value, (str, int, float, bool, list, dict, type(None)))
                   for value in column.raw.values())

    def test_unknown_fields_is_empty_when_fully_modelled(self):
        minimal = {"id": "canvas-1", "type": "page", "href": "h", "name": "n"}

        assert Section.from_json(minimal).unknown_fields == {}


class TestWarningIsOptIn:
    def test_silent_by_default(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            Section.from_json({**REALISTIC_PAGE, "wholeNewThing": 42})

    def test_warns_when_switched_on(self, monkeypatch):
        monkeypatch.setattr(base, "WARN_ON_UNKNOWN_FIELDS", True)

        with pytest.warns(err.UnknownFieldWarning, match="wholeNewThing"):
            Section.from_json({**REALISTIC_PAGE, "wholeNewThing": 42})


class TestRestoredFields:
    """
    `parent` and `format` were the only two keys the old builder deleted, and
    between them they are the page hierarchy and the column type -- the two
    things you most need and could not get.
    """

    def test_page_parent_and_children_become_references(self):
        page = Section.from_json(REALISTIC_PAGE)

        assert isinstance(page.parent, PageReference)
        assert page.parent.name == "Root"
        assert [child.id for child in page.children] == ["canvas-child"]
        assert isinstance(page.children[0], PageReference)

    def test_children_is_a_tuple_even_when_absent(self):
        page = Section.from_json({"id": "canvas-1", "type": "page"})

        assert page.children == ()

    def test_column_format_carries_the_column_type(self):
        column = Column.from_json(fixture("get_column.json"))

        assert column.format.type == "text"
        assert column.format.is_array is False

    def test_column_format_keeps_options_codaio_has_no_field_for(self):
        column = Column.from_json({
            "id": "c-1", "type": "column", "name": "Cost",
            "format": {"type": "currency", "isArray": False,
                       "currencyCode": "USD", "precision": 2},
        })

        assert column.format.type == "currency"
        assert column.format["currencyCode"] == "USD"
        assert column.format.get("precision") == 2
        assert column.format.get("nothing") is None

    def test_column_parent_becomes_a_table_reference(self):
        column = Column.from_json(fixture("get_column.json"))

        assert isinstance(column.parent, TableReference)
        assert column.parent.id == "table_id"


class TestIdentity:
    def test_a_filtered_table_is_hashable(self):
        """`filter` is a dict, which used to make the generated __hash__ raise."""
        table = Table.from_json(fixture("get_table.json"))

        assert isinstance(table.filter, dict)
        assert hash(table)

    def test_a_row_with_rich_values_is_hashable(self):
        """Rich values are dicts -- and rich is the format worth reading."""
        row = Row.from_json({
            "id": "i-1", "type": "row", "name": "n", "index": 0,
            "values": {"c-1": {"@type": "ImageObject", "url": "https://x/y.png"}},
        })

        assert hash(row)

    def test_same_id_same_object(self):
        first = Section.from_json(REALISTIC_PAGE)
        second = Section.from_json({**REALISTIC_PAGE, "name": "Renamed since"})

        assert first == second
        assert hash(first) == hash(second)

    def test_different_ids_are_different(self):
        first = Section.from_json(REALISTIC_PAGE)
        second = Section.from_json({**REALISTIC_PAGE, "id": "canvas-other"})

        assert first != second

    def test_different_classes_never_compare_equal(self):
        """A page and a table sharing an id are not the same thing."""
        page = Section.from_json({"id": "shared", "type": "page"})
        table = Table.from_json({"id": "shared", "type": "table"})

        assert page != table

    def test_objects_can_be_used_in_a_set(self):
        pages = {Section.from_json(REALISTIC_PAGE),
                 Section.from_json({**REALISTIC_PAGE, "subtitle": "changed"})}

        assert len(pages) == 1


class TestReferences:
    def test_type_selects_the_reference_class(self):
        assert isinstance(Reference.from_json({"id": "p", "type": "page"}),
                          PageReference)
        assert isinstance(Reference.from_json({"id": "t", "type": "table"}),
                          TableReference)

    def test_an_unrecognised_type_still_builds(self):
        reference = Reference.from_json({"id": "x", "type": "somethingNew"})

        assert type(reference) is Reference
        assert reference.id == "x"

    def test_a_reference_keeps_its_payload(self):
        payload = {"id": "p", "type": "page", "name": "N", "extra": "kept"}

        assert Reference.from_json(payload).raw == payload

    def test_resolving_an_unknown_reference_says_what_to_do_instead(self):
        reference = Reference.from_json({"id": "x", "type": "somethingNew"})

        with pytest.raises(NotImplementedError, match="'x'"):
            reference.resolve()


class TestSharedMutableDefaults:
    def test_tables_do_not_share_one_list(self):
        """`attr.ib(default=[])` handed every instance the same list object."""
        first = Table.from_json({"id": "t1", "type": "table", "name": "A"})
        second = Table.from_json({"id": "t2", "type": "table", "name": "B"})

        first.sorts.append("mutated")

        assert second.sorts == []

    def test_column_storage_is_not_shared_either(self):
        first = Table.from_json({"id": "t1", "type": "table", "name": "A"})
        second = Table.from_json({"id": "t2", "type": "table", "name": "B"})

        first.columns_storage.append("mutated")

        assert second.columns_storage == []


def test_every_object_class_tolerates_a_novel_field():
    """A blanket check, so a class added later cannot quietly be strict."""
    payloads = {
        Section: {"id": "canvas-1", "type": "page", "name": "P"},
        Table: {"id": "grid-1", "type": "table", "name": "T"},
        Column: {"id": "c-1", "type": "column", "name": "C"},
        Row: {"id": "i-1", "type": "row", "name": "R", "index": 0, "values": {}},
    }
    for cls, payload in payloads.items():
        built = cls.from_json({**payload, "aFieldFromTheFuture": ["anything"]})
        assert built.unknown_fields == {"aFieldFromTheFuture": ["anything"]}
        assert issubclass(cls, CodaObject)
