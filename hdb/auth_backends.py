import logging
import ssl
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.views.decorators.debug import sensitive_variables
from ldap3 import NONE, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars


logger = logging.getLogger(__name__)


class LDAPBackend(ModelBackend):
    @sensitive_variables()
    def authenticate(self, request, username=None, password=None, **kwargs):
        config = settings.HDB_LDAP_CONFIG
        if not config.enabled or not username or not password:
            return None
        user_model = get_user_model()
        try:
            user = user_model._default_manager.get(**{user_model.USERNAME_FIELD: username})
        except user_model.DoesNotExist:
            return None
        if user.has_usable_password() or not self.user_can_authenticate(user):
            return None

        endpoint = urlsplit(config.server_url)
        tls = Tls(
            validate=ssl.CERT_REQUIRED,
            version=ssl.PROTOCOL_TLS_CLIENT,
            ca_certs_file=config.ca_cert_file or None,
        )
        server = Server(
            endpoint.hostname,
            port=endpoint.port or (636 if endpoint.scheme == "ldaps" else 389),
            use_ssl=endpoint.scheme == "ldaps",
            tls=tls,
            get_info=NONE,
            connect_timeout=config.timeout,
        )
        lookup = None
        user_connection = None
        try:
            lookup = Connection(server, auto_referrals=False, receive_timeout=config.timeout)
            if not self._bind_securely(lookup, server):
                return None
            user_filter = f"({config.username_attribute}={escape_filter_chars(username)})"
            lookup.search(
                config.search_base,
                user_filter,
                SUBTREE,
                attributes=["1.1"],
                size_limit=2,
                time_limit=config.timeout,
            )
            if lookup.result.get("result") != 0 or len(lookup.entries) != 1:
                return None
            user_connection = Connection(
                server,
                user=lookup.entries[0].entry_dn,
                password=password,
                auto_referrals=False,
                receive_timeout=config.timeout,
            )
            if self._bind_securely(user_connection, server):
                return user
            return None
        except (LDAPException, OSError):
            logger.warning("LDAP authentication failed due to a directory connection error.")
            return None
        finally:
            for connection in (user_connection, lookup):
                if connection is not None:
                    try:
                        connection.unbind()
                    except (LDAPException, OSError):
                        pass

    @staticmethod
    @sensitive_variables()
    def _bind_securely(connection, server):
        connection.open()
        if not server.ssl and not connection.start_tls():
            return False
        return connection.bind()