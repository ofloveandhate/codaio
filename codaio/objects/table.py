"""
`Table`, `Column`, `Row` and `Cell`.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from typing import TYPE_CHECKING, Any, Dict, List, Tuple, Union

import attr
from dateutil.parser import parse

from codaio import err
from codaio.objects.base import CodaObject

if TYPE_CHECKING:  # pragma: no cover
    from codaio.objects.document import Document


@attr.s(auto_attribs=True, hash=True)
class Table(CodaObject):
    name: str
    document: Document = attr.ib(repr=False)
    display_column: Dict = attr.ib(default=None, repr=False)
    browser_link: str = attr.ib(default=None, repr=False)
    row_count: int = attr.ib(default=None, repr=False)
    sorts: List = attr.ib(default=[], repr=False)
    layout: str = attr.ib(repr=False, default=None)
    table_type: str = attr.ib(default=None, repr=False)
    created_at: dt.datetime = attr.ib(
        repr=False, converter=lambda x: parse(x) if x else None, default=None
    )
    updated_at: dt.datetime = attr.ib(
        repr=False, converter=lambda x: parse(x) if x else None, default=None
    )
    columns_storage: List[Column] = attr.ib(default=[], repr=False)
    filter: Dict = attr.ib(default=None, repr=False)
    parent_table: Table = attr.ib(default=None, repr=False)
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
                Column.from_json({**i, "table": self}, document=self.document)
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
            Row.from_json({"table": self, **i}, document=self.document)
            for i in self.document.coda.list_rows(
                self.document.id, self.id, offset=offset, limit=limit, data=data
            )["items"]
        ]

    def get_row_by_id(self, row_id: str) -> Row:
        row_js = self.document.coda.get_row(self.document.id, self.id, row_id)
        row = Row.from_json({**row_js, "table": self}, document=self.document)
        return row

    def get_column_by_id(self, column_id) -> Column:
        """
        Gets a Column by id.

        :param column_id: ID of the column. Example: "c-tuVwxYz"

        :return:
        """
        try:
            return next(filter(lambda x: x.id == column_id, self.columns()))
        except StopIteration:
            raise err.ColumnNotFound(f"No column with id {column_id}")

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
        r = self.document.coda.list_rows(
            self.document.id, self.id, query=f'"{column_name}":{json.dumps(value)}'
        )
        if not r.get("items"):
            return []
        return [
            Row.from_json({**i, "table": self}, document=self.document)
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
            Row.from_json({**i, "table": self}, document=self.document)
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
        """
        return [row.to_dict() for row in self.rows()]


@attr.s(auto_attribs=True, hash=True)
class Column(CodaObject):
    name: str
    table: Table = attr.ib(repr=False)
    display: bool = attr.ib(default=None, repr=False)
    calculated: bool = attr.ib(default=False)
    formula: str = attr.ib(default=None, repr=False)
    default_value: str = attr.ib(default=None, repr=False)

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


@attr.s(auto_attribs=True, hash=True)
class Row(CodaObject):
    name: str
    created_at: dt.datetime = attr.ib(converter=lambda x: parse(x), repr=False)
    index: int
    updated_at: dt.datetime = attr.ib(
        converter=lambda x: parse(x) if x else None, repr=False
    )
    values: Tuple[Tuple] = attr.ib(
        converter=lambda x: tuple([(k, v) for k, v in x.items()]), repr=False
    )
    table: Table = attr.ib(repr=False)
    browser_link: str = attr.ib(default=None, repr=False)


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
        Returns a row as a dictionary.

        :return:
        """
        return {column.name: self[column].value for column in self.columns()}


@attr.s(auto_attribs=True, hash=True, repr=False)
class Cell:
    column: Union[str, Column]
    value_storage: Any
    row: Row = attr.ib(default=None)

    @property
    def name(self):
        return self.column.name

    @property
    def table(self):
        return self.row.table

    @property
    def document(self):
        return self.table.document

    def __repr__(self):
        return (
            f"Cell(column={self.column.name}, row={self.row.name}, value={self.value})"
        )

    @property
    def value(self):
        return self.value_storage

    @property
    def column_id_or_name(self):
        if isinstance(self.column, Column):
            return self.column.id
        elif isinstance(self.column, str):
            return self.column

    @value.setter
    def value(self, value):
        data = {"row": {"cells": [{"column": self.column.id, "value": value}]}}
        self.document.coda.update_row(
            self.document.id, self.table.id, self.row.id, data=data
        )
        self.value_storage = value

        new_value = None
        while new_value != value:
            self.row.refresh()
            new_value = self.row.get_cell_by_column_id(self.column.id).value
            time.sleep(0.3)
