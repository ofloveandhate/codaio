"""
Writing to a real doc.

Structured around one fact: Coda accepts a write in well under a second and
applies it about a minute later. Waiting after each write is therefore the
difference between a suite that takes two minutes and one that takes thirteen --
which is not a hypothetical, it is what the first version of this file did.

So the writes happen in two batched phases at session scope, each waited on once,
and the tests only assert:

    phase 1  create every page this file needs        -> one wait
    phase 2  the writes that depend on those pages    -> one wait
    tests    read things back and check them          -> no waiting

Writes are applied concurrently, so a batch costs about as long as its slowest
member rather than the sum. Two phases rather than one because you cannot append
to a page that does not exist yet.

Nothing is cleaned up. The test doc is disposable by hand -- duplicate it when it
gets cluttered -- which is why every assertion here is on an object this run
created, addressed by the id the create call returned. Never by name or count:
those pass once and fail on every run after.
"""

import time

import pytest

from codaio import Cell, MutationGroup, err

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------
# Phase 1: create the pages, all at once
# --------------------------------------------------------------------------

#: What each page is for, and the content it starts with.
WANTED = {
    "read_back": "# Hello\n\nWritten by the integration suite.",
    "appended": "# Start",
    "deleted": None,
    "kept": "# Keep me",
    "exported": "# Heading\n\nSome text.",
    "two_step": "# Two step",
}


@pytest.fixture(scope="session")
def pages(live_doc, scratch_page):
    """
    Every page this file needs, created in one batch and waited on once.

    Six creates, one wait. Done one at a time this fixture alone would cost six
    minutes.
    """
    started = time.monotonic()
    writes, ids = MutationGroup(), {}

    for name, content in WANTED.items():
        mutation = live_doc.create_page(
            f"codaio: {name}", parent_page=scratch_page, content=content
        )
        writes.add(mutation)
        ids[name] = mutation.id

    writes.wait()
    print(f"\ncreated {len(ids)} pages in {time.monotonic() - started:.0f}s "
          f"(one wait, not {len(ids)})")

    missing = [name for name, page_id in ids.items() if not page_id]
    if missing:
        pytest.fail(f"the API did not report ids for: {missing}")

    return {name: live_doc.get_page(page_id) for name, page_id in ids.items()}


# --------------------------------------------------------------------------
# Phase 2: everything that depends on those pages, also all at once
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def edits(live_doc, pages, a_table):
    """
    The dependent writes: an append, a delete, and a row's worth of cells.

    A second batch rather than part of the first, because these need pages that
    already exist. Still one wait between them all.
    """
    started = time.monotonic()
    writes = MutationGroup()

    writes.add(pages["appended"].append("A line the test appended."))
    writes.add(pages["deleted"].delete())

    row = a_table.rows(limit=1)[0]
    written = {}
    for column in _coercible_columns(a_table)[:6]:
        sent = SAMPLES[column.format.type]
        try:
            writes.add(row[column.id].set(sent))
            written[column.id] = (column, sent)
        except Exception as exc:  # reporting, not asserting
            print(f"  {column.name!r} rejected {sent!r}: {type(exc).__name__}: {exc}")

    writes.wait()
    print(f"applied {len(writes)} dependent writes in "
          f"{time.monotonic() - started:.0f}s")

    return {"row_id": row.id, "written": written}


#: A plausible value per column type, so the write is accepted and the
#: interesting part is what Coda does to it rather than whether it errors.
SAMPLES = {
    "text": "codaio round trip",
    "currency": "12.34",
    "number": "42",
    "percent": "0.5",
    "date": "2026-01-15",
    "dateTime": "2026-01-15T09:30:00",
    "time": "09:30",
    "duration": "90 minutes",
    "checkbox": True,
    "slider": 3,
    "scale": 3,
}


def _coercible_columns(table):
    """Writable columns this test knows how to fill, coercing types first."""
    candidates = [
        column for column in table.columns()
        if not column.calculated and column.format.type in SAMPLES
    ]
    candidates.sort(key=lambda column: column.format.type == "text")
    return candidates


