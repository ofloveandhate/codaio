"""
Folders, which live in a workspace rather than in a doc.

`list_folders` and `get_folder` used to build `/docs/{docId}/folders`. That is
not an endpoint the API has ever had, so both could only ever 404 -- and the
mocked suite asserted the wrong URLs, which is how it survived. A mocked test
proves codaio calls the URL codaio meant to; only the conformance check compares
that against the API the service actually publishes.
"""

import pytest

from codaio import Folder, err
from tests.conftest import BASE_URL

FOLDERS = BASE_URL + "/folders"


class TestTheEndpointsAreReal:
    def test_listing_hits_the_workspace_level_path(self, coda, mocked_responses):
        mocked_responses.add("GET", FOLDERS, json={"items": []})
        coda.list_folders(workspace_id="ws-1")

        url = mocked_responses.calls[-1].request.url
        assert url.startswith(FOLDERS)
        assert "/docs/" not in url
        assert "workspaceId=ws-1" in url

    def test_fetching_one_hits_the_workspace_level_path(self, coda, mocked_responses):
        mocked_responses.add("GET", FOLDERS + "/fl-1", json={"id": "fl-1"})
        coda.get_folder("fl-1")

        assert mocked_responses.calls[-1].request.url == FOLDERS + "/fl-1"

    def test_a_workspace_is_optional(self, coda, mocked_responses):
        """Omitting it asks for every folder the token can see."""
        mocked_responses.add("GET", FOLDERS, json={"items": []})
        coda.list_folders()

        assert "workspaceId" not in mocked_responses.calls[-1].request.url


class TestTheOldSignatureFailsLoudly:
    """
    The same call now means something different, and the difference is invisible:
    a doc id passed where a workspace id is expected would ask a coherent
    question with the wrong subject and get an empty answer, not an error.
    """

    def test_a_positional_doc_id_is_refused(self, coda, mocked_responses):
        with pytest.raises(TypeError, match="never was"):
            coda.list_folders("AbCDeFGH")

        assert not mocked_responses.calls

    def test_an_explicit_doc_id_is_refused(self, coda):
        with pytest.raises(TypeError, match="workspace"):
            coda.list_folders(doc_id="AbCDeFGH")

    def test_the_old_get_folder_call_is_refused(self, coda):
        with pytest.raises(TypeError, match="never was"):
            coda.get_folder("AbCDeFGH", "fl-1")

    def test_the_message_says_what_to_do_instead(self, coda):
        with pytest.raises(TypeError) as caught:
            coda.list_folders("AbCDeFGH")

        message = str(caught.value)
        assert "workspace_id=" in message
        assert "Document.folder" in message


class TestWriting:
    def test_create(self, coda, mocked_responses):
        mocked_responses.add("POST", FOLDERS, status=201, json={"id": "fl-new"})
        coda.create_folder("Research", "ws-1", description="Papers")

        import json as _json
        assert _json.loads(mocked_responses.calls[-1].request.body) == {
            "name": "Research", "workspaceId": "ws-1", "description": "Papers"}

    def test_update_sends_only_what_changed(self, coda, mocked_responses):
        mocked_responses.add("PATCH", FOLDERS + "/fl-1", json={"id": "fl-1"})
        coda.update_folder("fl-1", name="Renamed")

        import json as _json
        request = mocked_responses.calls[-1].request
        assert request.method == "PATCH"
        assert _json.loads(request.body) == {"name": "Renamed"}

    def test_update_with_nothing_to_do_is_refused(self, coda, mocked_responses):
        with pytest.raises(err.InvalidQuery, match="nothing to change"):
            coda.update_folder("fl-1")

        assert not mocked_responses.calls

    def test_delete(self, coda, mocked_responses):
        mocked_responses.add("DELETE", FOLDERS + "/fl-1", json={})
        coda.delete_folder("fl-1")

        assert mocked_responses.calls[-1].request.method == "DELETE"


class TestTheFolderObject:
    PAYLOAD = {
        "id": "fl-1Ab234", "type": "folder", "name": "Research",
        "browserLink": "https://coda.io/docs?folderId=fl-1Ab234",
        "description": "Papers and notes",
        "workspace": {"id": "ws-abc", "type": "workspace", "name": "Lab"},
        "createdAt": "2020-01-01T00:00:00.000Z",
    }

    def test_it_parses(self, coda):
        folder = Folder.from_json(self.PAYLOAD, coda=coda)

        assert folder.name == "Research"
        assert folder.workspace_id == "ws-abc"
        assert folder.created_at.year == 2020

    def test_it_has_no_href_and_that_is_fine(self, coda):
        """
        Unlike every other object the API returns. It is why the base class had
        to make `href` optional rather than required.
        """
        folder = Folder.from_json(self.PAYLOAD, coda=coda)

        assert folder.href is None
        assert "href" not in self.PAYLOAD

    def test_its_docs_are_filtered_by_folder(self, coda, mocked_responses):
        mocked_responses.add(
            "GET", BASE_URL + "/docs",
            json={"items": [{"id": "d1", "type": "doc", "name": "Notes"}]})
        folder = Folder.from_json(self.PAYLOAD, coda=coda)

        docs = folder.docs()

        assert [d.name for d in docs] == ["Notes"]
        assert "folderId=fl-1Ab234" in mocked_responses.calls[-1].request.url

    def test_listing_docs_costs_one_request_not_one_per_doc(self, coda,
                                                            mocked_responses):
        """
        A listing returns each doc in full, so building them by fetching each one
        again would be a request per doc for information already in hand.
        """
        mocked_responses.add(
            "GET", BASE_URL + "/docs",
            json={"items": [{"id": f"d{n}", "type": "doc", "name": f"Doc {n}"}
                            for n in range(5)]})
        folder = Folder.from_json(self.PAYLOAD, coda=coda)

        before = len(mocked_responses.calls)
        docs = folder.docs()

        assert len(docs) == 5
        assert len(mocked_responses.calls) == before + 1


class TestFromADocument:
    def test_a_doc_knows_which_folder_it_is_in(self, main_document):
        """The repo's doc fixture carries a folder, as a real payload does."""
        assert main_document.folder_id == "fl-abc"
        assert main_document.workspace_id == "ws-abc"

    def test_fetching_that_folder(self, main_document, mocked_responses):
        mocked_responses.add(
            "GET", FOLDERS + "/fl-abc",
            json={"id": "fl-abc", "type": "folder", "name": "Test Folder",
                  "workspace": {"id": "ws-abc", "type": "workspace"}})

        assert main_document.get_folder().name == "Test Folder"

    def test_a_doc_built_without_a_folder_says_so(self, coda):
        from codaio import Document

        doc = Document.from_json({"id": "d1", "type": "doc"}, coda=coda)

        with pytest.raises(err.FolderNotFound, match="omitted"):
            doc.get_folder()
