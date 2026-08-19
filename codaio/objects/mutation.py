"""
Writes that the API has accepted but not necessarily applied yet.

Every mutating endpoint answers 202 rather than 200, meaning the edit was
queued. The response carries a `requestId`, and `GET /mutationStatus/{id}` says
whether that edit has been dealt with.

One thing about that endpoint shapes this whole module: it reports `completed`
and an optional `warning`, and **there is no failure field**. So `completed`
means "the API has stopped working on this", not "it did what you asked". A
mutation that completes with a warning did something other than what was
requested, and a mutation that is rejected outright may simply never complete.
Read `.warning`, and re-read the object when it matters.

Status is also not kept for long -- the API documents roughly a day -- so this is
for checking a write you just made, not for auditing one from last week.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional

import attr

from codaio import err

#: How long to wait for a write before giving up, by default.
#:
#: Deliberately far higher than the API's own claim. Coda documents edits as
#: "generally processed within several seconds"; measured against a real doc, a
#: row update reported `completed` after **41 seconds** and creating a page after
#: about **60**. A minute-long default therefore times out on ordinary, healthy
#: writes -- which is worse than useless, because the edit was fine and the error
#: says otherwise.
#:
#: Three minutes is not a prediction that writes take three minutes. It is the
#: point past which something is actually wrong rather than merely slow, and
#: nothing waits this long in practice unless it has to: polling stops the moment
#: the API says the edit is done.
MUTATION_TIMEOUT = 180.0

if TYPE_CHECKING:  # pragma: no cover
    from codaio.client import Coda


@attr.s(auto_attribs=True, eq=False, repr=False)
class Mutation:
    """
    One accepted write, and the means to find out whether it landed.

    Returned by every method that changes something. Ignoring it is fine when
    you do not care yet; :meth:`wait` is there when you do.
    """

    coda: "Coda" = attr.ib(repr=False)
    request_id: str = None
    completed: bool = False
    warning: str = None
    #: Whatever else the write returned -- new row ids, the affected id, and so on.
    result: Dict = attr.ib(factory=dict, repr=False)

    @classmethod
    def from_response(cls, coda: "Coda", payload: Dict) -> "Mutation":
        """
        Wrap a write's response.

        A response with no `requestId` was not asynchronous -- a few endpoints
        answer 200 or 201 and are simply done -- so the mutation starts already
        complete rather than being `None` and forcing a check at every call site.
        """
        payload = payload or {}
        request_id = payload.get("requestId")
        return cls(
            coda=coda,
            request_id=request_id,
            completed=request_id is None,
            result={k: v for k, v in payload.items() if k != "requestId"},
        )

    # -- the ids a write hands back ---------------------------------------

    @property
    def id(self) -> Optional[str]:
        """The affected object's id, for writes that name one."""
        return self.result.get("id")

    @property
    def row_ids(self) -> List[str]:
        """
        Row ids a write reports.

        For an upsert this is `addedRowIds`, which the API only fills in when no
        key columns were given -- with key columns it cannot say in advance which
        rows an upsert will touch.
        """
        return self.result.get("rowIds") or self.result.get("addedRowIds") or []

    # -- finding out whether it landed ------------------------------------

    def refresh(self) -> "Mutation":
        """Ask once whether the edit has been dealt with."""
        if self.completed or not self.request_id:
            return self
        status = self.coda.get_mutation_status(self.request_id)
        self.completed = bool(status.get("completed"))
        self.warning = status.get("warning")
        return self

    @property
    def done(self) -> bool:
        """Whether the API has reported this edit dealt with. See `completed`."""
        return self.completed

    def wait(
        self,
        *,
        timeout: float = MUTATION_TIMEOUT,
        interval: float = 1.0,
        multiplier: float = 1.5,
        max_interval: float = 10.0,
        sleep=time.sleep,
        clock=time.monotonic,
    ) -> "Mutation":
        """
        Block until the API reports the edit dealt with.

        **Expect this to take the better part of a minute.** A single write is
        slow to be applied -- see :data:`MUTATION_TIMEOUT` for the measurements --
        so waiting on writes one at a time is the difference between a script
        that runs in seconds and one that runs in hours. For more than one write,
        issue them all and wait once with a :class:`MutationGroup`.

        Raises :class:`codaio.err.MutationTimeout` rather than looping forever.
        The timeout is not proof the edit failed -- it is almost always still
        queued -- so the error carries the request id to poll again later.

        .. code-block:: python

            write = table.upsert_row(cells)
            write.wait()
            if write.warning:
                print("the API did something other than asked:", write.warning)

        `completed` is not success. The status endpoint has no failure field, so
        re-read the rows when it matters that the edit did what you meant.
        """
        if self.completed or not self.request_id:
            return self

        deadline = clock() + timeout
        wait_for = interval
        while True:
            self.refresh()
            if self.completed:
                return self
            if clock() >= deadline:
                raise err.MutationTimeout(
                    f"edit {self.request_id!r} was accepted but had not completed "
                    f"after {timeout:g}s. It is most likely still queued rather "
                    f"than lost; poll again with this request id.",
                    request_id=self.request_id,
                )
            sleep(wait_for)
            wait_for = min(wait_for * multiplier, max_interval)

    def __repr__(self):
        state = "completed" if self.completed else "pending"
        warned = f", warning={self.warning!r}" if self.warning else ""
        return f"Mutation(request_id={self.request_id!r}, {state}{warned})"


