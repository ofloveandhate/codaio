"""
The typed reading of cell values, and the serialisation that sends them back.

Under `valueFormat=rich` the API returns JSON-LD objects rather than flattened
strings. Before this, asking whether a cell held an image meant checking
`value["@type"] == "ImageObject"` by hand -- and first checking whether the cell
was a dict or a list of them, since an array-valued cell is both shapes at once.
"""

import datetime as dt
import decimal

import pytest

from codaio import err
from codaio.values import (
    CodaValue,
    ImageValue,
    LinkValue,
    MoneyValue,
    PersonValue,
    RowValue,
    UnknownValue,
    is_rich,
    parse_value,
    serialize,
    unwrap_rich_text,
)

IMAGE = {"@context": "http://schema.org", "@type": "ImageObject",
         "name": "Dogs Playing Poker", "url": "https://codahosted.io/dogs.jpg",
         "width": 640, "height": 480, "status": "live"}
PERSON = {"@context": "http://schema.org", "@type": "Person",
          "name": "Alice Atkins", "email": "alice@atkins.com"}
LINK = {"@context": "http://schema.org", "@type": "WebPage",
        "name": "Click me", "url": "https://example.com"}
MONEY = {"@context": "http://schema.org", "@type": "MonetaryAmount",
         "currency": "USD", "amount": "12.99"}
ROW = {"@context": "http://schema.org", "@type": "StructuredValue",
       "additionalType": "row", "name": "Apple", "rowId": "i-tuVwxYz",
       "tableId": "grid-pqRst-U", "url": "https://coda.io/d/x#t/_rui-tuVwxYz",
       "tableUrl": "https://coda.io/d/x#t"}


class TestDispatch:
    @pytest.mark.parametrize(
        "payload,expected",
        [(IMAGE, ImageValue), (PERSON, PersonValue), (LINK, LinkValue),
         (MONEY, MoneyValue), (ROW, RowValue)],
        ids=["image", "person", "link", "money", "row"],
    )
    def test_each_documented_type_gets_its_class(self, payload, expected):
        assert isinstance(parse_value(payload), expected)

    def test_an_unmodelled_type_is_not_an_error(self):
        """The API may add value types; refusing to build one is not an option."""
        value = parse_value({"@type": "SomethingNewEntirely", "field": 1})

        assert isinstance(value, UnknownValue)
        assert value.type == "SomethingNewEntirely"
        assert value["field"] == 1

    def test_an_object_with_no_type_is_still_kept(self):
        value = parse_value({"no": "type here"})

        assert isinstance(value, UnknownValue)
        assert value.raw == {"no": "type here"}

    @pytest.mark.parametrize("scalar", ["text", 3, 4.5, True, None])
    def test_scalars_pass_through_untouched(self, scalar):
        """Wrapping ordinary cells would make every one of them awkward."""
        assert parse_value(scalar) is scalar

    def test_array_valued_cells_are_walked(self):
        values = parse_value([IMAGE, IMAGE])

        assert len(values) == 2
        assert all(isinstance(v, ImageValue) for v in values)

    def test_a_mixed_array_keeps_each_kind(self):
        values = parse_value([IMAGE, "plain", MONEY])

        assert isinstance(values[0], ImageValue)
        assert values[1] == "plain"
        assert isinstance(values[2], MoneyValue)


class TestFields:
    def test_image(self):
        image = parse_value(IMAGE)
        assert (image.name, image.url) == ("Dogs Playing Poker",
                                           "https://codahosted.io/dogs.jpg")
        assert (image.width, image.height, image.status) == (640, 480, "live")

    def test_person(self):
        person = parse_value(PERSON)
        assert (person.name, person.email) == ("Alice Atkins", "alice@atkins.com")

    def test_link(self):
        link = parse_value(LINK)
        assert (link.name, link.url) == ("Click me", "https://example.com")

    def test_row_reference(self):
        row = parse_value(ROW)
        assert (row.name, row.row_id, row.table_id) == (
            "Apple", "i-tuVwxYz", "grid-pqRst-U")
        assert row.additional_type == "row"


