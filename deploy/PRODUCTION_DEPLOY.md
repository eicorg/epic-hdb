# HDB operational installation (AlmaLinux 9)

This guide installs HDB as a systemd-managed Django application backed by PostgreSQL. Gunicorn remains loopback-only on `127.0.0.1:8002`; Nginx serves static/media files and is the public web server.

Do not use `manage.py runserver` for this deployment.

## Conventions

| Item | Value |
| --- | --- |
| Service account | `hdb` |
| Application checkout | `/opt/hdb` |
| Existing uv executable | `/usr/local/bin/uv` |
| Private runtime configuration | `/etc/hdb/env` |
| LDAP configuration | `/etc/hdb/ldap.conf` |
| Uploaded media | `/var/data/hdb/media` |
| Collected static files | `/var/data/hdb/static` |
| PostgreSQL database / role | `hdb` / `hdb_app` |
| Gunicorn listener | `127.0.0.1:8002` |
| Shared hostname | `hdb.eic.bnl.gov` |

## 1. Install PostgreSQL

HDB requires PostgreSQL 14 or newer. On AlmaLinux 9, install PostgreSQL 16 from the PGDG repository.

```bash
sudo dnf install -y \
  https://download.postgresql.org/pub/repos/yum/reporpms/EL-9-x86_64/pgdg-redhat-repo-latest.noarch.rpm
sudo dnf -qy module disable postgresql
sudo dnf install -y postgresql16-server postgresql16 git curl

sudo /usr/pgsql-16/bin/postgresql-16-setup initdb
sudo systemctl enable --now postgresql-16
/usr/pgsql-16/bin/psql --version
```

The default local TCP rule is sufficient for HDB:

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

`\password` prompts without putting the password in shell history. Confirm the role can connect:

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
sudo -u hdb -H test -w /var/backups/hdb && echo "hdb can write backups"
```

`/etc/hdb/env` is root-owned but readable by the `hdb` group because Django needs the database password at startup. `/var/backups/hdb` is required because the deployment script writes a database dump there before migrations.

## 4. Check out HDB and create the locked Python environment

`uv` is already installed at `/usr/local/bin/uv`; do not install another copy.

```bash
sudo -u hdb -H /usr/local/bin/uv --version

sudo -u hdb -H git clone --branch <branch> <repository-url> /opt/hdb
sudo -u hdb -H bash -c '
  cd /opt/hdb
  /usr/local/bin/uv sync --locked --no-editable
'
```

This creates `/opt/hdb/.venv`. The version-controlled deployment script is already in the checkout:

```text
Repository path: deploy/deploy-hdb.py
Deployed path:   /opt/hdb/deploy/deploy-hdb.py
```

Confirm that it is executable:

```bash
sudo -u hdb -H test -x /opt/hdb/deploy/deploy-hdb.py
```

## 5. Generate the Django secret, then create the environment file

Generate a secret before creating the environment file:

```bash
openssl rand -base64 48
```

Create and edit the root-owned configuration file:

```bash
sudoedit /etc/hdb/env
```

Use the following contents, replacing the placeholders. Do not quote values.

```text
DJANGO_DB_TYPE=postgres
DJANGO_DB_NAME=hdb
DJANGO_DB_USER=hdb_app
DJANGO_DB_PASSWORD=<database-password>
DJANGO_DB_HOST=127.0.0.1
DJANGO_DB_PORT=5432

# Require this LDAP configuration at startup. Omit only when LDAP is
# intentionally disabled.
DJANGO_LDAP_CONFIG=/etc/hdb/ldap.conf

DJANGO_DEBUG=false
DJANGO_SECRET_KEY=<generated-secret>
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,hdb.eic.bnl.gov

