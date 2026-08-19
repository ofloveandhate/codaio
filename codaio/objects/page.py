"""
Pages: the objects, the tree they form, their content, and exporting them.

Three things about pages shape everything here.

A page listing is **flat, but self-describing**: every page carries both its
`parent` and its `children`, so one request rebuilds the entire tree. Those were
the two fields the old object model discarded on arrival.

Page content has **two different reads**. `GET .../content` is synchronous but
speaks only plain text, returning styled lines with stable element ids. Markdown
and HTML come only from an asynchronous export: start one, poll it, then fetch a
link that expires shortly after it appears.

And **pages cannot be moved**. `PageUpdate` has no field for reparenting, so
there is deliberately no `Page.move()` here to imply otherwise.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import IO, Dict, Iterator, List, Optional, Tuple, Union

import attr
from dateutil.parser import parse

from codaio import err
from codaio.http import fetch_untrusted
from codaio.objects.base import CodaObject, PageReference, Reference, ref, refs
from codaio.objects.mutation import Mutation


#: What a page's content is. A canvas holds editable content; an embed wraps an
#: external URL; a sync page mirrors another doc or page.
CANVAS = "canvas"
EMBED = "embed"
SYNC_PAGE = "syncPage"

#: The styles a line of page content can have.
PAGE_LINE_STYLES = frozenset({
    "blockQuote", "bulletedList", "checkboxList", "code", "collapsibleList",
    "h1", "h2", "h3", "numberedList", "paragraph", "pullQuote",
})


# --------------------------------------------------------------------------
# Content to write
# --------------------------------------------------------------------------


@attr.s(auto_attribs=True)
class CanvasContent:
    """
    Markdown or HTML to put on a page.

    >>> CanvasContent("# Hello").to_json()
    {'type': 'canvas', 'canvasContent': {'format': 'markdown', 'content': '# Hello'}}

    A plain string is taken as Markdown wherever content is accepted, so this
    class is only needed to say otherwise:

    .. code-block:: python

        page.append("## A new section")
        page.append(CanvasContent("<h2>A new section</h2>", format="html"))
    """

    content: str
    format: str = "markdown"

    def to_json(self) -> Dict:
        if self.format not in ("markdown", "html"):
            raise err.InvalidQuery(
                f"format must be 'markdown' or 'html', got {self.format!r}"
            )
        return {
            "type": CANVAS,
            "canvasContent": {"format": self.format, "content": self.content},
        }


@attr.s(auto_attribs=True)
class EmbedContent:
    """An external URL for the page to embed."""

    url: str
    render_method: str = None

    def to_json(self) -> Dict:
        out = {"type": EMBED, "url": self.url}
        if self.render_method:
            out["renderMethod"] = self.render_method
        return out


@attr.s(auto_attribs=True)
class SyncPageContent:
    """A page or doc for this page to mirror."""

    source_doc_id: str
    mode: str = "page"
    source_page_id: str = None
    include_subpages: bool = None

    def to_json(self) -> Dict:
        out = {"type": SYNC_PAGE, "mode": self.mode, "sourceDocId": self.source_doc_id}
        if self.source_page_id:
            out["sourcePageId"] = self.source_page_id
        if self.include_subpages is not None:
            out["includeSubpages"] = self.include_subpages
        return out


def _content_payload(content) -> Optional[Dict]:
    if content is None:
        return None
    if isinstance(content, str):
        content = CanvasContent(content)
    if hasattr(content, "to_json"):
        return content.to_json()
    return content


# --------------------------------------------------------------------------
# Content that was read
# --------------------------------------------------------------------------


@attr.s(auto_attribs=True, eq=False, repr=False)
class ContentItem(CodaObject):
    """
    One line of a page's content, from the synchronous content listing.

    `id` is stable -- it is what you pass as `element_id` to edit content
    relative to this line, or to delete it.
    """

    item_content: Dict = attr.ib(default=None, repr=False)

    @property
    def style(self) -> Optional[str]:
        """`h1`, `paragraph`, `bulletedList`, and so on."""
        return (self.item_content or {}).get("style")

    @property
    def content(self) -> Optional[str]:
        """The line's text. Plain text -- the synchronous read offers nothing else."""
        return (self.item_content or {}).get("content")

    @property
    def format(self) -> Optional[str]:
        """The content format. Only `plainText` is available from this endpoint."""
        return (self.item_content or {}).get("format")

    @property
    def line_level(self) -> Optional[int]:
        """Indentation depth, for list items and nested paragraphs."""
        return (self.item_content or {}).get("lineLevel")

    def __repr__(self):
        text = (self.content or "")[:40]
        return f"ContentItem(id={self.id!r}, style={self.style!r}, content={text!r})"


