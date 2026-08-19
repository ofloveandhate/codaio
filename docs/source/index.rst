.. codaio documentation master file, created by
   sphinx-quickstart on Thu Aug 29 12:12:19 2019.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

`codaio` documentation
==================================
Python wrapper for `coda.io <https://coda.io/developers/apis/v1beta1>`_ API

Project home: https://github.com/blasterai/codaio

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   testing

Credentials
-----------

.. automodule:: codaio.credentials
   :members:

The client
----------

.. autoclass:: codaio.Coda
   :members:

.. autoclass:: codaio.Document
   :members:

.. autoclass:: codaio.Folder
   :members:

Pages
-----

.. autoclass:: codaio.Page
   :members:

.. autoclass:: codaio.PageTree
   :members:

.. autoclass:: codaio.PageExport
   :members:

.. autoclass:: codaio.ContentItem
   :members:

.. autoclass:: codaio.CanvasContent
   :members:

.. autoclass:: codaio.EmbedContent
   :members:

.. autoclass:: codaio.SyncPageContent
   :members:

Tables, rows and cells
----------------------

.. autoclass:: codaio.Table
   :members:

.. autoclass:: codaio.Column
   :members:

.. autoclass:: codaio.Row
   :members:

.. autoclass:: codaio.Cell
   :members:

Writes
------

.. autoclass:: codaio.Mutation
   :members:

.. autoclass:: codaio.MutationGroup
   :members:

Sharing
-------

.. autoclass:: codaio.Permission
   :members:

.. autoclass:: codaio.Principal
   :members:

.. autoclass:: codaio.AccessType
   :members:

.. autoclass:: codaio.PrincipalType
   :members:

.. autoclass:: codaio.AclMetadata
   :members:

.. autoclass:: codaio.AclSettings
   :members:

Formulas and controls
---------------------

.. autoclass:: codaio.Formula
   :members:

.. autoclass:: codaio.Control
   :members:

Cell values
-----------

.. automodule:: codaio.values
   :members:
   :exclude-members: CodaValue

.. autoclass:: codaio.CodaValue
   :members:

The object model's base
-----------------------

.. autoclass:: codaio.CodaObject
   :members:

.. autoclass:: codaio.Reference
   :members:

.. autoclass:: codaio.ColumnFormat
   :members:

HTTP, retries and errors
------------------------

.. automodule:: codaio.http
   :members:
   :exclude-members: Idempotency

.. autoclass:: codaio.http.Idempotency
   :members:

.. automodule:: codaio.err
   :members:
   :show-inheritance:


Indices and tables
==================

* :ref:`genindex`
* :ref:`search`
