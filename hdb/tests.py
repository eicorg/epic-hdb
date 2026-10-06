from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth import authenticate
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory
from django.test import TestCase
from django.test import SimpleTestCase
from django.test import override_settings
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from .auth_backends import LDAPBackend
from hdb_project.ldap_config import LDAPConfig, load_ldap_config

from .admin import OptionalPasswordUserCreationForm


class OptionalPasswordUserCreationFormTests(TestCase):
	def make_form(self, **data):
		return OptionalPasswordUserCreationForm({"username": "external_user", **data})

	def test_blank_passwords_disable_local_password_login(self):
		form = self.make_form(password1="", password2="")
		self.assertTrue(form.is_valid(), form.errors)
		user = form.save()
		user.refresh_from_db()
		self.assertFalse(user.has_usable_password())
		self.assertFalse(user.check_password(""))
		self.assertFalse(user.check_password("arbitrary-password"))

	def test_valid_passwords_enable_local_password_login(self):
		password = "Strong-local-test-password-4829!"
		form = self.make_form(password1=password, password2=password)
		self.assertTrue(form.is_valid(), form.errors)
		user = form.save()
		self.assertTrue(user.has_usable_password())
		self.assertTrue(user.check_password(password))

	def test_one_blank_password_is_rejected(self):
		for field in ("password1", "password2"):
			with self.subTest(field=field):
				form = self.make_form(**{field: "Strong-local-test-password-4829!"})
				self.assertFalse(form.is_valid())
				self.assertTrue("password1" in form.errors or "password2" in form.errors)

	def test_mismatched_passwords_are_rejected(self):
		form = self.make_form(
			password1="Strong-local-test-password-4829!",
			password2="Different-local-test-password-8371!",
		)
		self.assertFalse(form.is_valid())
		self.assertIn("password2", form.errors)

	def test_password_strength_validation_is_preserved(self):
		form = self.make_form(password1="12345678", password2="12345678")
		self.assertFalse(form.is_valid())
		self.assertIn("password2", form.errors)

	def test_posted_disabled_flag_cannot_bypass_password_validation(self):
		form = self.make_form(
			password1="12345678", password2="12345678", usable_password="false"
		)
		self.assertFalse(form.is_valid())

	def test_admin_add_form_uses_optional_passwords(self):
		request = RequestFactory().get("/admin/auth/user/add/")
		request.user = User(username="administrator", is_staff=True, is_superuser=True)
		user_admin = admin.site._registry[User]
		form_class = user_admin.get_form(request)
		form = form_class({"username": "external_user"})
		self.assertNotIn("usable_password", form.fields)
		self.assertFalse(form.fields["password1"].required)
		self.assertFalse(form.fields["password2"].required)
		self.assertTrue(form.is_valid(), form.errors)
		self.assertFalse(form.save(commit=False).has_usable_password())


