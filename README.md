# Hardware Database (HDB)

This is a Django implementation of the Hardware Database inspired by *The Component
Database User Guide* (Argonne National Laboratory). The Hardware Database is the
central repository for documenting, organizing, and tracking hardware components
across projects and organizations.

---

## Table of Contents

1. [Overview](#overview)
2. [Project Structure](#project-structure)
3. [Quick Start](#quick-start)
4. [PostgreSQL Setup](#postgresql-setup)
5. [Data Model](#data-model)
   - [Institutions and Locations](#institutions-and-locations)
   - [Domain 1 — Component Catalog](#domain-1--component-catalog)
   - [Domain 2 — Component Inventory](#domain-2--component-inventory)
   - [Domain 3 — Designs](#domain-3--designs)
   - [Cross-Domain: Properties and Logs](#cross-domain-properties-and-logs)
   - [Ownership](#ownership)
6. [Django Admin](#django-admin)
7. [Web UI](#web-ui)
8. [Python Client, CLI, and MCP Server](#python-client-cli-and-mcp-server)
9. [Seed Data](#seed-data)
10. [Schema Diagram](#schema-diagram)
11. [Design Decisions](#design-decisions)

---

## Overview

This database captures three interrelated domains:

| Domain | Purpose |
|--------|---------|
| **Component Catalog** | Reference library of every component *type* — custom-fabricated or commercial — with metadata, drawings, vendors, and properties. |
| **Component Inventory** | Physical instances of catalog items, each with a unique tag, tracked to a specific room, cabinet, or shelf at a specific institution. |
| **Design Library** | Bill-of-Materials groupings: named assemblies of components and sub-assemblies, with hierarchical nesting and installed-instance tracking — plus reusable **Design Templates** for planning an assembly before any of it physically exists. |

A flexible **Properties** system attaches arbitrary typed metadata to any
domain item. A unified **Log** system records maintenance, inspection, and
lifecycle events across all domains.

---

## Project Structure

```
epic-hdb/
├── manage.py
├── pyproject.toml               # Python version, pinned dependencies, packaging
├── uv.lock                      # Reproducible dependency resolution
├── .python-version              # Default Python interpreter for uv
├── hdb_project/                 # Django project (settings, root URLconf)
│   ├── settings.py
│   ├── urls.py
│   ├── asgi.py
│   └── wsgi.py
├── hdb/                         # The "hdb" Django app — all models, views, admin
│   ├── models.py                # All data models
│   ├── admin.py                 # Django admin configuration
│   ├── views_web.py             # Server-rendered web UI views
│   ├── urls_web.py              # Web UI URL routes
│   ├── views.py / serializers.py  # Optional REST API (only if djangorestframework is installed)
│   ├── migrations/
│   │   └── 0001_initial.py
│   ├── management/commands/
│   │   └── seed_hdb.py          # Sample data loader
│   └── templates/cdb/           # Web UI templates — NOTE: this directory is still
│                                 # named "cdb" (pre-dating the project's cdb→hdb
│                                 # rename); the app itself is fully "hdb", only this
│                                 # template subdirectory's name is a known, pending
│                                 # cleanup, not a functional issue.
├── client/                      # hdb_client library, CLI, and MCP server — see client/README.md
│   ├── hdb.py                   # Command-line interface
│   ├── mcp_server.py            # MCP server (for AI assistants / MCP clients)
│   ├── smoke_test.py            # End-to-end test for the MCP server
│   ├── README.md                # Full client/CLI/MCP reference — usage mechanics live here
│   └── hdb_client/              # Programmatic query client (package)
│       ├── client.py            # HDBClient (combined entry point)
│       ├── catalog.py           # CatalogClient
│       ├── inventory.py         # InventoryClient
│       ├── designs.py           # DesignClient (designs + design templates)
│       ├── locations.py         # LocationClient
│       ├── systems.py           # SystemClient
│       ├── access.py            # Ownership/permission checks shared by all writes
│       └── serializers.py       # Plain-dict output shaping (no DRF dependency)
├── data/                        # Sample input files for the client (YAML)
│   ├── new_crystal.yaml
│   └── btof_stave_templates.yaml
└── assets/
    ├── docs/The_Legacy_Component_Database_User_Guide.pdf   # The original ANL inspiration
    └── images/                  # Reference photos used by seed_hdb.py
```

---

## Quick Start

This quick start uses SQLite for local development. For a shared application
or PostgreSQL development database, follow [PostgreSQL Setup](#postgresql-setup)
instead of exporting `DJANGO_DB_TYPE=sqlite` below. The storage directory
requirements apply to both databases.

**Requirements:** [uv](https://docs.astral.sh/uv/getting-started/installation/).
uv manages Python 3.12 and the project's `.venv`. `pyproject.toml` preserves the previously
pinned web app and client dependencies, including Django 6.0.8, Django REST
framework, Gunicorn, PostgreSQL support, and QR-code/Pillow support;
`uv.lock` records the resolved versions and distribution hashes.

> Upgraded from Python 3.10 / Django 5.2 to Python 3.12 / Django 6.0.8 in
> 2026-08. The codebase needed one code change for the jump to Django 6
> (`CheckConstraint`'s `check=` argument was removed in favor of
> `condition=`); everything else carried over unchanged. To set up a
> matching Python 3.12 environment, use `uv sync` as shown below.
> The old upgrade script and
> dependency snapshot in `attic/` are historical, not the current setup.

```bash
uv sync --locked --extra client

# Local development only (production uses /etc/hdb/env):
export DJANGO_DB_TYPE=sqlite
export DJANGO_DEBUG=true

# One-time local setup: create storage directories owned by your account.
# Run this privileged command yourself; do not use it on production.
sudo install -d -m 0755 -o "$(id -un)" -g "$(id -gn)" \
  /var/data/hdb/media /var/data/hdb/static

# Confirm media storage is writable before loading sample images:
test -w /var/data/hdb/media && test -x /var/data/hdb/media

# Apply all migrations (creates db.sqlite3):
uv run --locked --extra client python manage.py migrate

# Load sample ePIC detector data:
uv run --locked --extra client python manage.py seed_hdb

# Start the development server:
uv run --locked --extra client python manage.py runserver

# Web UI:
#   http://127.0.0.1:8000/           (login, then Dashboard)
# Admin interface:
#   http://127.0.0.1:8000/admin/
# Either way, log in as:
#   Username: admin   Password: admin
#   (or: maxim / maxim — also a superuser, see Seed Data below)
```

`seed_hdb` writes sample images to `MEDIA_ROOT`, currently
`/var/data/hdb/media`. A `PermissionError` for `/var/data` means the storage
directory is missing or the account running the command cannot write to it.
Complete the directory setup above before seeding; if the permission check
fails, stop and have an administrator fix ownership or access permissions.
`/var/data/hdb/static` is also needed for `collectstatic`. Do not run
`seed_hdb` as root or grant world-writable permissions to work around this.
Existing media files and subdirectories must also be writable by the app
account. On production, use the Gunicorn service account rather than your
local account, retain httpd read access, and apply the SELinux contexts
described in [CLAUDE.md](CLAUDE.md).

uv installs the project in editable mode, exposing `hdb_client` without setting `PYTHONPATH`.
The `client` extra enables the MCP CLI dependencies; `httpx` for the smoke
test is already included. Use `uv sync --locked` for the web app alone.
Commit `uv.lock` alongside `pyproject.toml`; after changing dependencies,
run `uv lock` and `uv sync --extra client` to update the environment.
For the remaining examples that use bare `python`, prefix commands with
`uv run --locked --extra client`, or activate `.venv` first.

For deployment, see `CLAUDE.md`: use `uv sync --locked --no-editable` with
`UV_PROJECT_ENVIRONMENT` set to the existing service virtualenv. This keeps
Gunicorn's executable path unchanged. Existing Django settings, database
environment variables, and service commands are unchanged. uv sync removes
packages not declared by the project; use a dedicated application environment.

To use the Python client from the Django shell:

```bash
uv run --locked --extra client python manage.py shell
```

```python
from hdb_client import HDBClient
client = HDBClient()

# Find where a component is located (pass a UUID primary key)
client.where_is("5a2c5c0e-479b-4e2f-a7cb-caea37435506")

# Get a full Bill of Materials
client.designs.bom("BEMC tower")
```

See [Python Client, CLI, and MCP Server](#python-client-cli-and-mcp-server)
below for where to go for the complete reference.

---

## PostgreSQL Setup

HDB supports PostgreSQL without application code changes. The locked
dependencies already include `psycopg[binary]`; no separate Python driver
installation is needed. Use a dedicated application database and role, not
the PostgreSQL administrator account.

### 1. Prepare the database server

Have an administrator install and initialize PostgreSQL 14 or newer,
start its service, and install the `psql` and `pg_dump` client tools.
Package names, initialization commands, and service names depend on the OS
and PostgreSQL distribution; RHEL's default package stream may be too old.
A managed PostgreSQL service also works: ask its administrator to provision
the database and role described below.

Connect as a PostgreSQL administrator (for example, `sudo -u postgres psql`
on a local Linux server) and run these commands once for a new database:

```sql
CREATE ROLE hdb_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
SET password_encryption = 'scram-sha-256';
\password hdb_app
CREATE DATABASE hdb OWNER hdb_app;
\connect hdb
GRANT USAGE, CREATE ON SCHEMA public TO hdb_app;
```

`\password` prompts for a password directly in your terminal; do not put
it in SQL files or command history. Database ownership and schema creation
rights allow Django migrations to create and alter tables. If reusing an
existing database, ask its administrator to confirm schema permissions and
ownership of existing HDB tables rather than rerunning the creation commands.

For a local server, the administrator should confirm TCP connections to
`127.0.0.1:5432` are enabled and password authentication is allowed in
`pg_hba.conf`, for example:

```text
host    hdb    hdb_app    127.0.0.1/32    scram-sha-256
```

Rules are matched top-to-bottom; an earlier rule may override this example.
Reload PostgreSQL configuration after approved changes. Do not use `trust`
authentication or expose port 5432 publicly. For a remote database, have
the administrator restrict network access to the application host and
configure verified TLS; set `PGSSLMODE=verify-full` and `PGSSLROOTCERT` to
the server's trusted CA certificate file as required by your provider.

### 2. Configure the application environment

From the repository root, sync the application and select PostgreSQL:

```bash
uv sync --locked --extra client
export DJANGO_DB_TYPE=postgres
export DJANGO_DB_NAME=hdb
export DJANGO_DB_USER=hdb_app
export DJANGO_DB_HOST=127.0.0.1
export DJANGO_DB_PORT=5432

# Enter the role password locally; it is not stored in shell history.
read -r -s -p 'Database password: ' DJANGO_DB_PASSWORD
printf '\n'
export DJANGO_DB_PASSWORD
```

`DJANGO_DB_NAME`, `DJANGO_DB_USER`, and `DJANGO_DB_PASSWORD` are required
in PostgreSQL mode. Host and port default to `127.0.0.1` and `5432`.
**If `DJANGO_DB_TYPE` is unset, HDB silently defaults to SQLite.** Supply
the same environment to the web server, CLI/MCP tools, and management commands;
exports in one terminal do not configure another terminal or systemd.
uv does not automatically load `.env` files: use an explicit environment-loading
step or `uv run --env-file /path/to/private/env` if you manage one locally.
Never commit real passwords or `DJANGO_SECRET_KEY` to git.

### 3. Verify the connection and create the schema

First test the application account independently of Django:

```bash
psql -h "$DJANGO_DB_HOST" -p "$DJANGO_DB_PORT" \
    -U "$DJANGO_DB_USER" -d "$DJANGO_DB_NAME" -W \
    -c 'SELECT current_database(), current_user;'

# Confirm Django actually selected PostgreSQL, without writing records:
uv run --locked --extra client python manage.py shell -c \
    'from django.db import connection; assert connection.vendor == "postgresql", "Wrong database backend"; connection.ensure_connection(); print(connection.vendor, connection.settings_dict["NAME"])'

uv run --locked --extra client python manage.py check
uv run --locked --extra client python manage.py showmigrations hdb
uv run --locked --extra client python manage.py migrate
```

Before migrating an existing database, take a backup and check that
`showmigrations hdb` lists the expected migration chain. Use the committed
migration files; do not delete or regenerate them to switch database backends.

For a fresh local/demo database, prepare writable media storage as described
in [Quick Start](#quick-start), then run:

```bash
uv run --locked --extra client python manage.py seed_hdb
DJANGO_DEBUG=true uv run --locked --extra client python manage.py runserver
```

The seed command creates development accounts with passwords matching their
usernames. **Do not seed a shared or production database with these accounts.**
For a fresh non-demo installation, create your administrator instead:

```bash
uv run --locked --extra client python manage.py createsuperuser
```

Switching backends does not transfer existing SQLite records: `migrate` creates
the PostgreSQL schema, not a copy of users, passwords, inventory, or designs.
If preserving data, stop writes, back up SQLite and media, and plan a tested
Django export/import before switching the running application. Do not seed
the destination before importing existing data. Uploaded files remain in
`/var/data/hdb/media`; changing the database does not move them.

### 4. Backups and troubleshooting

Back up PostgreSQL before migrations and regularly during operation:

```bash
umask 077
pg_dump -h "$DJANGO_DB_HOST" -p "$DJANGO_DB_PORT" \
    -U "$DJANGO_DB_USER" -W --format=custom \
  --file="$HOME/hdb-backup-$(date +%Y%m%d-%H%M%S).dump" "$DJANGO_DB_NAME"
```

Keep dumps outside git, back up media separately, and test restoration into
a separate database using `pg_restore`. A dump does not include uploaded
files or cluster-level roles; document role provisioning separately.

- **Connection refused:** check server status, host/port, listening addresses,
  and firewall rules.
- **Password authentication failed / no pg_hba.conf entry:** confirm the role
  password and the matching authentication rule with the administrator.
- **Permission denied for schema public or a table:** confirm schema access
  and ownership of the tables Django migrations need to alter.
- **Missing tables or unexpected empty data:** verify the backend, database
  name, and environment in the actual web-server process, then check migrations.
- **Permission denied for /var/data:** fix media directory access; this is
  filesystem storage, independent of PostgreSQL.

For the existing RHEL deployment, use the environment and service guidance
below and the host-specific notes in [CLAUDE.md](CLAUDE.md).

---

## Data Model

### Institutions and Locations

**`Institution`** is the top-level geographic anchor, representing a
collaborating lab or facility (e.g. BNL, CUA, UIC). Every location
must belong to an institution, enabling inventory tracking across multiple
sites.

| Field | Description |
|-------|-------------|
| `name` | Full name (unique) |
| `abbreviation` | Short code, e.g. `BNL` |
| `country` / `city` | Site geography |
| `url` | Homepage |

**`Location`** represents a physical place within an institution, organized
in a self-referential hierarchy: Building → Room → Cabinet → Shelf → Other.

| Field | Description |
|-------|-------------|
| `name` | Location name |
| `location_type` | `building`, `room`, `cabinet`, `shelf`, `other` |
| `institution` | FK to owning Institution (required — deleting an Institution with Locations still attached is blocked, not cascaded) |
| `parent` | FK to parent Location (self-referential) |

`Location.full_path()` (used by `__str__`) returns the full slash-separated
path, e.g.: `BNL / Bldg,510A`.

Every `User` also has a **`UserProfile`** with a mandatory `institution` FK —
a user's home institution, used to group "who's at which site" (e.g. the
`users_at_institution()` client method).

---

### Domain 1 — Component Catalog

**`Component`** — one entry per unique component *type*.

| Field | Description |
|-------|-------------|
| `name` | Unique together with `project` |
| `alternate_name` | Optional secondary name |
| `model_number` | Vendor or internal model number |
| `description` | Free-text description |
| `project` | e.g. `ePIC` (default) |
| `technical_system` | FK → `TechnicalSystem` |
| `sources` | M2M → `Source` via `ComponentSource` |
| *(OwnedModel)* | Ownership + timestamps — see [Ownership](#ownership) |

**`TechnicalSystem`** — an engineering subsystem (e.g. `BEMC-CRYSTAL`,
`BTOF-Sensor`). Acts as a chapter in the catalog, and carries its own `group`
FK (a Django `Group`) identifying the team responsible for it —
`ComponentInstance` inherits its `technical_system` from its `Component`
automatically if not set explicitly.

`TechnicalSystem.name` and `Group.name` are independent, unrelated strings —
there's no naming convention or code that ties them together, and nothing
requires a `TechnicalSystem` to be named after its `group` (or vice versa).
This repo's seed data just happens to *look* related because it follows an
informal `<GROUP>-<ROLE>` convention (`BEMC-CRYSTAL`/`BEMC-PM` → group
`BEMC`; `BTOF-Sensor`/`BTOF-Readout` → group `BTOF`; `PFRICH-HRPPD` → group
`PFRICH`) purely for human readability. The actual link between the two
models is only the one nullable FK, `TechnicalSystem.group`, set once at
creation — see [Domain 1](#domain-1--component-catalog) above and the
Django-admin note below for how that field is (and isn't) kept in sync.

**`Source`** — vendor or manufacturer. The `ComponentSource` through-table
adds `part_number`, `cost`, and `role` (`vendor` / `manufacturer` / `both`)
per (component, source) pair.

---

### Domain 2 — Component Inventory

**`ComponentInstance`** — one row per physical item.

| Field | Description |
|-------|-------------|
| `tag` | Human-readable label |
| `serial_number` | Vendor serial number |
| `component` | FK → `Component` (catalog type) — `PROTECT`ed, so a Component with instances can't be deleted out from under them |
| `technical_system` | FK → `TechnicalSystem`, auto-inherited from `component` if left unset |
| `location` | FK → `Location` (where it currently is) |
| *(OwnedModel)* | Ownership + timestamps |

Each instance inherits all catalog-level properties from its parent
`Component`, and may additionally carry its own instance-specific properties
that override the inherited default for the same `(property_type, tag)` pair
(`ComponentInstance.effective_properties()` merges the two).

---

### Domain 3 — Designs

This is the most structurally interesting part of the schema, because it's
actually **two related but distinct models** — mixing up their capabilities
is the easiest way to be surprised by this part of the database. This
section is the authoritative explanation; `client/README.md` covers the
`hdb_client`/CLI *mechanics* for working with both and links back here for
the *why*.

#### `Design` — a real, buildable assembly

| Field | Description |
|-------|-------------|
| `name` | Unique design name |
| `description` | Functional description |
| `project` | e.g. `ePIC` |
| `template` | FK → `DesignTemplate`, nullable — set if this design was instantiated from one |
| `location` | FK → `Location`, nullable — a design lives in exactly one place; placeholder-filling only offers inventory stored there |
| *(OwnedModel)* | Ownership + timestamps |

**`DesignElement`** — one slot within a design.

| Field | Description |
|-------|-------------|
| `element_name` | Unique within the design |
| `component` | FK → `Component`, nullable — set for a leaf element |
| `child_design` | FK → `Design`, nullable — set for a sub-assembly element (**real nesting**, arbitrarily deep) |
| `quantity` | Number of this element required (applies to either kind) |

Exactly one of `component` or `child_design` is set per element
(`element_type()` returns `"COMPONENT"` or `"DESIGN"` based on which).

**`DesignElementInstance`** — a separate join table, *not* a field on
`DesignElement`: one row per physical `ComponentInstance` installed into one
slot. A `DesignElement` with `quantity=4` (e.g. "SiPM x 4") can accept up to
four of these, each pointing at a distinct instance. A `ComponentInstance`
can be installed in at most one slot anywhere in the whole database at a
time — enforced by a database-level `unique` constraint on `instance`, not
just a UI check.

#### `DesignTemplate` — a reusable blueprint

| Field | Description |
|-------|-------------|
| `name` | Globally unique |
| `description` | Free-text |
| `project` | e.g. `ePIC` |
| `product_component` | FK → `Component`, nullable — the catalog entry for one physically completed instance of this template's assembly (e.g. the "BTOF Stavelet" template's `product_component` is the "BTOF Stavelet" `Component`), so it can be tracked as inventory once built. Independent of nesting — see below. |
| *(OwnedModel)* | Ownership + timestamps |
| `nesting_levels` | Computed property, not a DB column — depth of nested sub-templates beneath this one (1 = flat, every placeholder a leaf component) |

**`DesignTemplateElement`** — one placeholder line.

| Field | Description |
|-------|-------------|
| `element_name` | Unique within the template |
| `component` | FK → `Component`, nullable — set for a leaf placeholder |
| `child_template` | FK → `DesignTemplate`, nullable — set for a nested sub-assembly placeholder once resolved (**real nesting**, mirrors `child_design`) |
| `child_template_name` | The nested sub-assembly's name as uploaded, always set alongside a template-type placeholder — `child_template` may still be null if that name hasn't been uploaded yet (see "Asynchronous, order-independent loading" below) |
| `quantity` | Number needed (applies to either kind) |

Exactly one of `component` or a template reference (`child_template`/
`child_template_name`) is set per element (`element_type()` returns
`"COMPONENT"` or `"TEMPLATE"` based on which — the latter regardless of
whether the reference has resolved yet), enforced three ways:
`DesignTemplateElement.clean()`/`save()` (a friendly `ValidationError` on
every write path — the web UI, `hdb_client`, direct ORM use), and a
database `CheckConstraint` as a backstop against anything that bypasses
`save()` (`bulk_create`, raw SQL).

#### Asynchronous, order-independent loading

A `child_template` reference doesn't have to already exist when its
parent is uploaded. YAML templates are loaded via `hdb_client`/the `hdb
load-template` CLI command (below), and that loading is asynchronous: a
parent's file can be uploaded before the sub-template it names has been
uploaded at all — from any file, by any uploader, in any order. When the
named template doesn't exist yet, the placeholder is created *pending*
(`child_template` null, `child_template_name` recording the intended
name) rather than failing, and is linked automatically — with the same
cycle/project/owner_group validation an already-existing reference gets
immediately — the moment a template with that name is eventually loaded
(`resolve_pending_template_references()`, called after every upload).

`DesignTemplate.is_complete()` is true only once every placeholder
anywhere beneath a template has resolved; **a Design can never be
instantiated from an incomplete template**, enforced server-side, not
just hidden in the UI. The template detail page shows an "Incomplete"
banner naming exactly what's still pending; the Design Templates list
page flags and can filter to incomplete templates the same way.

#### Templates nest, structurally identically to Designs

`DesignTemplateElement.child_template` mirrors `DesignElement.child_design`
exactly — real, schema-native, unlimited-depth nesting. The two models are
now structurally parallel:

| | `Design` | `DesignTemplate` |
|---|---|---|
| Represents | A real assembly, instantiated or in progress | A reusable pattern, not a design itself |
| Element model | `DesignElement` | `DesignTemplateElement` |
| An element can point at | A `Component` **or** another `Design` | A `Component` **or** another `DesignTemplate` |
| Nests its own kind? | Yes, natively (`child_design`) | Yes, natively (`child_template`) |
| Physical tracking | `DesignElementInstance` pins real `ComponentInstance`s into slots | None on the placeholder itself — but `product_component` lets the *template as a whole* have a trackable product |
| Can reference itself, at any depth? | No restriction in the schema (not applicable — nothing instantiates into itself) | **Never** — `DesignTemplateElement.save()` rejects a `child_template` link that would let a template (in)directly nest itself |
| Can nest a template from a different project or owner_group? | Not applicable | **Never** — `child_template` must share both `project` and `owner_group` with its parent template (`DesignTemplate.can_nest`) |
| Locking | None of its own | **Locked** once any `Design` references it as `template` |
| Who can delete it | Any `owner_group` member, or a superuser | **Superuser only**, only while unlocked, and only while not nested inside another template (`child_template` is `PROTECT`ed) |

```
DesignTemplate "BTOF Stave"
  └─ element "Half-Stave Assemblies"  x144  → child_template: DesignTemplate "BTOF Half-Stave"
       ├─ element "Stavelet Modules"  x8  → child_template: DesignTemplate "BTOF Stavelet"
       │    ├─ "AC-LGAD Sensors"    x4 → component: "AC-LGAD Sensor"        (leaf)
       │    ├─ "FCFD Readout ASICs" x4 → component: "FCFDv2 Readout"        (leaf)
       │    ├─ "Interposer Boards"  x4 → component: "BTOF Interposer Board" (leaf)
       │    └─ "Flex Cable (FPC)"   x1 → component: "BTOF Flex Cable (FPC)" (leaf)
       │    (product_component: "BTOF Stavelet" -- one built+QA'd stavelet is trackable inventory)
       ├─ "Peripheral Board"      x1 → component: "BTOF Peripheral Board"       (leaf)
       └─ "Cooling Pipe Segment"  x1 → component: "BTOF Cooling Pipe Segment"   (leaf)
       (product_component: "BTOF Half-Stave")
```

Beyond the cycle rule, a `child_template` must also share the parent
template's `project` and `owner_group` (`DesignTemplate.can_nest`,
enforced the same three-layer way — the "Add Placeholder" dropdown only
ever *offers* in-scope templates, and `DesignTemplateElement.clean()` is
the actual enforcement against a direct POST or `hdb_client` call).
Nesting a sub-assembly from an unrelated project has no physical meaning,
and the group that owns a template is responsible for everything
physically built into it at every level — a BTOF assembly can't be built
out of a BEMC group's sub-assembly. There's no DB-level backstop for
either check (both compare across rows, which a `CheckConstraint` can't
do), same as the cycle rule.

`product_component` is what preserves the earlier flat-template scheme's
one real advantage — every intermediate assembly is independently
serial-trackable as its own `ComponentInstance` once actually built — while
keeping it a deliberate, explicit choice per template rather than an
implicit side effect of a naming coincidence. A template with no
`product_component` set is purely a nesting/BOM concept with nothing to
physically instantiate on its own (e.g. a top-level "BTOF Stave" template
might have none, if a whole stave is never itself installed into anything
larger and tracked as one serialized unit).

#### Instantiation and locking

A `DesignTemplate` starts **unlocked**: any member of its `owner_group` (or
a superuser) can add, remove, or resize its placeholders. Instantiating it —
via "New from Template" on the Designs page, or programmatically — creates a
real `Design` with `template` set and one `DesignElement` per placeholder.
For a `TEMPLATE`-type placeholder, instantiation **recurses**: a child
`Design` is auto-instantiated from that nested template the same way, and
the new `DesignElement`'s `child_design` points at it — so a multi-level
template produces a fully wired-up multi-level `Design` tree on the first
instantiation, not empty slots to fill in by hand later. `quantity` on a
`TEMPLATE`-type element means what it already means for `child_design` in a
hand-built `Design`: one exemplar child counted that many times when a BOM
is walked (`DesignBOMView.walk()` / `_build_bom()`), not that many separate
`Design` rows.

The instant **any** `Design` exists with that `template`, the template
**locks**: every editing and deletion control disappears, checked
server-side (`template.designs.exists()`), not just hidden in the UI. This
guarantees "instantiated from BEMC tower" always means the same bill of
placeholders, no matter when you look. The lock isn't a stored flag, so it's
automatically reversible — delete the last `Design` built from a template
and it becomes editable (and deletable) again immediately. `Design` deletion
itself carries none of these restrictions.

**Deleting a template** is further restricted to **superusers only** (even
more restrictive than editing, which any `owner_group` member can do), and
is blocked while it's referenced as another template's `child_template`
placeholder (`PROTECT`ed at the database level — remove the placeholder, or
delete the referencing template, first). See `client/README.md` for the
exact CLI/API/web-UI mechanics and real output.

---

### Cross-Domain: Properties and Logs

**`PropertyValue`** — flexible key/value metadata attachable to any domain
item. A single table serves all four targets via optional FK columns.

| Field | Description |
|-------|-------------|
| `property_type` | FK → `PropertyType` (predefined, admin-extensible) |
| `tag` | Optional label for this value |
| `value` | String value |
| `file` | Optional uploaded file (for `document`/`image` handler types) |
| `units` | Optional unit string |
| `is_dynamic` | True if value varies per instance (e.g. an inspection result) |
| `component` / `component_instance` / `design` / `design_element` | Target FK — exactly one is set |

**`PropertyType`** defines the schema for a property. The `handler` field
specifies typed behaviour:

| Handler | Behaviour |
|---------|-----------|
| `pdmlink` | Integrates with PDMLink engineering drawing system |
| `component_design` | Links to a component design document |
| `traveler_template` | Links to an eTraveler inspection template |
| `traveler_instance` | Links to a filled-out eTraveler form |
| `document` | Attach any file |
| `image` | Attach a viewable image (shown in gallery) |
| `http_link` | Store a URL |
| `currency` | Numeric value with `#.##` formatting |
| `boolean` | True/false checkbox |
| `date` | Date picker |

**`LogEntry`** — lifecycle event log, attachable to Component,
ComponentInstance, or Design.

| Field | Description |
|-------|-------------|
| `topic` | `general` (blank), `installation`, `inventory`, `design`, `maintenance`, `inspection`, `repair`, `decommission`, `other` |
| `entry` | Free-text log message |
| `attachment` | Optional file upload |
| `logged_by` | FK → Django User |
| `timestamp` | Auto-set on creation |

There's a `/logs/` page (`log_list`) that browses and filters all of them,
and each Component/Instance/Design detail page shows its own via
`related_name="log_entries"`. As of this writing there's no "Add Log Entry"
form anywhere in the web UI — `log_list` is read-only, and a handful of
actions (e.g. deleting an inventory item) create a `LogEntry` automatically
as a side effect — so a free-form entry currently has to go through the
Django admin or direct ORM/client access.

---

### Ownership

Every domain model that can be written to (`Component`, `ComponentInstance`,
`Design`, `DesignTemplate`) inherits from the abstract `OwnedModel`, which
supplies:

| Field | Description |
|-------|-------------|
| `owner_user` | Individual owner (Django User) |
| `owner_group` | Owning group (Django `Group`) |
| `group_writeable` | Whether group members can edit |
| `created_by` / `created_on` | Creation audit trail |
| `modified_by` / `modified_on` | Modification audit trail |

**`Group`** — a named team or subsystem (this repo's seed data uses `BEMC`,
`BTOF`, and `PFRICH`). A user may write to a row if they're its
`owner_user`, they're Django staff/superuser, or they're a member of
`owner_group` *and* `group_writeable` is `True` — see
`client/hdb_client/access.py` for the exact rule, shared by every write
path (web UI, client library, CLI, and MCP server alike).

---

## Django Admin

The admin interface at `/admin/` provides full CRUD access to every model:

- **Institution** pages include an inline table of all their locations.
- **Component** pages include inline tables for sources, properties,
  inventory instances, and log entries, plus a computed instance count.
- **ComponentInstance** pages include inline properties and logs, plus an
  Institution column in the list view for quick site identification.
- **TechnicalSystem** and **Source** have their own simple list/search pages.
  A `TechnicalSystem`'s `group` field is a plain dropdown of existing
  `Group`s (no free-text entry), but the "+" icon next to it opens a popup
  to create a new `Group` on the spot without leaving the page.
- **DesignTemplate** pages include an inline table of placeholders
  (`DesignTemplateElement`) and a computed placeholder count.
- **Design** pages include inline design elements, properties, and logs,
  plus a computed element count.
- **DesignElement** pages include inline properties and inline installed
  instances (`DesignElementInstance`).
- **User** admin is customized to show institution and group columns, with
  an inline `UserProfile` (institution) editor.
- All list views support filtering and search.

### Users, groups, and external authentication

The main application's Users page is a read-only directory. Add users and
groups through `/admin/` under Authentication and Authorization. Superusers
have access automatically; other administrators need active staff accounts
and the relevant user/group permissions.

On the Add user form, both password fields are optional. Leave both blank
to create an account with local password authentication disabled. This sets
Django's unusable-password state, not an empty or default password. If either
password field is filled, both are required and Django's password matching
and strength validation still apply. Existing users' passwords are unchanged.

An unusable local password allows the LDAP backend to authenticate the
account, but **does not enable LDAP automatically**. Configure LDAP as
described below; without an enabled configuration, only local-password
accounts can log in. LDAP setup is separate from PostgreSQL and works with
either supported database.

### LDAP configuration

HDB looks for `/etc/hdb/ldap.conf`, a conventional system configuration
location outside the checkout. Override the path with `DJANGO_LDAP_CONFIG`
for development or another deployment layout. Configuration is read at
startup: restart the application after changing the file. A missing default
file leaves LDAP disabled; a missing explicitly selected file or invalid
configuration causes startup to fail rather than silently ignoring it.

The credential-free [deploy/ldap.conf.example](deploy/ldap.conf.example)
contains the BNL configuration verified from this development host:

```ini
[ldap]
enabled = true
server_url = ldap://addressbook.bnl.gov:389
search_base = DC=bnl,DC=gov
username_attribute = sAMAccountName
ca_cert_file = /etc/pki/tls/certs/ca-bundle.crt
timeout = 10
```

An `ldap://` URL always requires StartTLS before any bind; an `ldaps://`
URL uses TLS directly. Certificate and hostname validation are mandatory,
and failures never fall back to unencrypted LDAP. On RHEL, the CA bundle
above verified this endpoint. Other systems must use their appropriate
trusted CA bundle; an empty `ca_cert_file` uses Python's default trust store.
Conda installations may have a different or incomplete trust store, so an
explicit bundle is recommended. `timeout` is in seconds (1 through 60).

Lookups are anonymous; there is no service bind account or join credential
in this configuration. The backend searches with the configured attribute
and an escaped username, requires exactly one matching entry, and then binds
as that entry with the password entered on the login form. User passwords
are not saved locally or written to logs. Directory referrals are disabled.

#### Development setup: step by step

Run these steps from the repository root. Keep the database environment set
for the SQLite or PostgreSQL database you already use; LDAP does not change it.

1. **Install the locked dependencies.**

  ```bash
  uv sync --locked --extra client
  ```

2. **Create a private configuration file outside the checkout.** The command
  below preserves an existing file instead of overwriting your configuration.

  ```bash
  if [[ ! -e "$HOME/.config/hdb/ldap.conf" ]]; then
     install -D -m 0600 deploy/ldap.conf.example "$HOME/.config/hdb/ldap.conf"
  fi
  ```

3. **Review that file in your editor.** For the tested BNL setup, use the INI
  values above. Confirm `enabled = true` and that `ca_cert_file` points to a
  readable, trusted CA bundle on this machine. Do not add service passwords
  or machine-join credentials: this configuration uses anonymous searches.

4. **Select the file and check that Django enables LDAP.** Run these commands
  in the same terminal that will start the server. This checks configuration
  loading, not a real user's LDAP password.

  ```bash
  export DJANGO_LDAP_CONFIG="$HOME/.config/hdb/ldap.conf"
  uv run --locked --extra client python manage.py check
  uv run --locked --extra client python manage.py shell -c \
     'from django.conf import settings; assert settings.HDB_LDAP_CONFIG.enabled, "LDAP is disabled"; print(settings.AUTHENTICATION_BACKENDS)'
  ```

  The printed list should include `hdb.auth_backends.LDAPBackend` followed by
  Django's `ModelBackend`. Repeat the export when opening a new terminal.

5. **Restart the development server with that environment.** Stop an existing
  development server with Ctrl+C, then start it from the terminal used above.

  ```bash
  DJANGO_DEBUG=true uv run --locked --extra client python manage.py runserver
  ```

6. **Provision the HDB account.** Visit `/admin/` and sign in with your local
  administrator (`admin/admin` for a seeded development database). Under
  Authentication and Authorization, choose Users, then Add user. Enter the
  exact directory username and leave both password fields blank. Save the
  account, assign its institution and groups, and confirm it is active.
  For an existing account, use its admin password-change form to disable
  password-based authentication instead of setting a blank local password.
  Keep your local administrator password enabled as a fallback.

7. **Test the user's login.** Log out of the administrator session or open a
  private browser window, visit `/`, and enter the directory username and
  that user's LDAP password. The login form itself does not change. The
  password owner should perform this test; do not share the password in chat
  or save it in a script. If login fails, use the troubleshooting notes below.

#### Production setup: step by step

These steps require an authorized administrator; do not run them against a
live deployment without approval. Do not commit the populated configuration.

1. **Update the application dependencies** using the service's virtualenv,
  following [Shell environment for deployment](#shell-environment-for-deployment).
  Keep PostgreSQL and the other production settings unchanged.

2. **Install a reviewed copy** of [deploy/ldap.conf.example](deploy/ldap.conf.example)
  as `/etc/hdb/ldap.conf`. Use root ownership, mode 0640, and the application's
  service group (`eicmax` on the existing RHEL host). Ensure the service account
  can read the file and traverse `/etc/hdb`, but cannot modify the config.
  Check that the configured CA bundle is readable by that account.

3. **Select the configuration path.** `/etc/hdb/ldap.conf` is the default, so
  no new environment variable is needed for that location. For another path,
  add `DJANGO_LDAP_CONFIG=/absolute/path/to/ldap.conf` to the private service
  environment file, currently `/etc/hdb/env`. Validate configuration with
  `manage.py check` using the same environment and virtualenv as Gunicorn.

4. **Restart the application service** after the approved configuration change.
  On the existing deployment, the administrator runs:

  ```bash
  sudo systemctl restart hdb-gunicorn
  ```

5. **Provision and test the user** as in development steps 6 and 7, using HTTPS
  and your real local administrator credentials, not the seeded defaults.
  Confirm local administrator login still works as well. Each HDB account
  must exist and be active before its owner can use LDAP login.

Only existing, active HDB accounts without usable local passwords use LDAP.
HDB does not automatically create users, import LDAP groups, grant staff or
superuser access, or change ownership. Accounts with local passwords continue
to use those passwords only, including the local administrator; failed local
logins are not retried against LDAP. Browser login, Django admin, REST Basic
authentication, and MCP Basic authentication use Django's configured backends.
Use HTTPS for client access in production, independently of LDAP's TLS.

Set `enabled = false` and restart to disable LDAP. Local accounts continue
working, but LDAP-only accounts cannot log in while it is disabled or unreachable.
Existing sessions are governed by Django's session/backend behavior; disabling
LDAP is not a substitute for explicitly revoking sessions when required.

Anonymous lookup and TLS have been verified; a successful login using a real
directory password still needs to be tested by its owner. Never paste that
password into chat or put it in a test script. For failures, first check that
the same config path is supplied to the running process, that the CA bundle
is readable, and that the HDB account exists, is active, and has local password
authentication disabled. Use the short directory username, not `DOMAIN\\user`
or an email address unless that is the configured directory attribute value.

---

## Web UI

A server-rendered web UI (separate from `/admin/`) lives at `/`, built from
`hdb/views_web.py` + `hdb/urls_web.py`. After logging in at `/`, it offers:

| Page | What it's for |
|------|----------------|
| Dashboard | At-a-glance counts across all domains |
| Components | Catalog browsing/search, add properties, create instances, transfer ownership |
| Inventory | Instance browsing/search, QR code page, delete (if not installed in a design), transfer ownership, update location/identifiers |
| Design Templates | Browse, create, edit placeholders, and (superuser) delete unlocked templates |
| Designs | Browse, "New from Template", assign/unassign installed instances, update location, delete |
| Systems | Technical systems with component/instance counts |
| Institutions | Institution list |
| Users | User list with institution/group info |
| Logs | Read-only browse/filter over every `LogEntry` |

Every write action goes through the same ownership rule described in
[Ownership](#ownership) above — group membership, `owner_user`, or
staff/superuser status — enforced server-side on every POST, not just by
hiding buttons in the template.

---

## Python Client, CLI, and MCP Server

`client/` holds three thin, interchangeable ways to talk to HDB
programmatically, all built on the same `hdb_client` query/write layer:

| Layer | File(s) | Use it when… |
|---|---|---|
| Python library | `client/hdb_client/` | Writing a script or Django-adjacent tool; direct ORM-speed access, in-process |
| CLI | `client/hdb.py` | Querying/updating from a terminal or shell script |
| MCP server | `client/mcp_server.py` | Letting an AI assistant or other MCP client query/update HDB over HTTP |

A minimal example (see [Quick Start](#quick-start) above for the full
bootstrap needed outside `manage.py shell`):

```python
from hdb_client import HDBClient
client = HDBClient()                    # or HDBClient(user=some_django_user) for writes

client.search_all("Crystal")
client.where_is(instance_pk)
client.designs.bom("BEMC tower")
client.designs.template_bom("BTOF Stave")   # see Domain 3 above for what this recurses through
```

`HDBClient` aggregates six sub-clients (`locations`, `systems`, `catalog`,
`inventory`, `designs`) plus the two cross-domain helpers shown above.

**For the complete method-by-method reference, the CLI command list
(including `load-template`/`bom-template`/`delete-template` for working
with Design Templates), the YAML formats, and MCP server setup/auth/tools —
see [`client/README.md`](client/README.md).** That document is the
authoritative usage reference for all three layers; this section is
intentionally just an orientation, kept short so it can't drift out of sync
with the client code the way a duplicated method list would.

---

## Seed Data

`python manage.py seed_hdb` loads a realistic ePIC detector dataset
(verified against the current schema — counts below are exact, not
approximate):

| Object | Count | Examples |
|--------|-------|---------|
| Institutions | 4 | BNL, CUA, UIC, UH (University of Hawaii) |
| Locations | 3 | Storage Room (CUA), Test Lab (UIC), Bldg,510A (BNL) |
| Groups | 3 | BEMC, BTOF, PFRICH |
| Users | 8 | `admin`, `maxim` (superusers); `crafts` (BEMC), `gnigmat` (BTOF), `ottjenni` (BTOF), `ullrich` (BEMC+BTOF), `bpage` (PFRICH), `ayk` (PFRICH) |
| Technical Systems | 5 | BEMC-CRYSTAL, BEMC-PM, BTOF-Sensor, BTOF-Readout, PFRICH-HRPPD |
| Components | 4 | PbWO4 Crystal, Hamamatsu S14160-3010PS, AC-LGAD Sensor, FCFDv2 Readout |
| Instances | 26 | 3 crystals, 16 SiPMs, 2 AC-LGAD sensors, 5 FCFDv2 readout ASICs |
| Design Templates | 1 | BEMC tower (blueprint: 1 crystal + 4 SiPMs) |
| Designs | 1 | BEMC tower (instantiated from its own template) |
| Property Types | 7 | Weight, Datasheet, Timing Constant, Length, Width, Height, Image |
| Sources | 0 | — |

Every account's initial password matches its username (e.g. `crafts`/`crafts`),
set only the first time each account is created. The command is
**idempotent** — safe to run repeatedly, never duplicates or clobbers
existing records (`get_or_create()` throughout).

Separately, `data/btof_stave_templates.yaml` provides a much larger,
multi-level Design Template set (the ePIC BTOF Stave → Half-Stave →
Stavelet hierarchy) — it's **not** loaded by `seed_hdb`, but by
`client/hdb.py load-template`. See
[Python Client, CLI, and MCP Server](#python-client-cli-and-mcp-server)
above and `client/README.md` for how.

---

## Schema Diagram

```
Institution ──► Location (self-FK: building → room → cabinet → shelf)
                    │
                    └──────────────────────────────────────┐
                                                             ▼
Group ──► TechnicalSystem ──► Component ──► ComponentInstance
                                   │                │
                                   ├─► ComponentSource ──► Source
                                   │
                                   ├─► PropertyValue  (FK: component)
                                   ├─► PropertyValue  (FK: component_instance)
                                   ├─► LogEntry        (FK: component)
                                   └─► LogEntry        (FK: component_instance)

DesignTemplate ──► DesignTemplateElement ──┬─► Component                       (leaf)
      │                    │               └─► DesignTemplate (child_template)  (real nesting)
      │                    │
      │                    (exactly one of the two, never neither/both; never a cycle)
      │
      │   instantiate (recurses into child_template placeholders too)
      ▼
    Design ──► DesignElement ──┬─► Component                 (leaf)
      │             │          └─► Design (child_design)      (real nesting)
      │             │
      │             └─► DesignElementInstance ──► ComponentInstance
      │
      ├─► PropertyValue  (FK: design)          ├─► PropertyValue  (FK: design_element)
      └─► LogEntry        (FK: design)

UserProfile ──► User, Institution
```

---

## Design Decisions

**`OwnedModel` abstract base** — every writable domain model inherits a
consistent set of ownership (`owner_user`, `owner_group`, `group_writeable`)
and audit fields (`created_by/on`, `modified_by/on`), matching the CDB User
Guide's ownership model.

**`Institution` as a first-class model** — rather than a free-text field or
a special location type, `Institution` is its own model with country, city,
and URL. Every `Location` has a mandatory FK to an `Institution`, enabling
`location__institution__abbreviation=...`-style queries across the whole
inventory.

**Single `PropertyValue` table** — four nullable FK columns (`component`,
`component_instance`, `design`, `design_element`) let one table serve all
domains with no duplication of schema, matching the CDB's philosophy that
any object can carry any property.

**`DesignElement` / `DesignTemplateElement` dual-target pattern** — each
element sets either `component` (leaf) or `child_design`/`child_template`
(sub-assembly), never both. This enables unlimited BOM nesting depth for
both real `Design`s and, structurally identically, `DesignTemplate`s — see
[Domain 3](#domain-3--designs) for the full reasoning, the self-nesting
cycle prevention, and how `product_component` keeps every intermediate
assembly level independently serial-trackable without tying that to how
the placeholder is nested.

**Superuser-only template deletion** — editing an unlocked `DesignTemplate`
is open to any `owner_group` member (matching every other write in the
system), but *deleting* one is superuser-only. Removing a template removes
it for every group member at once, not just the deleter's own records — a
deliberately stricter bar than the usual ownership rule. Deletion is also
blocked while the template is nested inside another one (`child_template`
is `PROTECT`ed).

**QuerySet-returning client methods** — `hdb_client`'s domain clients return
lazy Django QuerySets wherever possible. Callers can chain additional
filters, annotations, or `values()` calls without re-querying the database.
Methods that return dicts or lists (for JSON-safe API/MCP output) are
explicitly documented as such in `client/README.md`.

---

## Note for testers/developers

For a disposable SQLite development database only, stop the server and back
up the database and media before resetting. These commands do not reset
PostgreSQL. Keep the committed migration files intact:
```bash
# from your project root (where manage.py lives)
export DJANGO_DB_TYPE=sqlite
rm -f db.sqlite3
uv run --locked --extra client python manage.py migrate
uv run --locked --extra client python manage.py seed_hdb
```

---

## Shell environment for deployment

Use [deploy/env.example](deploy/env.example) as the key reference for the
private `/etc/hdb/env` file. Set `DJANGO_DB_TYPE=postgres`, the real database
credentials, a unique `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=false`, and
`DJANGO_ALLOWED_HOSTS` for your deployment's actual hostname. The example
contains hostnames from the original deployment; replace them for a new site.
Never commit the populated file. The existing file is root-owned, mode 600,
and loaded by systemd through `EnvironmentFile=/etc/hdb/env`.

For uv dependency updates on the existing host, preserve the virtualenv used
by the service:

```bash
UV_PROJECT_ENVIRONMENT=/direct/eic+u/eicmax/.virtualenvs/hdb \
    uv sync --locked --no-editable
```

Do not run production commands through a new default `.venv`; use the service
environment and load the same private configuration for management commands.

## Setting the Django and other environment on the deployment machine

An ordinary user shell cannot source the root-only `/etc/hdb/env` file.
Have an authorized administrator run management commands with that file
loaded; for example, this read-only connection check on the existing host:

```bash
sudo bash -c '
  set -a; source /etc/hdb/env; set +a
  cd /direct/eic+u/eicmax/projects/epic-hdb
  /direct/eic+u/eicmax/.virtualenvs/hdb/bin/python manage.py shell -c '\''from django.db import connection; assert connection.vendor == "postgresql"; connection.ensure_connection(); print(connection.vendor, connection.settings_dict["NAME"])'\''
'
```

Back up the database before running `migrate`. Run `collectstatic` using the
same environment and ensure `/var/data/hdb/static` and `/var/data/hdb/media`
have the application/httpd permissions described in [CLAUDE.md](CLAUDE.md).

## Updating the running code on the deployment machine:

```bash
sudo systemctl restart hdb-gunicorn
```
