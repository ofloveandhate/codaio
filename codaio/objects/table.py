"""
`Table`, `Column`, `Row` and `Cell`.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from typing import Any, Dict, List, Tuple, Union

import attr
from dateutil.parser import parse

from codaio import err
from codaio.objects.base import (
    CodaObject,
    ColumnFormat,
    Reference,
    TableReference,
    column_format,
    ref,
)
from codaio.values import parse_value, serialize, unwrap_rich_text

#: How long `Cell.value = x` waits for a write to become readable.
_WRITE_SETTLE_TIMEOUT = 60.0


@attr.s(auto_attribs=True, eq=False, repr=False)
class Table(CodaObject):
    name: str = None
    display_column: Dict = attr.ib(default=None, repr=False)
    browser_link: str = attr.ib(default=None, repr=False)
    row_count: int = attr.ib(default=None, repr=False)
    sorts: List = attr.ib(factory=list, repr=False)
    layout: str = attr.ib(repr=False, default=None)
    table_type: str = attr.ib(default=None, repr=False)
    created_at: dt.datetime = attr.ib(
        repr=False, converter=lambda x: parse(x) if x else None, default=None
    )
    updated_at: dt.datetime = attr.ib(
        repr=False, converter=lambda x: parse(x) if x else None, default=None
    )
    columns_storage: List[Column] = attr.ib(factory=list, repr=False)
    filter: Dict = attr.ib(default=None, repr=False)
    parent: Reference = attr.ib(default=None, converter=ref, repr=False)
    parent_table: TableReference = attr.ib(default=None, converter=ref, repr=False)
    view_id: str = attr.ib(default=None, repr=False)

    def meta_to_dict(self, incl_doc=False) -> Dict:
        """
        product a dict of the metadata for the table.  Omits the Document by default.  
        """
        meta_super = super().meta_to_dict(incl_doc)

        meta = {'name': self.name,
                'display_column': self.display_column,
                'browser_link': self.browser_link,
                'row_count': self.row_count,
                'sorts': self.sorts,
                'layout': self.layout,
                'table_type': self.table_type,
                'created_at': self.created_at,
                'updated_at': self.updated_at,
                'filter': self.filter,
                'parent_table': self.parent_table,
                'view_id': self.view_id}

        return meta_super | meta # using https://peps.python.org/pep-0584/

    def __getitem__(self, item):
        """
        table[row_id] -> Row with this id
        table[Row] -> Row with id == Row.id

        table[row_id][column_id] -> Cell from this intersection
        table[row_id][Column] -> Cell from this intersection

        :param item:

        :return:
        """
        if isinstance(item, str):
            return self.get_row_by_id(item)
        elif isinstance(item, Row):
            return self.get_row_by_id(item.id)
        raise ValueError("item type must be in [str, Row]")

    def columns(self, offset: int = None, limit: int = None) -> List[Column]:
        """
        Lists Table columns.

        Columns are stored in self.columns_storage for faster access
        as they tend to change less frequently than rows.

        :param limit: Maximum number of results to return in this query.

        :param offset: An opaque token used to fetch the next page of results.

        :return:
        """
        if not self.columns_storage:
            self.columns_storage = [
                Column.from_json(i, document=self.document, table=self)
                for i in self.document.coda.list_columns(
                    self.document.id, self.id, offset=offset, limit=limit
                )["items"]
            ]
        return self.columns_storage

    def rows(self, offset: int = None, limit: int = None, data: Dict = None) -> List[Row]:
        """
        Returns list of Table rows.

        :param limit: Maximum number of results to return in this query.

        :param offset: An opaque token used to fetch the next page of results.

        :return:
        """
        return [
            Row.from_json(i, document=self.document, table=self)
            for i in self.document.coda.list_rows(
                self.document.id, self.id, offset=offset, limit=limit, data=data
            )["items"]
        ]

    def get_row_by_id(self, row_id: str) -> Row:
        row_js = self.document.coda.get_row(self.document.id, self.id, row_id)
        row = Row.from_json(row_js, document=self.document, table=self)
        return row

    def get_column_by_id(self, column_id) -> Column:
        """
        Gets a Column by id.

        :param column_id: ID of the column. Example: "c-tuVwxYz"

        :return:
        """
        try:
            return self._columns_by_id()[column_id]
        except KeyError:
            raise err.ColumnNotFound(f"No column with id {column_id}")

    def _columns_by_id(self) -> Dict[str, Column]:
        """
        Column lookup by id, built once per set of columns.

        This used to be a linear scan, which would be unremarkable except for how
        often it runs: `Row.cells()` builds a `Cell` per value and looks up a
        column for each, and `Row.to_dict()` calls that once per column. Three
        nested linear passes made turning one wide row into a dict cubic in the
        column count -- around 125,000 operations per row at fifty columns, which
        is minutes of pure Python over a few thousand rows.
        """
        columns = self.columns()
        cache = getattr(self, "_column_index", None)
        if cache is None or cache[0] is not columns or len(cache[1]) != len(columns):
            cache = (columns, {column.id: column for column in columns})
            object.__setattr__(self, "_column_index", cache)
        return cache[1]

    def resolve_column(self, column: Union[str, Column]) -> Column:
        """
        Turn a `Column`, a column id, or a column name into a `Column`.

        Raises rather than passing an unrecognised string through to the API.
        The API accepts an id, a URL or a name wherever a column is named, so a
        typo in a name is otherwise indistinguishable from a name that happens
        not to match anything -- and the failure is silent: a query against a
        column that does not exist comes back empty rather than complaining.

        A URL is passed through unchecked, since it is not something codaio can
        resolve locally.

        :param column: a `Column`, a column id, or a column name.
        """
        if isinstance(column, Column):
            return column
        if not isinstance(column, str):
            raise err.ColumnNotFound(
                f"expected a Column, a column id or a column name, got {column!r}"
            )
        if "://" in column:
            return column

        by_id = self._columns_by_id()
        if column in by_id:
            return by_id[column]

        matches = [c for c in self.columns() if c.name == column]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise err.AmbiguousName(
                f"more than one column is named {column!r} in table {self.name!r}; "
                f"use the column id instead"
            )
        raise err.ColumnNotFound(
            f"no column with id or name {column!r} in table {self.name!r}. "
            f"Available: {sorted(c.name for c in self.columns())}"
        )

    def get_column_by_name(self, column_name) -> Column:
        """
        Gets a Column by id.

        :param column_name: Name of the column. Discouraged in case using column_id is possible.
            Example: "Column 1"

        :return:
        """
        res = list(filter(lambda x: x.name == column_name, self.columns()))
        if not res:
            raise err.ColumnNotFound(f"No column with name: {column_name}")
        if len(res) > 1:
            raise err.AmbiguousName(
                "More than 1 column found. Try using ID instead of Name"
            )
        return res[0]

    def find_row_by_column_name_and_value(
        self, column_name: str, value: Any
    ) -> List[Row]:
        """
        Finds rows by a value in column specified by name (discouraged).

        :param column_name:  Name of the column.

        :param value: Search value.

        :return:
        """
        # Resolve first: a query naming a column that does not exist comes back
        # empty, which is indistinguishable from "no row matched".
        self.resolve_column(column_name)
        r = self.document.coda.list_rows(
            self.document.id, self.id, query=f'"{column_name}":{json.dumps(value)}'
        )
        if not r.get("items"):
            return []
        return [
            Row.from_json(i, document=self.document, table=self)
            for i in r["items"]
        ]

    def find_row_by_column_id_and_value(self, column_id, value) -> List[Row]:
        """
        Finds rows by a value in column specified by id.

        :param column_id: ID of the column.

        :param value: Search value.

        :return:
        """
        r = self.document.coda.list_rows(
            self.document.id, self.id, query=f"{column_id}:{json.dumps(value)}"
        )
        if not r.get("items"):
            return []
        return [
            Row.from_json(i, document=self.document, table=self)
            for i in r["items"]
        ]

    def upsert_row(
        self, cells: List[Cell], key_columns: List[Union[str, Column]] = None
    ) -> Dict:
        """
        Upsert a Table row using a list of `Cell` objects optionally updating existing rows.

        :param cells: list of `Cell` objects.
        :param key_columns: list of `Column` objects, column IDs, URLs, or names
            specifying columns to be used as upsert keys.
        """

        return self.upsert_rows([cells], key_columns)

    def upsert_rows(
        self,
        rows: List[List[Cell]],
        key_columns: List[Union[str, Column]] = None,
    ) -> Dict:
        """
        Upsert multiple Table rows optionally updating existing rows.

        Works similar to Table.upsert_row() but uses 1 POST request for multiple rows.
        Input is a list of lists of Cells.

        :param rows: list of lists of `Cell` objects, one list for each row.
        :param key_columns: list of `Column` objects, column IDs, URLs, or names
            specifying columns to be used as upsert keys.
        """
        data = {
            "rows": [
                {
                    "cells": [
                        {"column": cell.column_id_or_name, "value": cell.value}
                        for cell in row
                    ]
                }
                for row in rows
            ]
        }

        if key_columns:
            if not isinstance(key_columns, list):
                raise err.ColumnNotFound(
                    f"key_columns parameter '{key_columns}' is not a list."
                )

            data["keyColumns"] = []

            for key_column in key_columns:
                if isinstance(key_column, Column):
                    data["keyColumns"].append(key_column.id)
                elif isinstance(key_column, str):
                    data["keyColumns"].append(key_column)
                else:
                    raise err.ColumnNotFound(
                        f"Invalid parameter: '{key_column}' in key_columns."
                    )

        return self.document.coda.upsert_row(self.document.id, self.id, data)

    def update_row(self, row: Union[str, Row], cells: List[Cell]) -> Dict:
        """
        Updates row with values according to list in cells.

        :param row: a str ROW_ID or an instance of class Row
        :param cells: list of `Cell` objects.
        """
        if isinstance(row, Row):
            row_id = row.id
        elif isinstance(row, str):
            row_id = row
        else:
            raise TypeError("row must be str ROW_ID or an instance of Row")

        data = {
            "row": {
                "cells": [
                    {"column": cell.column_id_or_name, "value": cell.value}
                    for cell in cells
                ]
            }
        }

        return self.document.coda.update_row(self.document.id, self.id, row_id, data)

    def delete_row_by_id(self, row_id: str):
        """
        Deletes row by id.

        :param row_id: ID of the row to delete.
        """
        return self.document.coda.delete_row(self.document.id, self.id, row_id)

    def delete_row(self, row: Row) -> Dict:
        """
        Delete row.

        :param row: a `Row` object to delete.
        """

        return self.delete_row_by_id(row.id)

    def to_dict(self) -> List[Dict]:
        """
        Returns entire table as list of dicts. Intended for use with pandas:

        pd.DataFrame(table.to_dict())

        The dicts are ragged: a row omits any column it carries no value for,
        rather than inventing one. `DataFrame` unions the keys, so every column
        still appears, and an absent one arrives as `NaN` while a cell that is
        genuinely empty arrives as the empty value the API sent. Filling `None`
        here would collapse those two into the same thing.
        """
        return [row.to_dict() for row in self.rows()]


@attr.s(auto_attribs=True, eq=False, repr=False)
class Column(CodaObject):
    name: str = None
    table: Table = attr.ib(default=None, repr=False)
    display: bool = attr.ib(default=None, repr=False)
    calculated: bool = attr.ib(default=False)
    formula: str = attr.ib(default=None, repr=False)
    default_value: str = attr.ib(default=None, repr=False)
    # Restored. This is the column's *type* -- `format.type` is "text", "canvas",
    # "person", "image" and so on -- and it was previously discarded on arrival,
    # so the only way to find out what a column held was to inspect a row that
    # happened to have a value in it.
    format: ColumnFormat = attr.ib(default=None, converter=column_format, repr=False)
    parent: TableReference = attr.ib(default=None, converter=ref, repr=False)

    def meta_to_dict(self, incl_table=False) -> Dict:
        """
        product a dict of the metadata for the column.  Omits the table by default.  
        """
        meta_super = super().meta_to_dict()

        meta = {'name': self.name,
                'display': self.display,
                'calculated': self.calculated,
                'formula': self.formula,
                'default_value': self.default_value}

        if incl_table:
            meta['table'] = self.table

        return meta_super | meta # using https://peps.python.org/pep-0584/


@attr.s(auto_attribs=True, eq=False, repr=False)
class Row(CodaObject):
    name: str = None
    index: int = None
    created_at: dt.datetime = attr.ib(
        default=None, converter=lambda x: parse(x) if x else None, repr=False
    )
    updated_at: dt.datetime = attr.ib(
        default=None, converter=lambda x: parse(x) if x else None, repr=False
    )
    values: Tuple[Tuple] = attr.ib(
        default=(),
        converter=lambda x: tuple(x.items()) if isinstance(x, dict) else tuple(x or ()),
        repr=False,
    )
    table: Table = attr.ib(default=None, repr=False)
    browser_link: str = attr.ib(default=None, repr=False)
    parent: TableReference = attr.ib(default=None, converter=ref, repr=False)


    def meta_to_dict(self, incl_table=False) -> Dict:
        """
        product a dict of the metadata for the row.  Omits the table by default.  
        Does not include `values`, cuz those are not metadata, but the data itself.
        """
        meta_super = super().meta_to_dict()

        meta = {'name':self.name,
                'created_at':self.created_at,
                'index': self.index,
                'updated_at': self.updated_at,
                'browser_link': self.browser_link
                } 

        if incl_table:
            meta['table'] = self.table

        return meta_super | meta # using https://peps.python.org/pep-0584/


    def columns(self):
        return self.table.columns()

    def refresh(self):
        new_data = self.table.document.coda.get_row(
            self.table.document.id, self.table.id, self.id
        )
        self.values = tuple([(k, v) for k, v in new_data["values"].items()])
        return self

    def cells(self) -> List[Cell]:
        return [
            Cell(column=self.table.get_column_by_id(i[0]), value_storage=i[1], row=self)
            for i in self.values
        ]

    def delete(self):
        """
        Delete row.

        :return:
        """
        return self.table.delete_row(self)

    def get_cell_by_column_id(self, column_id: str) -> Cell:
        try:
            return next(filter(lambda x: x.column.id == column_id, self.cells()))
        except StopIteration:
            raise KeyError("Column not found")

    def __getitem__(self, item) -> Cell:
        if isinstance(item, Column):
            return self.get_cell_by_column_id(item.id)
        elif isinstance(item, str):
            try:
                return self.get_cell_by_column_id(item)
            except KeyError:
                pass
            column = self.table.get_column_by_name(item)
            found_by_name = self.get_cell_by_column_id(column.id)
            if found_by_name:
                return found_by_name

        raise KeyError(f"Invalid column_id: {item}")

    def __setitem__(self, item, value) -> Cell:
        cell = self.__getitem__(item)
        data = {"row": {"cells": [{"column": cell.column.id, "value": value}]}}
        self.document.coda.update_row(
            self.document.id, self.table.id, self.id, data=data
        )
        cell.value_storage = value
        return cell

    def to_dict(self) -> Dict:
        """
        Returns a row as a dictionary keyed by column name.

        Only columns this row actually carries a value for appear. A value is
        never invented for one that is absent: a row can legitimately be missing
        a column -- `visibleOnly=true` is documented as returning only visible
        rows *and columns*, and a cached column list can outlive a schema change
        -- and recording `None` for those would be indistinguishable from a cell
        that is genuinely empty. For a stored copy that difference matters.

        Raises if the row shares no column at all with its table, which is not a
        partial row but a mismatched one. The usual cause is `useColumnNames`:
        `values` is then keyed by name, every id lookup misses, and the result
        would otherwise be a full set of keys with every value silently dropped.

        :return:
        """
        values = dict(self.values)
        if not values:
            return {}

        by_id = self._column_ids()
        if by_id and not (values.keys() & by_id.keys()):
            raise err.ColumnNotFound(
                f"row {self.id!r} shares no column with table {self.table.name!r}. "
                f"Its values are keyed by {sorted(values)[:3]}..., but the table's "
                f"columns are {sorted(by_id)[:3]}.... If these rows were fetched "
                f"with useColumnNames, the values are keyed by name and cannot be "
                f"matched to columns by id."
            )
        return {
            column.name: values[column.id]
            for column in self.columns()
            if column.id in values
        }

    def _column_ids(self) -> Dict:
        return self.table._columns_by_id()



@attr.s(auto_attribs=True, hash=True, repr=False)
class Cell:
    """
    One column's value on one row.

    `value` is the typed reading of the cell: a scalar comes back as a scalar,
    while an image, person, link, currency or row reference comes back as the
    matching class from :mod:`codaio.values`. `raw_value` is what the API
    actually sent, untouched.
    """

    column: Union[str, Column]
    value_storage: Any
    row: Row = attr.ib(default=None)

    @property
    def name(self):
        return self.column.name if isinstance(self.column, Column) else self.column

    @property
    def table(self):
        return self.row.table

    @property
    def document(self):
        return self.table.document

    def __repr__(self):
        row = getattr(self.row, "name", None)
        return f"Cell(column={self.name}, row={row}, value={self.value!r})"

    @property
    def raw_value(self):
        """Exactly what the API sent, with no interpretation."""
        return self.value_storage

    @property
    def value(self):
        """
        The cell's value, typed.

        Under `valueFormat=rich` a structured cell arrives as JSON-LD; this is
        that turned into an :class:`~codaio.values.ImageValue`,
        :class:`~codaio.values.PersonValue` and so on. Scalars are returned as
        they are, and a `@type` codaio does not know becomes an
        :class:`~codaio.values.UnknownValue` rather than an error.
        """
        return parse_value(self.value_storage)

    @property
    def markdown(self):
        """
        A rich text value as the API sent it -- Markdown, fence and all.

        Text read with `valueFormat=rich` comes back as Markdown, and a value
        with no formatting is wrapped in triple backticks so that it round trips.
        """
        return self.value_storage

    @property
    def text(self):
        """
        A rich text value with the ``` fence stripped.

        Lossy on purpose, and never applied automatically: a cell whose Markdown
        genuinely is a fenced code block cannot be told apart from a plain string
        that was wrapped. Use it when you want text rather than Markdown.
        """
        return unwrap_rich_text(self.value_storage)

    @property
    def column_id_or_name(self):
        if isinstance(self.column, Column):
            return self.column.id
        elif isinstance(self.column, str):
            return self.column

    @value.setter
    def value(self, value):
        data = {
            "row": {
                "cells": [
                    {"column": self.column_id_or_name, "value": serialize(value)}
                ]
            }
        }
        self.document.coda.update_row(
            self.document.id, self.table.id, self.row.id, data=data
        )
        self.value_storage = serialize(value)

        # Writes are applied asynchronously, so the value is not readable back
        # immediately. This waits for it, but only for a while: the loop used to
        # have no bound at all, and it compares what the API stored rather than
        # what was sent, because Coda coerces values to the column's format
        # ("$12.34" becomes 12.34, dates are reformatted) and an exact match may
        # never arrive. Phase-appropriate stopgap: `mutationStatus` is the right
        # primitive and replaces this.
        deadline = time.monotonic() + _WRITE_SETTLE_TIMEOUT
        while time.monotonic() < deadline:
            self.row.refresh()
            settled = self.row.get_cell_by_column_id(self.column_id_or_name)
            if settled.raw_value == self.value_storage:
                return
            time.sleep(0.3)
        raise err.MutationTimeout(
            f"wrote {self.name!r} on row {self.row.id!r} but the new value was "
            f"not readable back within {_WRITE_SETTLE_TIMEOUT:g}s. The edit was "
            f"accepted and may still be applied; Coda also coerces values to the "
            f"column's format, so what it stores may differ from what was sent."
        )