@override_settings(HDB_LDAP_CONFIG=LDAPConfig(
	enabled=True,
	server_url="ldap://directory.example:389",
	search_base="DC=example,DC=org",
))
class LDAPBackendTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user("directory_user", password=None)
		self.backend = LDAPBackend()
		self.lookup = MagicMock()
		self.lookup.start_tls.return_value = True
		self.lookup.bind.return_value = True
		self.lookup.result = {"result": 0}
		self.lookup.entries = [SimpleNamespace(entry_dn="CN=Directory User,DC=example,DC=org")]
		self.user_connection = MagicMock()
		self.user_connection.start_tls.return_value = True
		self.user_connection.bind.return_value = True
		patcher = patch("hdb.auth_backends.Connection")
		self.factory = patcher.start()
		self.addCleanup(patcher.stop)
		self.factory.side_effect = [self.lookup, self.user_connection]

	def test_ldap_user_authenticates_without_local_password(self):
		password_hash = self.user.password
		result = self.backend.authenticate(None, username=self.user.username, password="directory-password")
		self.assertEqual(result, self.user)
		self.lookup.start_tls.assert_called_once()
		self.user_connection.start_tls.assert_called_once()
		self.lookup.unbind.assert_called_once()
		self.user_connection.unbind.assert_called_once()
		self.user.refresh_from_db()
		self.assertEqual(self.user.password, password_hash)
		self.assertFalse(self.user.is_staff)
		self.assertFalse(self.user.is_superuser)

	def test_unknown_user_is_not_created_or_searched(self):
		self.assertIsNone(self.backend.authenticate(None, username="unknown", password="directory-password"))
		self.factory.assert_not_called()
		self.assertEqual(User.objects.count(), 1)

	def test_local_password_user_is_not_sent_to_ldap(self):
		self.user.set_password("local-password")
		self.user.save()
		self.assertIsNone(self.backend.authenticate(None, username=self.user.username, password="wrong-password"))
		self.factory.assert_not_called()

	def test_inactive_user_is_not_sent_to_ldap(self):
		self.user.is_active = False
		self.user.save()
		self.assertIsNone(self.backend.authenticate(None, username=self.user.username, password="directory-password"))
		self.factory.assert_not_called()

	def test_empty_password_is_rejected_without_connection(self):
		self.assertIsNone(self.backend.authenticate(None, username=self.user.username, password=""))
		self.factory.assert_not_called()

	def test_tls_failure_never_sends_credentials(self):
		self.lookup.start_tls.return_value = False
		self.assertIsNone(self.backend.authenticate(None, username=self.user.username, password="directory-password"))
		self.lookup.bind.assert_not_called()
		self.assertEqual(self.factory.call_count, 1)
		self.lookup.unbind.assert_called_once()

	def test_user_tls_failure_never_binds_password(self):
		self.user_connection.start_tls.return_value = False
		self.assertIsNone(self.backend.authenticate(None, username=self.user.username, password="directory-password"))
		self.user_connection.bind.assert_not_called()

	def test_wrong_ldap_password_is_rejected(self):
		self.user_connection.bind.return_value = False
		self.assertIsNone(self.backend.authenticate(None, username=self.user.username, password="wrong-password"))

	def test_missing_or_ambiguous_directory_match_is_rejected(self):
		for entries in ([], self.lookup.entries * 2):
			with self.subTest(count=len(entries)):
				self.factory.reset_mock()
				self.factory.side_effect = [self.lookup]
				self.lookup.entries = entries
				self.assertIsNone(self.backend.authenticate(None, username=self.user.username, password="directory-password"))
				self.assertEqual(self.factory.call_count, 1)

	def test_search_errors_are_rejected(self):
		self.lookup.result = {"result": 4}
		self.assertIsNone(self.backend.authenticate(None, username=self.user.username, password="directory-password"))
		self.assertEqual(self.factory.call_count, 1)

	def test_connection_errors_fail_closed_and_release_connections(self):
		self.lookup.open.side_effect = OSError("connection unavailable")
		with self.assertLogs("hdb.auth_backends", level="WARNING"):
			self.assertIsNone(self.backend.authenticate(None, username=self.user.username, password="directory-password"))
		self.lookup.unbind.assert_called_once()

	def test_username_is_escaped_in_filter(self):
		self.user.username = "user*)(sAMAccountName=*)"
		self.user.save()
		self.backend.authenticate(None, username=self.user.username, password="directory-password")
		query = self.lookup.search.call_args.args[1]
		self.assertEqual(query, r"(sAMAccountName=user\2a\29\28sAMAccountName=\2a\29)")

	@override_settings(HDB_LDAP_CONFIG=LDAPConfig())
	def test_disabled_ldap_never_connects(self):
		self.assertIsNone(self.backend.authenticate(None, username=self.user.username, password="directory-password"))
		self.factory.assert_not_called()

	def test_certificate_validation_is_required(self):
		self.backend.authenticate(None, username=self.user.username, password="directory-password")
		import ssl
		server = self.factory.call_args.args[0]
		self.assertEqual(server.tls.validate, ssl.CERT_REQUIRED)
		self.assertFalse(self.factory.call_args.kwargs["auto_referrals"])

	@override_settings(HDB_LDAP_CONFIG=LDAPConfig(
		enabled=True, server_url="ldaps://directory.example", search_base="DC=example,DC=org"
	))
	def test_ldaps_uses_direct_tls_without_starttls(self):
		self.assertEqual(self.backend.authenticate(None, username=self.user.username, password="directory-password"), self.user)
		server = self.factory.call_args.args[0]
		self.assertTrue(server.ssl)
		self.assertEqual(server.port, 636)
		self.lookup.start_tls.assert_not_called()
		self.user_connection.start_tls.assert_not_called()

	def test_anonymous_bind_failure_stops_user_authentication(self):
		self.lookup.bind.return_value = False
		self.assertIsNone(self.backend.authenticate(None, username=self.user.username, password="directory-password"))
		self.assertEqual(self.factory.call_count, 1)

	@override_settings(AUTHENTICATION_BACKENDS=[
		"hdb.auth_backends.LDAPBackend", "django.contrib.auth.backends.ModelBackend"
	])
	def test_django_authenticate_uses_ldap_for_external_user(self):
		user = authenticate(username=self.user.username, password="directory-password")
		self.assertEqual(user, self.user)
		self.assertEqual(user.backend, "hdb.auth_backends.LDAPBackend")

	@override_settings(AUTHENTICATION_BACKENDS=[
		"hdb.auth_backends.LDAPBackend", "django.contrib.auth.backends.ModelBackend"
	])
	def test_django_authenticate_preserves_local_login(self):
		self.user.set_password("local-password")
		self.user.save()
		user = authenticate(username=self.user.username, password="local-password")
		self.assertEqual(user, self.user)
		self.assertEqual(user.backend, "django.contrib.auth.backends.ModelBackend")
		self.factory.assert_not_called()


