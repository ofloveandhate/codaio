[![Stand With Ukraine](https://raw.githubusercontent.com/vshymanskyy/StandWithUkraine/main/banner-direct.svg)](https://vshymanskyy.github.io/StandWithUkraine)

## Python wrapper for [Coda.io](https://coda.io) API

[![CodaAPI](https://img.shields.io/badge/Coda_API_-V1-green)](https://coda.io/developers/apis/v1beta1)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/codaio)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Documentation Status](https://readthedocs.org/projects/codaio/badge/?version=latest)](https://codaio.readthedocs.io/en/latest/?badge=latest)
[![PyPI](https://img.shields.io/pypi/v/codaio)](https://pypi.org/project/codaio/)
![PyPI - Downloads](https://img.shields.io/pypi/dw/codaio)
[![](https://img.shields.io/badge/Support-Buy_coffee!-Orange)](https://www.buymeacoffee.com/licht1stein)

Don't hesitate to contribute, issues and PRs very welcome! 


### Installation
Install with [poetry](https://python-poetry.org/) (always recommended):

```shell script
poetry add codaio
```

or with `pip`

```shell script
pip install codaio
```


### Quickstart using raw API
Coda class provides a wrapper for all API methods. If API response included a JSON it will be returned as a dictionary from all methods. If it didn't a dictionary `{"status": response.status_code}` will be returned.
If request wasn't successful a `CodaError` will be raised with details of the API error.

```python
from codaio import Coda

coda = Coda('YOUR_API_KEY')

>>> coda.create_doc('My Document')
{'id': 'NEW_DOC_ID', 'type': 'doc', 'href': 'https://coda.io/apis/v1/docs/NEW_DOC_ID', 'browserLink': 'https://coda.io/d/_dNEW_DOC_ID', 'name': 'My Document', 'owner': 'your@email', 'ownerName': 'Your Name', 'createdAt': '2020-09-28T19:32:20.866Z', 'updatedAt': '2020-09-28T19:32:20.924Z'}
```
For full API reference for Coda class see [documentation](https://codaio.readthedocs.io/en/latest/index.html#codaio.Coda)

### Quickstart using codaio objects

`codaio` implements convenient classes to work with Coda documents: `Document`, `Table`, `Row`, `Column` and `Cell`.

```python
from codaio import Coda, Document

# Initialize by providing a coda object directly
coda = Coda('YOUR_API_KEY')

doc = Document('YOUR_DOC_ID', coda=coda)

# Or let the token be resolved from the environment or the OS keyring
doc = Document.from_credentials('YOUR_DOC_ID')

# ...optionally naming which stored token to use
doc = Document.from_credentials('YOUR_DOC_ID', profile='research')

doc.list_tables()

table = doc.get_table('TABLE_ID')
```
#### Fetching a Row
```python
# You can fetch a row by ID
row  = table['ROW_ID']
```

#### Using with Pandas
If you want to load a codaio Table or Row into pandas, you can use the `Table.to_dict()` or `Row.to_dict()` methods:
```python
import pandas as pd

df = pd.DataFrame(table.to_dict())
```

#### Fetching a Cell
```python
# Or fetch a cell by ROW_ID and COLUMN_ID
cell = table['ROW_ID']['COLUMN_ID']  

# This is equivalent to getting item from a row
cell = row['COLUMN_ID']
# or 
cell = row['COLUMN_NAME']  # This should work fine if COLUMN_NAME is unique, otherwise it will raise AmbiguousColumn error
# or use a Column instance
cell = row[column]
```

#### Changing Cell value

```python
row['COLUMN_ID'] = 'foo'
# or
row['Column Name'] = 'foo'
```

#### Iterating over rows
```python
# Iterate over rows using IDs -> delete rows that match a condition
for row in table.rows():
    if row['COLUMN_ID'] == 'foo':
        row.delete()

# Iterate over rows using names -> edit cells in rows that match a condition
for row in table.rows():
    if row['Name'] == 'bar':
        row['Value'] = 'spam'
```

#### Upserting new row
To upsert a new row you can pass a list of cells to `table.upsert_row()`
```python
name_cell = Cell(column='COLUMN_ID', value_storage='new_name')
value_cell = Cell(column='COLUMN_ID', value_storage='new_value')

table.upsert_row([name_cell, value_cell])
```

#### Upserting multiple new rows
Works like upserting one row, except you pass a list of lists to `table.upsert_rows()` (rows, not row)
```python
name_cell_a = Cell(column='COLUMN_ID', value_storage='new_name')
value_cell_a = Cell(column='COLUMN_ID', value_storage='new_value')

name_cell_b = Cell(column='COLUMN_ID', value_storage='new_name')
value_cell_b = Cell(column='COLUMN_ID', value_storage='new_value')

table.upsert_rows([[name_cell_a, value_cell_a], [name_cell_b, value_cell_b]])
```

#### Updating a row
To update a row use `table.update_row(row, cells)`
```python
row = table['ROW_ID']

name_cell_a = Cell(column='COLUMN_ID', value_storage='new_name')
value_cell_a = Cell(column='COLUMN_ID', value_storage='new_value')

table.update_row(row, [name_cell_a, value_cell_a])
```

### Documentation

Since this is silviana's fork of the original `codaio` repo, documentation does NOT live at [readthedocs.io](https://codaio.readthedocs.io/en/latest/index.html).  Sorry.  You will have to build it yourself.



### Authentication

There are options for how to get `codaio` to find the api token it will use.

#### Token lookup order

First one found wins:

| Order | Source | Notes |
|---|---|---|
| 1 | `Coda("YOUR_API_KEY")` | an explicit argument always wins |
| 2 | `CODA_API_KEY_<PROFILE>` | e.g. `CODA_API_KEY_RESEARCH`; skipped for the default profile |
| 3 | `CODA_API_KEY` | |
| 4 | OS keyring | entry `codaio` / `<profile>` |

Environment variables are checked *before* the keyring on purpose: reading a
locked keyring can pop a blocking desktop password prompt, so an already-set
variable should short-circuit that. It also means you can override a stored
token for one run without touching the keyring. `Coda(...).source` tells you
which one was actually used.

If nothing supplies a token you get a `codaio.err.NoApiKey` listing every
mechanism it tried and how to fix it.

To see which backend `keyring` resolves to on a given machine, and whether
`codaio` considers it safe to write to:

```shell script
python -c "from codaio.credentials import keyring_status; print(keyring_status())"
python -m keyring --list-backends     # every candidate, with priorities
```

**On headless servers**, use `CODA_API_KEY` rather than the keyring. With no
Secret Service running, the `keyring` package silently falls back to a
backend that stores tokens base64-encoded rather than encrypted — `codaio`
refuses to *write* to such a backend, and warns if it reads from one.

##### Other variables

* `CODA_API_ENDPOINT` (default `https://coda.io/apis/v1`)
* `CODA_PROFILE` — default profile name
* `CODAIO_DOTENV` — see below

#### Store and retrieve API token using `keyring`

Silviana did some work in 2026 to make it so that a user doesn't have to leave their token in plaintext in env or in a file, where they might get spoiled by a filescanner.  

This method uses the Python `keyring` package.

Store your API token in the OS keyring:

```shell script
python -m keyring set codaio default    # paste your token at the prompt
```

This makes a new entry with service name `codaio`, "username" `default`.  If you use default, then the `codaio` package will pick it up when you construct a `Coda` object.

Then, in Python, just construct a client

```python
from codaio import Coda

coda = Coda()
```

`keyring` is installed as a dependency, and `python -m keyring` works the same
way on Linux, macOS and Windows. It writes to the platform's own secret store:
Secret Service / KWallet on Linux, Keychain on macOS, Credential Manager on
Windows. The plain `keyring` command works too, but `python -m keyring`
guarantees you are using the same environment `codaio` runs in.

The keyring keeps the token **encrypted at rest** — on Linux gnome-keyring
writes it to `~/.local/share/keyrings` encrypted with a key derived from your
login password, so it is not readable with `cat` and not usable if it gets
swept into a backup. It does *not* mean the token never touches disk, and it
is no protection against a process running as you while your session is
unlocked.

##### Several tokens, one per docset

Use a "profile". The profile name is the keyring entry's username, so what
you type is what shows up in your platform's credential manager:

```shell script
python -m keyring set codaio research
python -m keyring set codaio teaching
```

```python
Coda(profile="research")
Document.from_credentials("YOUR_DOC_ID", profile="teaching")
```

`codaio` does not keep its own list of your profile names — that is what the
keyring itself is for. To see what you have stored, use your platform's
credential manager: Seahorse or `secret-tool search service codaio` on Linux,
Keychain Access on macOS, Credential Manager on Windows.



#### Breaking change in 0.8.0

Importing `codaio` used to read a `.env` file from the current working
directory and inject it into the process environment, as a side effect of the
import. That is gone. To opt back in, set `CODAIO_DOTENV=1` (or to a path)
and `pip install 'codaio[dotenv]'`, or call
`codaio.credentials.load_dotenv()` yourself.





### Running the tests

The recommended way of running the test suite is to use [nox](https://nox.thea.codes/en/stable/tutorial.html).

Once `nox`: is installed, just run the following command:
```shell script
nox
```

The nox session will run the test suite against python 3.8 and 3.7. It will also look for linting errors with `flake8`.

You can still invoke `pytest` directly with:
```shell script
poetry run pytest --cov
```

Check out the fixtures if you want to improve the testing process.


#### Contributing

If you are willing to contribute please go ahead, we can use some help!

##### Using CI to deploy to PyPi

When a PR is merged to master the CI will try to deploy to pypi.org using poetry. It will succeed only if the 
version number changed in pyproject.toml. 

To do so use poetry's [version command](https://python-poetry.org/docs/cli/#version). For example:

Bump 0.4.11 to 0.4.12:
```bash
poetry version patch
```

Bump 0.4.11 to 0.5.0:
```bash
poetry version minor
```

Bump 0.4.11 to 1.0.0:
```bash
poetry version major
```
