"""
Pages. Historically called sections, which is what the class is still named.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, Tuple

import attr
from dateutil.parser import parse

from codaio.objects.base import CodaObject, PageReference, Reference, ref, refs


@attr.s(auto_attribs=True, eq=False, repr=False)
class Section(CodaObject):
    name: str = None
    browser_link: str = attr.ib(default=None, repr=False)
    subtitle: str = attr.ib(default=None, repr=False)
    icon: Dict = attr.ib(default=None, repr=False)
    image: Dict = attr.ib(default=None, repr=False)
    content_type: str = attr.ib(default=None, repr=False)
    is_hidden: bool = attr.ib(default=None, repr=False)
    is_effectively_hidden: bool = attr.ib(default=None, repr=False)
    # The two fields the old builder threw away, which between them are what
    # makes the page tree reconstructable from a single flat listing.
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