# --------------------------------------------------------------------------
# The tests, which only read
# --------------------------------------------------------------------------


class TestPageWrites:
    def test_a_created_page_is_where_it_was_put(self, live_doc, pages, scratch_page):
        page = pages["read_back"]

        assert page.parent and page.parent.id == scratch_page.id

    def test_appended_content_is_there(self, edits, live_doc, pages):
        page = live_doc.get_page(pages["appended"].id)
        text = " ".join(line.content or "" for line in page.content())

        assert "appended" in text

    def test_a_deleted_page_reports_gone(self, edits, live_doc, pages):
        """
        410 rather than 404: the API is saying this page was real, which is a
        different situation from a bad id and worth telling apart.
        """
        with pytest.raises((err.Gone, err.NotFound)) as caught:
            live_doc.get_page(pages["deleted"].id)

        print(f"\n  deleted page reads as {type(caught.value).__name__} "
              f"({caught.value.status_code})")

    def test_deleting_no_content_is_refused_before_the_request(self, pages):
        """
        The API treats an empty list of element ids like an omitted one and
        deletes everything, so codaio refuses to send it. Costs no round trip.
        """
        page = pages["kept"]

        with pytest.raises(err.InvalidQuery):
            page.delete_content([])

        assert page.content(), "the page still has its content"


class TestPageExport:
    def test_export_to_markdown(self, pages):
        markdown = pages["exported"].export_text("markdown")

        assert "Heading" in markdown
        print(f"\n  exported markdown:\n{markdown[:200]}")

    def test_the_two_step_flow(self, pages):
        """
        What `done` gates on matters here. `status` is an untyped string with no
        documented values, so codaio waits for a download link rather than
        believing it.
        """
        export = pages["two_step"].begin_export("html")
        assert export.request_id

        export.wait()
        assert export.download_link
        print(f"\n  export status when finished: {export.status!r}")

        assert b"Two step" in export.read()


class TestRowWrites:
    def test_upsert_reports_the_rows_it_will_add(self, a_table):
        writable = [c for c in a_table.columns() if not c.calculated]
        if not writable:
            pytest.skip("no writable columns in this table")

        written = a_table.upsert_row([Cell(writable[0], "codaio integration test")])

        assert written.request_id
        assert not written.completed, "a 202 has not been applied yet"
        print(f"\n  upsert reported added rows: {written.row_ids}")

    def test_what_coda_stores_is_not_always_what_was_written(self, a_table, edits):
        """
        The reason the old polling loop could never finish.

        It re-read the row until the value equalled what was sent, unbounded. But
        values are coerced to the column's format, so for many columns that
        moment never arrived. Reported rather than asserted: what coercion
        happens is Coda's business.
        """
        stored = dict(a_table.get_row_by_id(edits["row_id"]).values)

        print("\n  sent -> stored:")
        for column_id, (column, sent) in edits["written"].items():
            got = stored.get(column_id)
            note = "  <-- coerced" if got != sent else ""
            print(f"    {column.name!r:24} ({column.format.type:9}) "
                  f"{sent!r} -> {got!r}{note}")

    def test_an_unknown_column_raises_rather_than_reaching_the_api(self, a_table):
        with pytest.raises(err.ColumnNotFound):
            a_table.upsert_row([Cell("No Such Column", "x")])


class TestHowSlowWritesAre:
    def test_measure_one_write_end_to_end(self, live_doc, scratch_page):
        """
        Deliberately unbatched: this is the measurement everything else is
        arranged around, and it is nothing like the "several seconds" the API
        documents.
        """
        started = time.monotonic()
        written = live_doc.create_page("codaio: timing", parent_page=scratch_page)
        accepted = time.monotonic() - started

        assert written.request_id
        assert not written.completed, "a 202 has not been applied yet"

        written.wait()
        applied = time.monotonic() - started

        print(f"\n  accepted after {accepted:.1f}s, applied after {applied:.0f}s")
        assert written.completed