class LDAPConfigTests(SimpleTestCase):
	def setUp(self):
		directory = TemporaryDirectory()
		self.addCleanup(directory.cleanup)
		self.path = Path(directory.name) / "ldap.conf"

	def write_config(self, **overrides):
		values = {
			"enabled": "true",
			"server_url": "ldap://directory.example:389",
			"search_base": "DC=example,DC=org",
		}
		values.update(overrides)
		self.path.write_text("[ldap]\n" + "\n".join(f"{key} = {value}" for key, value in values.items()))
		return load_ldap_config(self.path, required=True)

	def test_missing_default_config_disables_ldap(self):
		self.assertFalse(load_ldap_config(self.path).enabled)

	def test_missing_explicit_config_fails_closed(self):
		with self.assertRaises(ImproperlyConfigured):
			load_ldap_config(self.path, required=True)

	def test_valid_config_supports_anonymous_lookup(self):
		config = self.write_config()
		self.assertTrue(config.enabled)
		self.assertEqual(config.search_base, "DC=example,DC=org")
		self.assertEqual(config.username_attribute, "sAMAccountName")

	def test_disabled_config_needs_no_server_details(self):
		self.path.write_text("[ldap]\nenabled = false\n")
		self.assertFalse(load_ldap_config(self.path, required=True).enabled)

	def test_invalid_settings_are_rejected(self):
		for overrides in (
			{"enabled": "invalid"},
			{"server_url": "https://directory.example"},
			{"server_url": "ldap://user:password@directory.example"},
			{"server_url": "ldap://directory.example:invalid"},
			{"server_url": "ldap://directory.example?query=bad"},
			{"search_base": ""},
			{"username_attribute": "sAMAccountName)(uid=*"},
			{"timeout": "0"},
			{"timeout": "61"},
			{"ca_cert_file": str(self.path.parent / "missing-ca.pem")},
		):
			with self.subTest(overrides=overrides), self.assertRaises(ImproperlyConfigured):
				self.write_config(**overrides)

	def test_missing_section_or_malformed_file_is_rejected(self):
		for contents in ("[other]\nenabled=true", "not an ini file"):
			with self.subTest(contents=contents):
				self.path.write_text(contents)
				with self.assertRaises(ImproperlyConfigured):
					load_ldap_config(self.path, required=True)
