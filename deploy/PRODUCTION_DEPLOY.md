# HDB operational installation (AlmaLinux 9)

This guide installs HDB as a systemd-managed Django application backed by
PostgreSQL.  It intentionally stops short of publishing the service: Gunicorn
listens only on `127.0.0.1:8002`.  That permits safe initial verification over
an SSH tunnel.  Add Nginx, HTTPS, institutional authentication, and SELinux
web-server policy only when the application is ready to be shared.

Do not use `manage.py runserver` for this deployment.

## Conventions

| Item | Value |
| --- | --- |
| Service account | `hdb` |
| Application checkout | `/opt/hdb` |
| Existing uv executable | `/usr/local/bin/uv` |
| Private runtime configuration | `/etc/hdb/env` |
| Uploaded media | `/var/data/hdb/media` |
| Collected static files | `/var/data/hdb/static` |
| PostgreSQL database / role | `hdb` / `hdb_app` |
| Gunicorn listener | `127.0.0.1:8002` |

All commands below are for the AlmaLinux host.  Replace `<repository-url>`,
`<branch>`, and `<vm-hostname>` before executing them.

## 1. Install PostgreSQL

HDB requires PostgreSQL 14 or newer.  On AlmaLinux 9, install PostgreSQL 16
from the PGDG repository rather than relying on the older default stream.

```bash
sudo dnf install -y \
  https://download.postgresql.org/pub/repos/yum/reporpms/EL-9-x86_64/pgdg-redhat-repo-latest.noarch.rpm
sudo dnf -qy module disable postgresql
sudo dnf install -y postgresql16-server postgresql16 git curl

sudo /usr/pgsql-16/bin/postgresql-16-setup initdb
sudo systemctl enable --now postgresql-16
/usr/pgsql-16/bin/psql --version
```

The default local TCP rule below is sufficient for HDB and does not need to be
changed:

```text
host    all    all    127.0.0.1/32    scram-sha-256
```

Do not expose PostgreSQL to the network.

## 2. Create the application database

```bash
sudo -u postgres /usr/pgsql-16/bin/psql
```

At the `psql` prompt, enter:

```sql
CREATE ROLE hdb_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
SET password_encryption = 'scram-sha-256';
\password hdb_app
CREATE DATABASE hdb OWNER hdb_app;
\connect hdb
GRANT USAGE, CREATE ON SCHEMA public TO hdb_app;
\q
```

The `\password` command prompts for the password without putting it in shell
history.  Confirm the role can connect:

```bash
/usr/pgsql-16/bin/psql -h 127.0.0.1 -p 5432 -U hdb_app -d hdb -W \
  -c 'SELECT current_database(), current_user;'
```

## 3. Create the service account and directories

```bash
sudo useradd --system --user-group --create-home \
  --home-dir /var/lib/hdb --shell /sbin/nologin hdb

sudo install -d -o hdb -g hdb -m 0755 /opt/hdb
sudo install -d -o hdb -g hdb -m 0750 /var/data/hdb
sudo install -d -o hdb -g hdb -m 0750 /var/data/hdb/media
sudo install -d -o hdb -g hdb -m 0750 /var/data/hdb/static
sudo install -d -o hdb -g hdb -m 0750 /var/backups/hdb
sudo install -d -o root -g hdb -m 0750 /etc/hdb
sudo install -o root -g hdb -m 0640 /dev/null /etc/hdb/env
sudo install -d -o hdb -g hdb -m 0750 /var/backups/hdb
sudo -u hdb -H test -w /var/backups/hdb && echo "hdb can write backups"
```

`hdb` owns only the application files and its runtime data.  `/etc/hdb/env` is
root-owned, but the `hdb` group can read it because Django needs the database
password when it starts.

