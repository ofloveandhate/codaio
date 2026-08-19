"""
Writing to a real doc.

Everything here creates something and leaves it there. That is the deal: nothing
is cleaned up, because cleanup runs on the unhappy path and a half-finished
sweep leaves the doc worse than none. Duplicate the doc in the Coda UI when it
gets cluttered.

The rule that makes that work: assert on what this run created, by the id the
create call returned. Never by name, never by count, never by asserting a
listing is exhaustive -- those pass once and then fail on every later run.

The other thing shaping this file is how slow a write is to be *applied*. Coda
accepts one in well under a second and reports it complete around forty seconds
later, so waiting after every write turns this suite into a coffee break. The
session-scoped `pending` fixture collects writes and waits on them once; only
the tests that must read something back immediately wait on their own.
"""

import time

import pytest

from codaio import Cell, err

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def pending():
    """
    Writes made by this session, waited on once at the end.

    Writes are applied concurrently, so waiting on the lot costs about as long
    as the slowest -- rather than the sum, which at roughly a minute apiece is
    the difference between a minute and an afternoon.
    """
    from codaio import MutationGroup

    group = MutationGroup()
    yield group
    if group.mutations:
        print(f"\nwaiting once for {len(group)} writes...")
        started = time.monotonic()
        try:
            group.wait()
        except err.MutationTimeout as exc:
            print(f"  not all applied: {exc}")
        else:
            print(f"  all applied after {time.monotonic()-started:.0f}s")
        for warning in group.warnings:
            print(f"  warning from the API: {warning}")


class TestPageWrites:
    def test_create_a_page_and_read_it_back(self, live_doc, scratch_page):
        """
        Waits on its own, because it reads the page back immediately. Most tests
        here should not: see the `pending` fixture.
        """
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
    def test_upsert_a_row(self, a_table, pending):
        columns = a_table.columns()
        writable = [c for c in columns if not c.calculated]
        if not writable:
            pytest.skip("no writable columns in this table")

        target = writable[0]
        written = pending.add(
            a_table.upsert_row([Cell(target, "codaio integration test")])
        )

        assert written.request_id
        assert not written.completed, "a 202 has not been applied yet"
        print(f"\nupsert reported added rows: {written.row_ids}")

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

    def test_what_coda_stores_is_not_always_what_was_written(self, a_table, pending):
        """
        The reason the old polling loop could never finish.

        It re-read the row until the value equalled what was sent, unbounded. But
        values are coerced to the column's format, so for many columns that
        moment never arrived: "12.34" comes back as a number, a date is
        reformatted, a duration is normalised. Waiting on `mutationStatus`
        answers the question that was actually being asked.

        Reports rather than asserts, because what coercion happens is Coda's
        business and differs per column.
        """
        rows = a_table.rows(limit=1)
        if not rows:
            pytest.skip("no rows to edit")

        candidates = [
            column for column in a_table.columns()
            if not column.calculated and column.format.type in self.SAMPLES
        ]
        if not candidates:
            pytest.skip("no writable columns of a type this test knows how to fill")

        # Types that coerce are the interesting ones; text is the dull case.
        candidates.sort(key=lambda column: column.format.type == "text")

        row = rows[0]
        print("\n  what Coda stored, per column type:")
        for column in candidates[:6]:
            sent = self.SAMPLES[column.format.type]
            cell = row[column.id]
            try:
                pending.add(cell.set(sent))
            except Exception as exc:  # reporting, not asserting
                print(f"    {column.name!r:24} ({column.format.type:9}) "
                      f"sent {sent!r} -> {type(exc).__name__}: {exc}")
                continue
            print(f"    {column.name!r:24} ({column.format.type:9}) sent {sent!r}")

        # One wait for all of them, then one read to see what was stored.
        pending.wait()
        fresh = dict(a_table.get_row_by_id(row.id).values)
        print("  what Coda stored:")
        for column in candidates[:6]:
            sent = self.SAMPLES[column.format.type]
            stored = fresh.get(column.id)
            changed = "  <-- coerced" if stored != sent else ""
            print(f"    {column.name!r:24} ({column.format.type:9}) "
                  f"stored {stored!r}{changed}")

    def test_an_unknown_column_raises_rather_than_reaching_the_api(self, a_table):
        with pytest.raises(err.ColumnNotFound):
            a_table.upsert_row([Cell("No Such Column", "x")])


class TestMutationStatus:
    def test_a_write_is_accepted_before_it_is_applied(self, live_doc, scratch_page,
                                                      pending):
        """
        The distinction the whole Mutation type exists for: 202 means queued.
        """
        written = pending.add(
            live_doc.create_page("codaio: mutation status", parent_page=scratch_page)
        )

        assert written.request_id
        assert not written.completed

    def test_how_long_a_write_actually_takes(self, live_doc, scratch_page):
        """
        Reported, because it is the number that decides how this library should
        be used -- and it is nothing like the "several seconds" the docs claim.
        """
        started = time.monotonic()
        written = live_doc.create_page(
            "codaio: timing", parent_page=scratch_page
        )
        accepted = time.monotonic() - started

        written.wait()
        applied = time.monotonic() - started

        print(f"\n  accepted after {accepted:.1f}s, applied after {applied:.0f}s")
        assert written.completed
