"""
The base every API object shares.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict

import attr
import inflection

if TYPE_CHECKING:  # pragma: no cover
    from codaio.objects.document import Document


@attr.s(hash=True)
class CodaObject:
    id: str = attr.ib(repr=False)
    type: str = attr.ib(repr=False)
    href: str = attr.ib(repr=False)

    document: Document = attr.ib(repr=False)

    @classmethod
    def from_json(cls, js: Dict, *, document: Document):
        js = {inflection.underscore(k): v for k, v in js.items()}
        for key in ["parent", "format"]:
            if key in js:
                js.pop(key)
        return cls(**js, document=document)

    def meta_to_dict(self, incl_doc=False) -> Dict:
        """ return the metdata about this CodaObject as a dict. 
        Expected that derived types will also call this function."""

        meta = {'id':self.id,'type':self.type,'href':self.href}
        if incl_doc: meta['doc'] = self.document

        return meta