# --------------------------------------------------------------------------
# Exporting
# --------------------------------------------------------------------------


@attr.s(auto_attribs=True, eq=False, repr=False)
class PageExport:
    """
    An export of a page's content, in progress or finished.

    Both halves of the flow are exposed rather than only a blocking helper: an
    export is a write against the tightest rate-limit bucket, so a caller
    exporting many pages needs to start them and collect them on their own terms.
    :meth:`wait` is there when that does not matter.

    One page, the simple way:

    .. code-block:: python

        markdown = page.export_text("markdown")

    Many pages, without waiting on each in turn. Starting an export is a write
    against the five-per-ten-seconds bucket, so start them steadily rather than
    all at once:

    .. code-block:: python

        exports = [page.begin_export("markdown") for page in doc.list_pages()]
        for export in exports:
            path = f"{export.page.id}.md"
            Path(path).write_text(export.wait().text())
    """

    page: "Page" = attr.ib(repr=False)
    output_format: str = "markdown"
    request_id: str = None
    href: str = attr.ib(default=None, repr=False)
    status: str = None
    download_link: str = attr.ib(default=None, repr=False)
    error: str = None
    raw: Dict = attr.ib(factory=dict, repr=False)

    @classmethod
    def from_json(cls, js: Dict, *, page: "Page", output_format: str) -> "PageExport":
        return cls(
            page=page,
            output_format=output_format,
            request_id=js.get("id"),
            href=js.get("href"),
            status=js.get("status"),
            download_link=js.get("downloadLink"),
            error=js.get("error"),
            raw=dict(js),
        )

    def refresh(self) -> "PageExport":
        """Poll once, updating in place."""
        js = self.page.document.coda.get_page_export(
            self.page.document.id, self.page.id, self.request_id
        )
        self.status = js.get("status")
        self.download_link = js.get("downloadLink")
        self.error = js.get("error")
        self.raw = dict(js)
        return self

    @property
    def done(self) -> bool:
        """
        Whether the export has finished, successfully or not.

        Gates on a download link or an error being present, never on `status`.
        The API types that field as a plain string with no documented values, so
        reading it would be guessing -- its own example says "complete" while an
        export is still being prepared.
        """
        return bool(self.download_link) or bool(self.error)

    @property
    def failed(self) -> bool:
        """Whether the export finished unsuccessfully."""
        return bool(self.error)

    def raise_for_error(self) -> "PageExport":
        """Raise `err.ExportFailed` if the export failed; return self otherwise."""
        if self.error:
            raise err.ExportFailed(
                f"exporting page {self.page.id!r} failed: {self.error}"
            )
        return self

    def wait(
        self,
        *,
        timeout: float = 300.0,
        interval: float = 1.0,
        multiplier: float = 1.5,
        max_interval: float = 15.0,
        sleep=time.sleep,
        clock=time.monotonic,
    ) -> "PageExport":
        """
        Poll until the export finishes. Never loops forever.

        The default interval starts at a second and backs off: an export is a
        doc-content write, and that bucket allows only five requests per ten
        seconds, so polling hard makes the export slower rather than faster.
        """
        if self.done:
            return self.raise_for_error()

        deadline = clock() + timeout
        wait_for = interval
        while True:
            # Poll before sleeping: an export that has already finished should
            # cost nothing to collect.
            self.refresh()
            if self.done:
                return self.raise_for_error()
            if clock() >= deadline:
                raise err.ExportTimeout(
                    f"export {self.request_id!r} of page {self.page.id!r} did not "
                    f"finish within {timeout:g}s. It may still be running; poll "
                    f"again with the same request id.",
                    request_id=self.request_id,
                )
            sleep(wait_for)
            wait_for = min(wait_for * multiplier, max_interval)

    def _link(self) -> str:
        self.raise_for_error()
        if not self.download_link:
            raise err.ExportFailed(
                f"export {self.request_id!r} has no download link yet; call "
                f"wait() or refresh() first.",
                request_id=self.request_id,
            )
        return self.download_link

    def read(self, *, timeout: float = 30.0) -> bytes:
        """
        Fetch the exported bytes, with no credentials attached.

        The link points at a content host rather than the API, and it expires
        soon after it appears -- so a rejection is re-polled once for a fresh
        link before giving up, rather than reported as a failed export.
        """
        try:
            return fetch_untrusted(self._link(), timeout=timeout).content
        except (err.Forbidden, err.NotFound, err.Gone):
            self.refresh()
            return fetch_untrusted(self._link(), timeout=timeout).content

    def open(self, *, timeout: float = 30.0) -> IO[bytes]:
        """A streaming file-like over the exported bytes."""
        return fetch_untrusted(self._link(), timeout=timeout, stream=True).raw

    def text(self, *, encoding: str = "utf-8", timeout: float = 30.0) -> str:
        """The exported content as text -- Markdown or HTML, as requested."""
        return self.read(timeout=timeout).decode(encoding)

    def __repr__(self):
        state = "failed" if self.failed else ("ready" if self.done else "pending")
        return (
            f"PageExport(page={self.page.id!r}, format={self.output_format!r}, "
            f"{state})"
        )