class TestMoneyIsExact:
    def test_amount_is_a_decimal_not_a_float(self):
        """Money through a float loses cents for no benefit."""
        assert parse_value(MONEY).amount == decimal.Decimal("12.99")
        assert isinstance(parse_value(MONEY).amount, decimal.Decimal)

    def test_a_numeric_amount_is_also_exact(self):
        """The API sends the amount as a number or a string, interchangeably."""
        value = parse_value({**MONEY, "amount": 0.1})

        assert value.amount == decimal.Decimal("0.1")

    def test_an_unparseable_amount_does_not_raise(self):
        assert parse_value({**MONEY, "amount": "not a number"}).amount is None


class TestRawIsPreserved:
    @pytest.mark.parametrize("payload", [IMAGE, PERSON, LINK, MONEY, ROW],
                             ids=["image", "person", "link", "money", "row"])
    def test_round_trips_unchanged(self, payload):
        """A stored copy must be able to send back exactly what it was given."""
        assert parse_value(payload).to_json() == payload

    def test_fields_the_class_does_not_model_are_still_reachable(self):
        value = parse_value({**IMAGE, "somethingNew": "kept"})

        assert value["somethingNew"] == "kept"
        assert value.get("nothing") is None


class TestEquality:
    def test_same_payload_same_value(self):
        assert parse_value(IMAGE) == parse_value(dict(IMAGE))

    def test_different_payload_differs(self):
        assert parse_value(IMAGE) != parse_value({**IMAGE, "name": "Other"})

    def test_different_types_never_match(self):
        assert parse_value(LINK) != parse_value({**LINK, "@type": "ImageObject"})

    def test_values_are_hashable(self):
        assert len({parse_value(IMAGE), parse_value(dict(IMAGE))}) == 1


class TestSerialize:
    def test_a_datetime_no_longer_breaks_a_write(self):
        """It used to reach json.dumps and raise TypeError there."""
        assert serialize(dt.datetime(2020, 1, 2, 3, 4)) == "2020-01-02T03:04:00"

    def test_dates_and_times_too(self):
        assert serialize(dt.date(2020, 1, 2)) == "2020-01-02"
        assert serialize(dt.time(3, 4)) == "03:04:00"

    def test_decimals_keep_their_precision(self):
        assert serialize(decimal.Decimal("12.99")) == "12.99"

    def test_a_typed_value_goes_back_as_the_api_sent_it(self):
        assert serialize(parse_value(IMAGE)) == IMAGE

    def test_containers_are_walked(self):
        out = serialize({"when": dt.date(2020, 1, 2), "what": [parse_value(MONEY)]})

        assert out == {"when": "2020-01-02", "what": [MONEY]}

    @pytest.mark.parametrize("scalar", ["text", 3, 4.5, True, None])
    def test_scalars_are_untouched(self, scalar):
        assert serialize(scalar) is scalar

    def test_parse_and_serialize_round_trip(self):
        assert serialize(parse_value([IMAGE, MONEY])) == [IMAGE, MONEY]


class TestRichText:
    def test_the_fence_can_be_stripped(self):
        assert unwrap_rich_text("```just some text```") == "just some text"

    def test_formatted_markdown_is_left_alone(self):
        assert unwrap_rich_text("**bold** text") == "**bold** text"

    def test_multiline_content_is_handled(self):
        assert unwrap_rich_text("```line one\nline two```") == "line one\nline two"

    def test_a_genuine_code_block_is_the_ambiguous_case(self):
        """
        Documented, not fixed: a cell whose Markdown really is a fenced code
        block cannot be told apart from a plain string that was wrapped. That is
        why nothing unwraps automatically.
        """
        assert unwrap_rich_text("```def f():\n    pass```") == "def f():\n    pass"

    def test_non_strings_are_returned_as_they_are(self):
        assert unwrap_rich_text(42) == 42


class TestIsRich:
    def test_scalars_are_not(self):
        assert is_rich("text") is False

    def test_structured_values_are(self):
        assert is_rich(parse_value(IMAGE)) is True

    def test_an_array_containing_one_is(self):
        assert is_rich([parse_value(IMAGE)]) is True


class TestRowReferenceResolution:
    def test_a_reference_missing_its_ids_says_so(self):
        value = parse_value({"@type": "StructuredValue", "name": "Dangling"})

        with pytest.raises(err.RowNotFound, match="Dangling"):
            value.resolve(document=None)


def test_every_value_class_shares_the_base():
    for payload in (IMAGE, PERSON, LINK, MONEY, ROW):
        assert isinstance(parse_value(payload), CodaValue)
