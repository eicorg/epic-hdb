#!/opt/hdb/.venv/bin/python
"""Deploy a reviewed HDB revision.  Run only as the hdb service account."""

from __future__ import annotations

import argparse
import os
import pwd
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


APP_DIR = Path("/opt/hdb")
ENV_FILE = Path("/etc/hdb/env")
UV = Path("/usr/local/bin/uv")
PG_DUMP = Path("/usr/pgsql-16/bin/pg_dump")
BACKUP_DIR = Path("/var/backups/hdb")
EXPECTED_USER = "hdb"


def parse_args() -> argparse.Namespace:
    """Return command-line options and provide standard -h/--help support."""
    parser = argparse.ArgumentParser(
        description=(
            "Synchronize, validate, back up, migrate, and prepare HDB for restart."
        ),
        epilog=(
            "Run as the HDB service account: "
            "sudo -u hdb -H /opt/hdb/deploy/deploy-hdb.py"
        ),
    )
    return parser.parse_args()


def fail(message: str) -> None:
    """Print an actionable error and exit unsuccessfully."""
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_environment(path: Path) -> dict[str, str]:
    """Read simple KEY=value settings without executing the environment file."""
    environment: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            fail(f"Invalid line {line_number} in {path}: expected KEY=value")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum() or key[0].isdigit():
            fail(f"Invalid variable name on line {line_number} in {path}")
        environment[key] = value.strip()
    return environment


def run(*command: str, environment: dict[str, str] | None = None) -> None:
    """Run one command in the HDB checkout and show it to the operator."""
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=APP_DIR, env=environment, check=True)


def require_deployment_prerequisites() -> None:
    """Fail before making changes when the host layout is incomplete."""
    current_user = pwd.getpwuid(os.geteuid()).pw_name
    if current_user != EXPECTED_USER:
        fail(
            f"This script must run as {EXPECTED_USER}, not {current_user}.\n"
            "Expected command: sudo -u hdb -H /opt/hdb/deploy/deploy-hdb.py"
        )
    if not (APP_DIR / ".git").is_dir():
        fail(f"{APP_DIR} is not an HDB Git checkout")
    if not ENV_FILE.is_file() or not os.access(ENV_FILE, os.R_OK):
        fail(f"{ENV_FILE} is not readable by {EXPECTED_USER}")
    if not os.access(UV, os.X_OK):
        fail(f"{UV} is not executable")
    if not os.access(PG_DUMP, os.X_OK):
        fail(f"{PG_DUMP} is not executable")
    if not BACKUP_DIR.is_dir() or not os.access(BACKUP_DIR, os.W_OK):
        fail(f"{BACKUP_DIR} is not writable by {EXPECTED_USER}")


def main() -> int:
    """Perform one HDB deployment preparation transaction."""
    parse_args()
    require_deployment_prerequisites()

    environment = os.environ.copy()
    environment.update(read_environment(ENV_FILE))

    if environment.get("DJANGO_DB_TYPE") != "postgres":
        fail("DJANGO_DB_TYPE must be postgres")
    for required in ("DJANGO_DB_NAME", "DJANGO_DB_USER", "DJANGO_DB_PASSWORD"):
        if not environment.get(required):
            fail(f"{required} is not set in {ENV_FILE}")

    status = subprocess.run(
        ("git", "status", "--porcelain"),
        cwd=APP_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    if status.stdout:
        fail("Refusing to deploy a checkout with local modifications")

    run("git", "pull", "--ff-only", environment=environment)
    run(str(UV), "sync", "--locked", "--no-editable", environment=environment)
    run(".venv/bin/python", "manage.py", "check", "--deploy", environment=environment)

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_file = BACKUP_DIR / f"hdb-{timestamp}.dump"
    os.umask(0o077)
    backup_environment = environment.copy()
    backup_environment["PGPASSWORD"] = environment["DJANGO_DB_PASSWORD"]
    run(
        str(PG_DUMP),
        "-h",
        environment.get("DJANGO_DB_HOST", "127.0.0.1"),
        "-p",
        environment.get("DJANGO_DB_PORT", "5432"),
        "-U",
        environment["DJANGO_DB_USER"],
        "--format=custom",
        f"--file={backup_file}",
        environment["DJANGO_DB_NAME"],
        environment=backup_environment,
    )

    run(".venv/bin/python", "manage.py", "migrate", environment=environment)
    run(
        ".venv/bin/python",
        "manage.py",
        "collectstatic",
        "--noinput",
        environment=environment,
    )
    print(f"Deployment preparation complete. Backup: {backup_file}")
    print("Restart with: sudo systemctl restart hdb-gunicorn.service")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        print(f"ERROR: command failed with exit code {error.returncode}", file=sys.stderr)
        raise SystemExit(error.returncode)