# --------------------------------------------------------------------------
# The page itself
# --------------------------------------------------------------------------


@attr.s(auto_attribs=True, eq=False, repr=False)
class Page(CodaObject):
    """A page in a doc."""

    name: str = None
    browser_link: str = attr.ib(default=None, repr=False)
    subtitle: str = attr.ib(default=None, repr=False)
    icon: Dict = attr.ib(default=None, repr=False)
    image: Dict = attr.ib(default=None, repr=False)
    content_type: str = attr.ib(default=None, repr=False)
    is_hidden: bool = attr.ib(default=None, repr=False)
    is_effectively_hidden: bool = attr.ib(default=None, repr=False)
    # The two fields the old builder threw away, which between them are what
    # makes the tree reconstructable from one flat listing.
    parent: PageReference = attr.ib(default=None, converter=ref, repr=False)
    children: Tuple[Reference, ...] = attr.ib(factory=tuple, converter=refs, repr=False)
    authors: Tuple = attr.ib(factory=tuple, repr=False)
    created_at: dt.datetime = attr.ib(
        default=None, converter=lambda x: parse(x) if x else None, repr=False
    )
    updated_at: dt.datetime = attr.ib(
        default=None, converter=lambda x: parse(x) if x else None, repr=False
    )
    created_by: Dict = attr.ib(default=None, repr=False)
    updated_by: Dict = attr.ib(default=None, repr=False)

    # -- what kind of page is this ----------------------------------------

    @property
    def is_canvas(self) -> bool:
        """Whether this page holds editable content, as opposed to an embed or a sync."""
        return self.content_type == CANVAS

    @property
    def is_embed(self) -> bool:
        """Whether this page wraps an external URL."""
        return self.content_type == EMBED

    @property
    def is_sync_page(self) -> bool:
        """Whether this page mirrors another doc or page."""
        return self.content_type == SYNC_PAGE

    # -- reading content --------------------------------------------------

    def iter_content(self, *, page_size: int = None) -> Iterator[ContentItem]:
        """
        Walk the page's content a line at a time, fetching lazily.

        Plain text only: that is all the synchronous read offers. Use
        :meth:`export` for Markdown or HTML.
        """
        doc = self.document
        for item in doc.coda.iter_items(
            f"/docs/{doc.id}/pages/{self.id}/content", page_size=page_size
        ):
            yield ContentItem.from_json(item, document=doc)

    def content(self, *, limit: int = None) -> List[ContentItem]:
        """The page's content as a list of lines."""
        items = self.iter_content()
        if limit:
            return [item for _, item in zip(range(limit), items)]
        return list(items)

    # -- exporting --------------------------------------------------------

    def begin_export(self, output_format: str = "markdown") -> PageExport:
        """
        Start an export and return it, without waiting.

        Use this when exporting more than one page: you can start them, then
        collect them, rather than blocking on each in turn.
        """
        doc = self.document
        js = doc.coda.begin_page_export(doc.id, self.id, output_format)
        return PageExport.from_json(js, page=self, output_format=output_format)

    def export(self, output_format: str = "markdown", *, timeout: float = 300.0) -> bytes:
        """Start an export, wait for it, and return the bytes."""
        return self.begin_export(output_format).wait(timeout=timeout).read()

    def export_text(
        self, output_format: str = "markdown", *, timeout: float = 300.0,
        encoding: str = "utf-8",
    ) -> str:
        """The same, decoded."""
        return self.export(output_format, timeout=timeout).decode(encoding)

    # -- writing ----------------------------------------------------------

    def update(
        self,
        *,
        name: str = None,
        subtitle: str = None,
        icon_name: str = None,
        image_url: str = None,
        is_hidden: bool = None,
    ) -> Mutation:
        """
        Change the page's title, subtitle, icon, cover or hidden flag.

        There is no way to reparent a page: `PageUpdate` has no field for it, so
        the API cannot move pages and neither can this.

        .. code-block:: python

            page.update(name="Launch Status", subtitle="Updated weekly").wait()
        """
        data = {}
        for key, value in (
            ("name", name), ("subtitle", subtitle), ("iconName", icon_name),
            ("imageUrl", image_url), ("isHidden", is_hidden),
        ):
            if value is not None:
                data[key] = value
        if not data:
            raise err.InvalidQuery("update() was given nothing to change")
        return Mutation.from_response(
            self.document.coda,
            self.document.coda.update_page(self.document.id, self.id, data),
        )

    def _content_update(self, mode: str, content, element_id: str = None) -> Mutation:
        if isinstance(content, str):
            content = CanvasContent(content)
        payload = content.to_json()["canvasContent"]
        update = {"insertionMode": mode, "canvasContent": payload}
        if element_id:
            update["elementId"] = element_id
        return Mutation.from_response(
            self.document.coda,
            self.document.coda.update_page(
                self.document.id, self.id, {"contentUpdate": update}
            ),
        )

    def append(self, content: Union[str, CanvasContent], *, element_id: str = None) -> Mutation:
        """Add content at the end of the page, or after `element_id`."""
        return self._content_update("append", content, element_id)

    def prepend(self, content: Union[str, CanvasContent], *, element_id: str = None) -> Mutation:
        """Add content at the start of the page, or before `element_id`."""
        return self._content_update("prepend", content, element_id)

    def replace(self, content: Union[str, CanvasContent], *, element_id: str = None) -> Mutation:
        """
        Replace the page's content, or just `element_id`'s.

        Without `element_id` this replaces everything on the page.

        .. code-block:: python

            page.replace("# Fresh start")           # the whole page

        With an element id it replaces just that line. Note codaio will not retry
        that form if the request is inconclusive: the first attempt consumes the
        element, and a missing element id means "the entire page" to the API.

        .. code-block:: python

            first = page.content()[0]
            page.replace("## A better heading", element_id=first.id)
        """
        return self._content_update("replace", content, element_id)

    def delete_content(self, element_ids: List[str] = None) -> Mutation:
        """
        Delete specific elements from the page.

        Passing an empty list is refused: the API treats an empty list of ids the
        same as none at all, and would delete the entire page's content. Use
        :meth:`clear_content` when that is what you mean.
        """
        return Mutation.from_response(
            self.document.coda,
            self.document.coda.delete_page_content(
                self.document.id, self.id, element_ids
            ),
        )

    def clear_content(self) -> Mutation:
        """Delete all of the page's content, explicitly."""
        return Mutation.from_response(
            self.document.coda,
            self.document.coda.delete_page_content(self.document.id, self.id, None),
        )

    def delete(self) -> Mutation:
        """Delete the page."""
        return Mutation.from_response(
            self.document.coda,
            self.document.coda.delete_page(self.document.id, self.id),
        )

    def refresh(self) -> "Page":
        """Re-read the page from the API."""
        js = self.document.coda.get_page(self.document.id, self.id)
        return Page.from_json(js, document=self.document)


