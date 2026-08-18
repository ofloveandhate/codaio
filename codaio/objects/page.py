"""
Pages. Historically called sections, which is what the class is still named.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import attr

from codaio.objects.base import CodaObject

if TYPE_CHECKING:  # pragma: no cover
    from codaio.objects.document import Document


@attr.s(auto_attribs=True, hash=True)
class Section(CodaObject):
    name: str
    browser_link: str = attr.ib(repr=False)
    document: Document = attr.ib(repr=False)
