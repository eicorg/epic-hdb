import configparser
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured


@dataclass(frozen=True)
class LDAPConfig:
    enabled: bool = False
    server_url: str = ""
    search_base: str = ""
    username_attribute: str = "sAMAccountName"
    ca_cert_file: str = ""
    timeout: int = 10


def load_ldap_config(path, required=False):
    path = Path(path).expanduser()
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8") as source:
            parser.read_file(source)
        section = parser["ldap"]
        config = LDAPConfig(
            enabled=section.getboolean("enabled", fallback=False),
            server_url=section.get("server_url", "").strip(),
            search_base=section.get("search_base", "").strip(),
            username_attribute=section.get("username_attribute", "sAMAccountName").strip(),
            ca_cert_file=section.get("ca_cert_file", "").strip(),
            timeout=section.getint("timeout", fallback=10),
        )
        if not config.enabled:
            return config
        server = urlsplit(config.server_url)
        if (
            server.scheme not in {"ldap", "ldaps"}
            or not server.hostname
            or server.username is not None
            or server.password is not None
            or server.path not in {"", "/"}
            or server.query
            or server.fragment
            or (server.port is not None and not 1 <= server.port <= 65535)
        ):
            raise ValueError("server_url must be an ldap:// or ldaps:// host URL without credentials")
        if not config.search_base:
            raise ValueError("search_base is required when LDAP is enabled")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", config.username_attribute):
            raise ValueError("username_attribute must be an LDAP attribute name")
        if not 1 <= config.timeout <= 60:
            raise ValueError("timeout must be between 1 and 60 seconds")
        if config.ca_cert_file and not Path(config.ca_cert_file).is_file():
            raise ValueError("ca_cert_file must reference an existing certificate bundle")
        return config
    except FileNotFoundError as exc:
        if not required:
            return LDAPConfig()
        raise ImproperlyConfigured(f"LDAP configuration file does not exist: {path}") from exc
    except (OSError, configparser.Error, KeyError, ValueError) as exc:
        raise ImproperlyConfigured(f"Invalid LDAP configuration in {path}: {exc}") from exc