# Keep false until Nginx HTTPS for hdb.eic.bnl.gov is verified.
DJANGO_SECURE_COOKIES=false
```

Confirm permissions:

```bash
sudo stat -c '%A %U:%G %n' /etc/hdb/env
```

Expected permissions include `-rw-r----- root:hdb /etc/hdb/env`.

`DJANGO_DB_TYPE=postgres` is mandatory. HDB otherwise falls back to SQLite and can create an unintended `db.sqlite3` in the repository.

| Variable | Purpose |
| --- | --- |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated permitted HTTP hostnames. |
| `DJANGO_SECURE_COOKIES` | Enables HTTPS-only session and CSRF cookies. Set to `true` only after HTTPS is verified. |

## 6. Configure LDAP authentication

The `controls_update` branch includes HDB's own LDAP backend. It reads `/etc/hdb/ldap.conf` by default. Setting `DJANGO_LDAP_CONFIG=/etc/hdb/ldap.conf` makes a missing or malformed file stop Django at startup rather than silently disabling LDAP.

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

With this backend, an `ldap://` URL is upgraded to StartTLS before both directory lookup and password bind. An `ldaps://` URL uses direct TLS. In either case the configured CA bundle is validated.

LDAP is authentication, not automatic account provisioning. HDB tries LDAP only for an **existing active Django user** whose username matches the directory `sAMAccountName` and whose local password is unusable. This creates an intentional local allow-list. Existing local administrators with usable Django passwords remain break-glass accounts.

The backend performs its initial directory search without a service-account bind. Confirm that the directory permits the required search under `DC=bnl,DC=gov`; otherwise design a service-account bind before enabling LDAP.

## 7. Create the schema and initial administrator

Run management commands as `hdb`, never as root:

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

Do **not** run `seed_hdb` on a generic production deployment. It creates demonstration accounts and ePIC-oriented sample data.

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

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hdb-gunicorn.service
sudo systemctl status hdb-gunicorn.service
sudo journalctl -u hdb-gunicorn.service -n 100 --no-pager
```

## 9. Verify Gunicorn without publishing the service

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8002/
sudo ss -ltnp | grep ':8002'
```

The listener must be `127.0.0.1:8002`, not `0.0.0.0:8002`. A temporary tunnel can verify the application before Nginx is installed:

```bash
ssh -L 8002:127.0.0.1:8002 <your-user>@hdb.eic.bnl.gov
```

Open `http://localhost:8002/`. A login redirect is normal; styling is not expected until Nginx serves `/static/`.

## 10. Routine release procedure

For each approved release:

```bash
sudo -u hdb -H /opt/hdb/deploy/deploy-hdb.py
sudo systemctl restart hdb-gunicorn.service
sudo systemctl status hdb-gunicorn.service
```

The script refuses to run as root or any account other than `hdb`; use `/opt/hdb/deploy/deploy-hdb.py --help` for its command-line help. It verifies the PostgreSQL environment, refuses an unexpectedly modified checkout, fast-forwards from Git, syncs the lockfile, runs Django validation, creates a PostgreSQL dump, applies migrations, and collects static files.

The script writes a custom-format dump to `/var/backups/hdb` before every migration. Retain backups according to site policy, back up `/var/data/hdb/media` separately, and test restoration with `pg_restore` into a different database.

## 11. Install and configure Nginx

Install Nginx, the SELinux management utility, and ACL support:

```bash
sudo dnf install -y nginx policycoreutils-python-utils acl
```

Nginx must be able to traverse `/var/data/hdb` and read static/media files without making them world-readable. Grant Nginx a specific ACL; the default ACL maintains access for subsequently created files and directories.

```bash
sudo setfacl -m u:nginx:--x /var/data/hdb
sudo setfacl -R -m u:nginx:rX /var/data/hdb/static /var/data/hdb/media
sudo find /var/data/hdb/static /var/data/hdb/media -type d \
  -exec setfacl -m d:u:nginx:r-X {} +
```

Apply persistent SELinux labels and allow Nginx to proxy only to the loopback Gunicorn listener:

