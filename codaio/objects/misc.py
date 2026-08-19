"""
Named formulas and controls -- the parts of a doc that hold a value without
being a table.

A named formula is a calculation somebody gave a name to; a control is an input
on the canvas, like a slider or a select. Both come back with a `value`, which
goes through the same typing as a cell's, so a currency formula gives you a
:class:`~codaio.values.MoneyValue` rather than a dict.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, Tuple

import attr

from codaio.objects.base import CodaObject, PageReference, ref
from codaio.values import parse_value

#: The kinds of control a doc can hold.
CONTROL_TYPES: Tuple[str, ...] = (
    "aiBlock", "button", "checkbox", "datePicker", "dateRangePicker",
    "dateTimePicker", "lookup", "multiselect", "select", "scale", "slider",
    "reaction", "textbox", "timePicker",
)


@attr.s(auto_attribs=True, eq=False, repr=False)
class Formula(CodaObject):
    """
    A named formula in a doc.

    >>> formula = Formula.from_json({
    ...     "id": "f-1", "type": "formula", "name": "Total cost",
    ...     "value": {"@type": "MonetaryAmount", "currency": "GBP",
    ...               "amount": "42.50"},
    ... })
    >>> formula.name
    'Total cost'
    >>> formula.value.currency, formula.value.amount
    ('GBP', Decimal('42.50'))

    Note the API warns that time- and user-dependent formulas read oddly through
    it: `Today()` and `Now()` are only current to the doc's last edit, and
    `User()` may come back blank.
    """

    name: str = None
    parent: PageReference = attr.ib(default=None, converter=ref, repr=False)
    value_storage: Any = attr.ib(default=None, repr=False)

    #: JSON key that holds the value, since `value` is a property here.
    _VALUE_KEY: ClassVar[str] = "value"

    @classmethod
    def from_json(cls, js: Dict, **kwargs) -> "Formula":
        """Build a formula from an API payload."""
        js = dict(js)
        value = js.get(cls._VALUE_KEY)
        built = super().from_json(
            {k: v for k, v in js.items() if k != cls._VALUE_KEY}, **kwargs
        )
        built.value_storage = value
        built.raw = js
        return built

    @property
    def value(self):
        """
        The formula's result, typed the same way a cell's value is.

        >>> Formula.from_json({"id": "f-1", "value": 7}).value
        7
        """
        return parse_value(self.value_storage)

    def __repr__(self):
        return f"Formula(id={self.id!r}, name={self.name!r})"


@attr.s(auto_attribs=True, eq=False, repr=False)
class Control(CodaObject):
    """
    A control on a doc's canvas -- a slider, a select, a button, and so on.

    >>> control = Control.from_json({
    ...     "id": "ctrl-1", "type": "control", "name": "Threshold",
    ...     "controlType": "slider", "value": 4,
    ... })
    >>> control.name, control.control_type, control.value
    ('Threshold', 'slider', 4)
    """

    name: str = None
    control_type: str = None
    parent: PageReference = attr.ib(default=None, converter=ref, repr=False)
    value_storage: Any = attr.ib(default=None, repr=False)

    _VALUE_KEY: ClassVar[str] = "value"

    @classmethod
    def from_json(cls, js: Dict, **kwargs) -> "Control":
        """Build a control from an API payload."""
        js = dict(js)
        value = js.get(cls._VALUE_KEY)
        built = super().from_json(
            {k: v for k, v in js.items() if k != cls._VALUE_KEY}, **kwargs
        )
        built.value_storage = value
        built.raw = js
        return built

    @property
    def value(self):
        """The control's current value, typed the same way a cell's value is."""
        return parse_value(self.value_storage)

    def __repr__(self):
        return (
            f"Control(id={self.id!r}, name={self.name!r}, "
            f"control_type={self.control_type!r})"
        )
