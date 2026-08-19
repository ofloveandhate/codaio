"""
The doc itself, and the parts of it that hold a value without being a table.
"""

import json as _json

import pytest

from codaio import Control, Document, Formula, MoneyValue, err
from tests.conftest import BASE_URL

DOC = BASE_URL + "/docs/doc_id"


class TestUpdatingADoc:
    def test_rename(self, main_document, mocked_responses):
        mocked_responses.add("PATCH", DOC, json={"id": "doc_id"})
        mocked_responses.add(
            "GET", DOC + "/",
            json={"id": "doc_id", "type": "doc", "name": "Renamed",
                  "browserLink": "b", "owner": "o",
                  "createdAt": "2020-01-01T00:00:00.000Z",
                  "updatedAt": "2020-01-01T00:00:00.000Z"})

        main_document.update(title="Renamed")

        patch = [c for c in mocked_responses.calls if c.request.method == "PATCH"][0]
        assert _json.loads(patch.request.body) == {"title": "Renamed"}
        assert main_document.name == "Renamed"

    def test_changing_nothing_is_refused(self, coda, mocked_responses):
        with pytest.raises(err.InvalidQuery, match="nothing to change"):
            coda.update_doc("d1")

        assert not mocked_responses.calls


class TestCopyingADoc:
    def test_source_doc_and_folder_are_sent(self, coda, mocked_responses):
        """
        Copying is the only way to get a doc with tables in it, since tables
        cannot be created through the API.
        """
        mocked_responses.add("POST", BASE_URL + "/docs", status=201, json={"id": "new"})
        coda.create_doc("This week", source_doc="AbCDeFGH", folder_id="fl-1")

        assert _json.loads(mocked_responses.calls[-1].request.body) == {
            "title": "This week", "sourceDoc": "AbCDeFGH", "folderId": "fl-1"}


class TestBuildingWithoutAFetch:
    def test_from_json_makes_no_request(self, coda, mocked_responses):
        before = len(mocked_responses.calls)
        doc = Document.from_json(
            {"id": "d1", "type": "doc", "name": "Notes",
             "createdAt": "2020-01-01T00:00:00.000Z"},
            coda=coda,
        )

        assert doc.name == "Notes"
        assert doc.created_at.year == 2020
        assert len(mocked_responses.calls) == before

    def test_the_ordinary_constructor_still_fetches(self, coda, mock_json_response,
                                                    mocked_responses):
        mock_json_response(DOC + "/", "get_doc.json")
        before = len(mocked_responses.calls)

        Document("doc_id", coda=coda)

        assert len(mocked_responses.calls) == before + 1


class TestFormulas:
    def test_they_are_typed_and_their_values_are_too(self, main_document,
                                                     mocked_responses):
        mocked_responses.add(
            "GET", DOC + "/formulas",
            json={"items": [
                {"id": "f-1", "type": "formula", "name": "Total",
                 "value": {"@type": "MonetaryAmount", "currency": "GBP",
                           "amount": "42.50"}},
            ]})
        formulas = main_document.list_formulas()

        assert isinstance(formulas[0], Formula)
        assert formulas[0].name == "Total"
        assert isinstance(formulas[0].value, MoneyValue)
        assert str(formulas[0].value.amount) == "42.50"

    def test_a_scalar_value_stays_scalar(self, main_document, mocked_responses):
        mocked_responses.add(
            "GET", DOC + "/formulas/f-1",
            json={"id": "f-1", "type": "formula", "name": "Count", "value": 7})

        assert main_document.get_formula("f-1").value == 7

    def test_the_payload_is_kept_whole(self, main_document, mocked_responses):
        """`value` is a property, so it must not be lost from `.raw`."""
        mocked_responses.add(
            "GET", DOC + "/formulas/f-1",
            json={"id": "f-1", "type": "formula", "value": 7, "somethingNew": 1})
        formula = main_document.get_formula("f-1")

        assert formula.raw["value"] == 7
        assert formula.raw["somethingNew"] == 1


class TestControls:
    def test_they_report_their_kind(self, main_document, mocked_responses):
        mocked_responses.add(
            "GET", DOC + "/controls",
            json={"items": [
                {"id": "ctrl-1", "type": "control", "name": "Threshold",
                 "controlType": "slider", "value": 4},
            ]})
        controls = main_document.list_controls()

        assert isinstance(controls[0], Control)
        assert controls[0].control_type == "slider"
        assert controls[0].value == 4

    def test_one_by_id(self, main_document, mocked_responses):
        mocked_responses.add(
            "GET", DOC + "/controls/ctrl-1",
            json={"id": "ctrl-1", "type": "control", "name": "Mode",
                  "controlType": "select", "value": "fast"})

        assert main_document.get_control("ctrl-1").value == "fast"