```bash
sudo semanage fcontext -a -t httpd_sys_content_t '/var/data/hdb(/.*)?'
sudo restorecon -Rv /var/data/hdb
sudo setsebool -P httpd_can_network_connect 1
```

Create `/etc/nginx/conf.d/hdb.conf` for local/tunnel verification. Do not open the firewall while this HTTP-only configuration is active.

```bash
sudoedit /etc/nginx/conf.d/hdb.conf
```

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name hdb.eic.bnl.gov localhost 127.0.0.1;

    client_max_body_size 50m;

    location /static/ {
        alias /var/data/hdb/static/;
        expires 7d;
        access_log off;
    }

    location /media/ {
        alias /var/data/hdb/media/;
    }

    location / {
        proxy_pass http://127.0.0.1:8002;
        proxy_http_version 1.1;

        # Preserve the browser's original host and port. $host drops a
        # nonstandard tunnel port and can cause Django CSRF failures.
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Validate and start Nginx:

```bash
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl status nginx

curl -I -H 'Host: hdb.eic.bnl.gov' \
  http://127.0.0.1/static/admin/css/base.css
```

The static-file request must return `200 OK` or `304 Not Modified`, not 403 or 404. To verify the complete Nginx path from a workstation without publishing the service, use an unused local port:

```bash
ssh -L 18080:127.0.0.1:80 <your-user>@hdb.eic.bnl.gov
```

Open `http://localhost:18080/`. The Django admin should have its normal styling. Do not use a port occupied by another local service.

## 12. Publish HDB through HTTPS

Do not expose HDB's login page over plain HTTP. LDAP StartTLS protects the application-to-directory connection; HTTPS protects users' credentials between their browser and Nginx.

Before publishing, obtain a trusted, institutionally managed TLS certificate and private key for `hdb.eic.bnl.gov`. Obtain the exact paths from the system administrators. This guide uses these placeholders:

```text
<certificate-full-chain.pem>
<private-key.pem>
```

Replace `/etc/nginx/conf.d/hdb.conf` with the following configuration, substituting the supplied certificate paths:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name hdb.eic.bnl.gov;

    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name hdb.eic.bnl.gov;

    ssl_certificate     <certificate-full-chain.pem>;
    ssl_certificate_key <private-key.pem>;
    ssl_protocols TLSv1.2 TLSv1.3;

    client_max_body_size 50m;

    location /static/ {
        alias /var/data/hdb/static/;
        expires 7d;
        access_log off;
    }

    location /media/ {
        alias /var/data/hdb/media/;
    }

    location / {
        proxy_pass http://127.0.0.1:8002;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

The Django settings must contain:

```python
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
```

This setting is already present in the `controls_update` branch. It makes Django recognize that Nginx terminated HTTPS. Keep `proxy_set_header X-Forwarded-Proto $scheme;` in the Nginx configuration.

Validate and reload Nginx. Confirm HTTPS works before permitting external connections:

```bash
sudo nginx -t
sudo systemctl reload nginx

curl -I http://hdb.eic.bnl.gov/
curl -I https://hdb.eic.bnl.gov/
```

The HTTP request must redirect to `https://hdb.eic.bnl.gov/`; the HTTPS request must succeed with a certificate trusted by the client.

Only after HTTPS verification, enable secure cookies and restart Gunicorn:

```bash
sudoedit /etc/hdb/env
# Change the existing line to:
DJANGO_SECURE_COOKIES=true

sudo systemctl restart hdb-gunicorn.service
sudo systemctl status hdb-gunicorn.service
```

Finally, permit HTTP only for the redirect and HTTPS for the application. This does not replace any required BNL network-firewall approval.

```bash
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
sudo firewall-cmd --list-services
```

Users should use:

```text
https://hdb.eic.bnl.gov/
```

Keep Gunicorn loopback-only. Certificate renewal must follow the institutional certificate-management process; reload Nginx after renewal if that process does not do so automatically.
