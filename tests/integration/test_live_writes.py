"""
Writing to a real doc.

Everything here creates something and leaves it there. That is the deal: nothing
is cleaned up, because cleanup runs on the unhappy path and a half-finished
sweep leaves the doc worse than none. Duplicate the doc in the Coda UI when it
gets cluttered.

The rule that makes that work: assert on what this run created, by the id the
create call returned. Never by name, never by count, never by asserting a
listing is exhaustive -- those pass once and then fail on every later run.
"""

import pytest

from codaio import Cell, err

pytestmark = pytest.mark.integration


class TestPageWrites:
    def test_create_a_page_and_read_it_back(self, live_doc, scratch_page):
        written = live_doc.create_page(
            "codaio: created by a test",
            parent_page=scratch_page,
            content="# Hello\n\nWritten by the integration suite.",
        )
        written.wait()

        assert written.id, "creating a page should report the new page's id"
        page = live_doc.get_page(written.id)
        assert page.id == written.id
        assert page.parent and page.parent.id == scratch_page.id

    def test_append_then_read_the_content_back(self, live_doc, scratch_page):
        written = live_doc.create_page(
            "codaio: content edits", parent_page=scratch_page, content="# Start"
        )
        written.wait()
        page = live_doc.get_page(written.id)

        page.append("A line the test appended.").wait()

        text = " ".join(line.content or "" for line in page.content())
        assert "appended" in text

    def test_a_deleted_page_reports_gone(self, live_doc, scratch_page):
        """
        410 rather than 404: the API is saying this page was real.
        """
        written = live_doc.create_page(
            "codaio: to be deleted", parent_page=scratch_page
        )
        written.wait()
        page = live_doc.get_page(written.id)

        page.delete().wait()

        with pytest.raises((err.Gone, err.NotFound)) as caught:
            live_doc.get_page(written.id)
        print(f"\ndeleted page reads as {type(caught.value).__name__} "
              f"({caught.value.status_code})")

    def test_deleting_no_content_is_refused_before_the_request(self, live_doc,
                                                               scratch_page):
        """
        The API treats an empty list of element ids like an omitted one and
        deletes everything, so codaio refuses to send it.
        """
        written = live_doc.create_page(
            "codaio: empty delete guard", parent_page=scratch_page, content="# Keep me"
        )
        written.wait()
        page = live_doc.get_page(written.id)

        with pytest.raises(err.InvalidQuery):
            page.delete_content([])

        assert page.content(), "the page still has its content"


class TestPageExport:
    def test_export_a_page_to_markdown(self, live_doc, scratch_page):
        written = live_doc.create_page(
            "codaio: export me",
            parent_page=scratch_page,
            content="# Heading\n\nSome text.",
        )
        written.wait()
        page = live_doc.get_page(written.id)

        markdown = page.export_text("markdown")

        assert "Heading" in markdown
        print(f"\nexported markdown:\n{markdown[:200]}")

    def test_the_two_step_flow(self, live_doc, scratch_page):
        """
        What `done` gates on matters here: `status` is an untyped string, and the
        spec's own example shows a terminal-looking value on a response with no
        download link.
        """
        written = live_doc.create_page(
            "codaio: two-step export", parent_page=scratch_page, content="# Two step"
        )
        written.wait()
        page = live_doc.get_page(written.id)

        export = page.begin_export("html")
        assert export.request_id

        export.wait()
        assert export.download_link
        print(f"\nexport status when finished: {export.status!r}")

        assert b"Two step" in export.read()


class TestRowWrites:
    def test_upsert_a_row_and_wait_for_it(self, a_table):
        columns = a_table.columns()
        writable = [c for c in columns if not c.calculated]
        if not writable:
            pytest.skip("no writable columns in this table")

        target = writable[0]
        written = a_table.upsert_row([Cell(target, "codaio integration test")])
        written.wait()

        assert written.request_id
        print(f"\nupsert reported added rows: {written.row_ids}")
        if written.warning:
            print(f"warning from the API: {written.warning}")

    def test_a_write_is_coerced_and_the_cell_reports_what_was_stored(self, a_table):
        """
        The reason the old polling loop could never finish.

        It waited for the value read back to equal the value written, but Coda
        coerces to the column's format, so for many columns that never happened.
        """
        rows = a_table.rows(limit=1)
        if not rows:
            pytest.skip("no rows to edit")
        writable = [c for c in a_table.columns() if not c.calculated]
        if not writable:
            pytest.skip("no writable columns")

        row, column = rows[0], writable[0]
        cell = row[column.id]
        before = cell.raw_value

        cell.set("codaio round trip")

        print(f"\n{column.name} ({column.format.type}): wrote 'codaio round trip', "
              f"stored {cell.raw_value!r} (was {before!r})")

    def test_an_unknown_column_raises_rather_than_reaching_the_api(self, a_table):
        with pytest.raises(err.ColumnNotFound):
            a_table.upsert_row([Cell("No Such Column", "x")])


class TestMutationStatus:
    def test_a_write_reports_completion(self, live_doc, scratch_page):
        written = live_doc.create_page(
            "codaio: mutation status", parent_page=scratch_page
        )

        assert not written.completed, "a 202 has not been applied yet"
        written.wait()
        assert written.completed