`/var/backups/hdb` is required because `deploy-hdb.py` creates a PostgreSQL dump there before migrations.  `deploy-hdb.py` exists in the repository path at `deploy/deploy-hdb.py` and is not copied separately to a release area.  The cloned area is the location where this script can be executed `/opt/hdb/deploy/deploy-hdb.py`.

## 4. Check out HDB and create the locked Python environment

`uv` is already installed at `/usr/local/bin/uv`; do not install another copy.
Confirm the service account can run it:

```bash
sudo -u hdb -H /usr/local/bin/uv --version
```

Clone the approved HDB repository and branch:

```bash
sudo -u hdb -H git clone --branch <branch> <repository-url> /opt/hdb

sudo -u hdb -H bash -c '
  cd /opt/hdb
  /usr/local/bin/uv sync --locked --no-editable
'
```

This creates `/opt/hdb/.venv` with the locked Python version and Gunicorn.
Do not use `--extra client` on the server unless the optional CLI/MCP tools are
specifically needed there.

## 5. Generate the Django secret, then create the environment file

Generate a secret first, and retain the value long enough to enter it in the
environment file:

```bash
openssl rand -base64 48
```

Now edit the root-owned configuration file:

```bash
sudoedit /etc/hdb/env
```

Use the following contents, replacing the placeholders.  Do not quote values.

```text
DJANGO_DB_TYPE=postgres
DJANGO_DB_NAME=hdb
DJANGO_DB_USER=hdb_app
DJANGO_DB_PASSWORD=<database-password>
DJANGO_DB_HOST=127.0.0.1
DJANGO_DB_PORT=5432

# Require this LDAP configuration at startup.  Omit this line only when LDAP
# authentication is intentionally disabled.
DJANGO_LDAP_CONFIG=/etc/hdb/ldap.conf

DJANGO_DEBUG=false
DJANGO_SECRET_KEY=<generated-secret>
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,<vm-hostname>
```

Confirm the permissions:

```bash
sudo stat -c '%A %U:%G %n' /etc/hdb/env
```

Expected output includes `-rw-r----- root:hdb /etc/hdb/env`.

`DJANGO_DB_TYPE=postgres` is mandatory.  HDB otherwise silently falls back to
SQLite, which can create an unintended `db.sqlite3` in the repository.

## 6. Configure LDAP authentication

The `controls_update` branch includes HDB's own LDAP backend, so no additional
Python package is needed.  It reads `/etc/hdb/ldap.conf` by default.  Setting
`DJANGO_LDAP_CONFIG=/etc/hdb/ldap.conf` above is recommended: it makes a
missing or malformed file stop Django at startup instead of silently disabling
LDAP.

Create the file and edit it as root:

```bash
sudo install -o root -g hdb -m 0640 /dev/null /etc/hdb/ldap.conf
sudoedit /etc/hdb/ldap.conf
```

Use the site-supplied values:

```ini
[ldap]
enabled = true
server_url = ldap://addressbook.bnl.gov:389
search_base = DC=bnl,DC=gov
username_attribute = sAMAccountName
ca_cert_file = /etc/pki/tls/certs/ca-bundle.crt
timeout = 10
```

With this backend, an `ldap://` URL is upgraded to StartTLS before both the
directory lookup and the user's password bind.  An `ldaps://` URL uses direct
TLS.  In either case the configured CA bundle is validated.

LDAP is authentication, not automatic account provisioning.  HDB only tries
LDAP for an **existing active Django user** whose username exactly matches the
directory's `sAMAccountName` and whose local password is unusable.  This forms
an intentional local allow-list.  Provision approved users through HDB's
admin/user workflow, ensuring any required UserProfile is created; do not make
users superusers merely to enable LDAP login.  Existing local administrators
with usable Django passwords remain local break-glass accounts.

The backend performs its initial directory search without a service-account
bind.  Confirm that the BNL directory permits the required authenticated or
anonymous search beneath `DC=bnl,DC=gov`; otherwise this backend will need a
deliberate service-account design.