@attr.s(auto_attribs=True, repr=False)
class MutationGroup:
    """
    Several writes, waited on together.

    This is how a batch of edits stays quick. Writes are applied concurrently by
    the API, so issuing all of them and then waiting once costs about as long as
    the slowest single write -- while waiting after each one costs their sum, and
    at roughly a minute apiece that is the difference between a minute and an
    afternoon.

    .. code-block:: python

        writes = MutationGroup()
        for row in table.iter_rows():
            writes.add(row["Done"].set(True))     # no waiting
        writes.wait()                             # once, for all of them

    Returned directly by the operations that are already several requests, such
    as a chunked :meth:`codaio.Table.delete_rows`.
    """

    mutations: List[Mutation] = attr.ib(factory=list)

    def add(self, mutation: "Mutation") -> "Mutation":
        """Put a write in the group and hand it straight back."""
        if mutation is not None:
            self.mutations.append(mutation)
        return mutation

    def extend(self, mutations: Iterable["Mutation"]) -> "MutationGroup":
        """Put several writes in the group."""
        self.mutations.extend(m for m in mutations if m is not None)
        return self

    def wait(
        self,
        *,
        timeout: float = MUTATION_TIMEOUT,
        interval: float = 1.0,
        multiplier: float = 1.5,
        max_interval: float = 10.0,
        sleep=time.sleep,
        clock=time.monotonic,
    ) -> "MutationGroup":
        """
        Wait for every write in the group, against one shared deadline.

        The deadline is shared rather than per-write on purpose. Waiting on each
        in turn with its own timeout would let one slow write consume the whole
        budget before the others were even asked about -- and since they are all
        already in flight, the second is usually finished by the time the first
        is.

        Raises :class:`codaio.err.MutationTimeout` naming everything still
        outstanding, so a caller can poll those ids again rather than starting
        over.
        """
        deadline = clock() + timeout
        wait_for = interval

        while True:
            pending = [m for m in self.mutations if not m.completed and m.request_id]
            if not pending:
                return self
            for mutation in pending:
                mutation.refresh()
            if all(m.completed for m in pending):
                return self
            if clock() >= deadline:
                outstanding = [m.request_id for m in self.mutations if not m.completed]
                raise err.MutationTimeout(
                    f"{len(outstanding)} of {len(self.mutations)} writes had not "
                    f"completed after {timeout:g}s: {outstanding}. They are most "
                    f"likely still queued rather than lost; poll again with those "
                    f"request ids.",
                    request_id=outstanding[0] if outstanding else None,
                )
            sleep(wait_for)
            wait_for = min(wait_for * multiplier, max_interval)

    def refresh(self) -> "MutationGroup":
        """Ask about every mutation once."""
        for mutation in self.mutations:
            mutation.refresh()
        return self

    @property
    def completed(self) -> bool:
        """Whether every write in this group has been dealt with."""
        return all(mutation.completed for mutation in self.mutations)

    #: Alias of :attr:`completed`, matching `Mutation.done`.
    done = completed

    @property
    def warnings(self) -> List[str]:
        """Every warning any of these writes came back with."""
        return [m.warning for m in self.mutations if m.warning]

    @property
    def request_ids(self) -> List[str]:
        """Every request id in this group, for polling later."""
        return [m.request_id for m in self.mutations if m.request_id]

    @property
    def row_ids(self) -> List[str]:
        """Every row id these writes reported, across the group."""
        out: List[str] = []
        for mutation in self.mutations:
            out.extend(mutation.row_ids)
        return out

    def __iter__(self) -> Iterable[Mutation]:
        return iter(self.mutations)

    def __len__(self) -> int:
        return len(self.mutations)

    def __repr__(self):
        pending = sum(1 for m in self.mutations if not m.completed)
        return f"MutationGroup({len(self.mutations)} writes, {pending} pending)"
