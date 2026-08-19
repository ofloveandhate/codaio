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
        return self.completed

    def wait(
        self,
        *,
        timeout: float = 60.0,
        interval: float = 0.5,
        multiplier: float = 1.5,
        max_interval: float = 5.0,
        sleep=time.sleep,
        clock=time.monotonic,
    ) -> "Mutation":
        """
        Block until the API reports the edit dealt with.

        Raises :class:`codaio.err.MutationTimeout` rather than looping forever.
        The timeout is not proof the edit failed -- it is almost always still
        queued -- so the error carries the request id to poll again later.
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
    Several writes made together, so a bulk call can be waited on as one thing.

    A bulk delete is chunked into several requests, and each of those is its own
    mutation with its own id.
    """

    mutations: List[Mutation] = attr.ib(factory=list)

    def wait(self, **kwargs) -> "MutationGroup":
        """Wait for every mutation in turn."""
        for mutation in self.mutations:
            mutation.wait(**kwargs)
        return self

    def refresh(self) -> "MutationGroup":
        for mutation in self.mutations:
            mutation.refresh()
        return self

    @property
    def completed(self) -> bool:
        return all(mutation.completed for mutation in self.mutations)

    done = completed

    @property
    def warnings(self) -> List[str]:
        """Every warning any of these writes came back with."""
        return [m.warning for m in self.mutations if m.warning]

    @property
    def request_ids(self) -> List[str]:
        return [m.request_id for m in self.mutations if m.request_id]

    @property
    def row_ids(self) -> List[str]:
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