# --------------------------------------------------------------------------
# The tree
# --------------------------------------------------------------------------


@attr.s(auto_attribs=True)
class PageTree:
    """
    The page hierarchy of a doc, built from one flat listing.

    Both directions are used: `children` gives the order pages appear in, which
    a `parent`-only reconstruction cannot recover, and `parent` gives the
    structure, which covers any page the `children` arrays happen to miss.

    >>> from codaio import Page
    >>> pages = [
    ...     Page.from_json({"id": "canvas-1", "type": "page", "name": "Handbook",
    ...                     "children": [{"id": "canvas-2", "type": "page"}]}),
    ...     Page.from_json({"id": "canvas-2", "type": "page", "name": "Onboarding",
    ...                     "parent": {"id": "canvas-1", "type": "page"}}),
    ... ]
    >>> tree = PageTree.from_pages(pages)
    >>> [page.name for page in tree.roots]
    ['Handbook']
    >>> [(page.name, depth) for page, depth in tree.walk()]
    [('Handbook', 0), ('Onboarding', 1)]

    In practice you get one from a document, which costs a single request
    because each page carries both its parent and its children:

    .. code-block:: python

        tree = doc.page_tree()
        for page, depth in tree.walk():
            print("  " * depth + page.name)
    """

    roots: List[Page]
    by_id: Dict[str, Page]

    @classmethod
    def from_pages(cls, pages: List[Page]) -> "PageTree":
        by_id = {page.id: page for page in pages}
        order = {page.id: index for index, page in enumerate(pages)}

        children: Dict[str, List[str]] = {page.id: [] for page in pages}
        placed = set()

        # Ordered children first, straight from each parent's own list.
        for page in pages:
            for reference in page.children:
                if reference.id in by_id:
                    children[page.id].append(reference.id)
                    placed.add(reference.id)

        # Then anything that names a parent but was not listed by it. A page
        # whose parent is not in `by_id` at all becomes a root rather than an
        # error: a truncated listing is a normal thing to be handed.
        roots = []
        for page in pages:
            if page.id in placed:
                continue
            parent_id = page.parent.id if page.parent else None
            if parent_id == page.id:
                # A page cannot be nested under itself. Treating it as a root
                # keeps it visible; filing it under itself would drop it from
                # every walk without saying so.
                roots.append(page)
                continue
            if parent_id and parent_id in by_id:
                children[parent_id].append(page.id)
            else:
                roots.append(page)

        # No re-sorting: the first pass appended in each parent's own order, and
        # the second appended what that order missed. Sorting afterwards would
        # throw away the only ordering the API actually gives us.
        roots.sort(key=lambda page: order[page.id])

        if pages and not roots:
            # Every page is somebody's child, so there is nowhere to start. That
            # cannot happen in a real doc, and walking would silently yield
            # nothing rather than reporting the problem.
            raise err.CodaError(
                "every page in this listing is a child of another, so the "
                "hierarchy has no root and cannot be walked as a tree. This "
                "usually means the parent/children references disagree."
            )

        tree = cls(roots=roots, by_id=by_id)
        tree._children = children
        return tree

    def children_of(self, page: Union[Page, str]) -> List[Page]:
        """The pages directly beneath this one, in the order the doc shows them."""
        page_id = page.id if isinstance(page, Page) else page
        return [self.by_id[child] for child in self._children.get(page_id, ())]

    def walk(self, *, include_hidden: bool = True) -> Iterator[Tuple[Page, int]]:
        """
        Depth-first through the tree, yielding `(page, depth)`.

        Iterative, and it refuses to revisit a page. The API should never return
        a cycle, but this tree is built from whatever arrives, and a hang is a
        much worse way to find out than a clear error.

        >>> from codaio import Page
        >>> tree = PageTree.from_pages([
        ...     Page.from_json({"id": "canvas-1", "type": "page", "name": "Visible"}),
        ...     Page.from_json({"id": "canvas-2", "type": "page", "name": "Draft",
        ...                     "isHidden": True}),
        ... ])
        >>> [page.name for page, _ in tree.walk()]
        ['Visible', 'Draft']
        >>> [page.name for page, _ in tree.walk(include_hidden=False)]
        ['Visible']
        """
        seen = set()
        stack = [(page, 0) for page in reversed(self.roots)]
        while stack:
            page, depth = stack.pop()
            if page.id in seen:
                raise err.CodaError(
                    f"page {page.id!r} appears twice in the page hierarchy, so it "
                    f"cannot be walked as a tree."
                )
            seen.add(page.id)
            if include_hidden or not page.is_hidden:
                yield page, depth
            stack.extend(
                (child, depth + 1) for child in reversed(self.children_of(page))
            )

    def path(self, page: Union[Page, str]) -> List[Page]:
        """
        The pages from the root down to this one, inclusive.

        Useful for mirroring a doc's structure on disk:

        >>> from codaio import Page
        >>> tree = PageTree.from_pages([
        ...     Page.from_json({"id": "canvas-1", "type": "page", "name": "Handbook",
        ...                     "children": [{"id": "canvas-2", "type": "page"}]}),
        ...     Page.from_json({"id": "canvas-2", "type": "page", "name": "Onboarding",
        ...                     "parent": {"id": "canvas-1", "type": "page"}}),
        ... ])
        >>> "/".join(page.name for page in tree.path("canvas-2"))
        'Handbook/Onboarding'
        """
        page_id = page.id if isinstance(page, Page) else page
        out = []
        seen = set()
        while page_id and page_id in self.by_id and page_id not in seen:
            seen.add(page_id)
            current = self.by_id[page_id]
            out.append(current)
            page_id = current.parent.id if current.parent else None
        return list(reversed(out))

    def __getitem__(self, page_id: str) -> Page:
        return self.by_id[page_id]

    def __iter__(self) -> Iterator[Page]:
        for page, _ in self.walk():
            yield page

    def __len__(self) -> int:
        return len(self.by_id)


PageTree._children = {}


def __getattr__(name):
    """
    `Section` was what a page was called here; the API renamed it years ago.

    A module-level `Section = Page` could not warn, and a subclass would break
    `isinstance`. This gives the same object while saying so once per import site.
    """
    if name == "Section":
        import warnings

        warnings.warn(
            "codaio Section is deprecated; pages are called Page. "
            "Section is Page, so isinstance checks keep working.",
            DeprecationWarning,
            stacklevel=2,
        )
        return Page
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