After creating the file, restart and test one approved, pre-provisioned account:

```bash
sudo systemctl restart hdb-gunicorn.service
sudo systemctl status hdb-gunicorn.service
sudo journalctl -u hdb-gunicorn.service -n 100 --no-pager
```

## 7. Create the schema and initial administrator

Run Django management commands as `hdb`, never as root:

```bash
sudo -u hdb -H bash -c '
  set -a
  . /etc/hdb/env
  set +a
  cd /opt/hdb

  .venv/bin/python manage.py check --deploy
  .venv/bin/python manage.py shell -c \
    "from django.db import connection; assert connection.vendor == \"postgresql\"; print(connection.vendor, connection.settings_dict[\"NAME\"])"
  .venv/bin/python manage.py showmigrations hdb
  .venv/bin/python manage.py migrate
  .venv/bin/python manage.py collectstatic --noinput
  .venv/bin/python manage.py createsuperuser
'
```

Do **not** run `seed_hdb` on a generic production deployment.  It creates
demonstration accounts and ePIC-oriented sample data.

## 8. Install the systemd service

Create `/etc/systemd/system/hdb-gunicorn.service`:

```ini
[Unit]
Description=HDB Gunicorn application server
After=network.target postgresql-16.service
Wants=postgresql-16.service

[Service]
Type=exec
User=hdb
Group=hdb
WorkingDirectory=/opt/hdb
EnvironmentFile=/etc/hdb/env
Environment=PYTHONUNBUFFERED=1

ExecStart=/opt/hdb/.venv/bin/gunicorn \
    --bind 127.0.0.1:8002 \
    --workers 2 \
    --timeout 60 \
    --access-logfile - \
    --error-logfile - \
    hdb_project.wsgi:application

Restart=on-failure
RestartSec=5
UMask=0027
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

Enable and verify it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hdb-gunicorn.service
sudo systemctl status hdb-gunicorn.service
sudo journalctl -u hdb-gunicorn.service -n 100 --no-pager
```

## 9. Verify without publishing the service

On the VM:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8002/
sudo ss -ltnp | grep ':8002'
```

The listener must be `127.0.0.1:8002`, not `0.0.0.0:8002`.

From a workstation, create a temporary tunnel:

```bash
ssh -L 8002:127.0.0.1:8002 <your-user>@<vm-hostname>
```

Open `http://localhost:8002/`.  A login redirect is normal.  Static styling
and direct media delivery are completed later by Nginx.

## 10. Routine release procedure

The version-controlled [`deploy/deploy-hdb.py`](deploy/deploy-hdb.py) script replaces
the long manual release command.  It verifies the PostgreSQL environment,
refuses an unexpectedly modified checkout, fast-forwards from Git, synchronizes
the lockfile, runs Django validation, creates a PostgreSQL dump, applies
migrations, and collects static files.

Install or update the script’s executable bit after checkout:

```bash
sudo chown hdb:hdb /opt/hdb/deploy/deploy-hdb.py
sudo chmod 0750 /opt/hdb/deploy/deploy-hdb.py
```

For each approved release:

```bash
sudo -u hdb -H /opt/hdb/deploy/deploy-hdb.py
sudo systemctl restart hdb-gunicorn.service
sudo systemctl status hdb-gunicorn.service
```

The script writes a custom-format dump to `/var/backups/hdb` before every
migration.  Retain backups according to your site policy and back up
`/var/data/hdb/media` separately.  Test restoration with `pg_restore` into a
different database.  It refuses to run as root or any account other than
`hdb`, and displays the expected command when that happens.  Run
`/opt/hdb/deploy/deploy-hdb.py --help` for its command-line help.

## Next phase: Nginx

Only after the above works, install Nginx to serve `/static/` and `/media/`,
proxy application traffic to `127.0.0.1:8002`, terminate TLS, and integrate
any institutional authentication.  Keep Gunicorn loopback-only.
