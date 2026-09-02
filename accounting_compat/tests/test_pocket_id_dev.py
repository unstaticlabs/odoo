import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "pocket_id_dev.py"
ROOT = SCRIPT_PATH.parents[1]
SPEC = importlib.util.spec_from_file_location("usl_pocket_id_dev", SCRIPT_PATH)
POCKET_ID_DEV = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(POCKET_ID_DEV)


class TestPocketIDDevEnvironment(unittest.TestCase):
    def test_defaults_target_canonical_odoo_dev(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / ".pocket-id.env"
            with patch.dict(os.environ, {}, clear=True):
                POCKET_ID_DEV._write_new_env(path)
            values = POCKET_ID_DEV._read_env(path)
            mode = stat.S_IMODE(path.stat().st_mode)

        self.assertEqual(values["ODOO_INIT_DB"], "odoo_dev")
        self.assertEqual(values["ODOO_DB_FILTER"], "^odoo_dev$")
        self.assertEqual(values["POCKET_ID_PROSPER_ODOO_EMAIL"], "")
        self.assertEqual(values["ODOO_HTTP_PORT"], "8069")
        self.assertEqual(values["ODOO_GEVENT_PORT"], "8072")
        self.assertEqual(values["PAPERLESS_HTTP_PORT"], "8010")
        self.assertEqual(
            values["POCKET_ID_PAPERLESS_CLIENT_ID"],
            "usl-paperless-preproduction",
        )
        self.assertEqual(
            values["PAPERLESS_PUBLIC_BASE_URL"],
            "http://paperless.localhost:8010",
        )
        self.assertEqual(mode, 0o600)

    def test_generated_environment_honors_isolated_paperless_port(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / ".pocket-id.env"
            with patch.dict(
                os.environ,
                {
                    "USL_POCKET_ID_DEV_PAPERLESS_PORT": "21946",
                    "USL_POCKET_ID_DEV_PAPERLESS_URL": (
                        "http://paperless.localhost:21946"
                    ),
                },
                clear=True,
            ):
                POCKET_ID_DEV._write_new_env(path)
            values = POCKET_ID_DEV._read_env(path)

        self.assertEqual(values["PAPERLESS_HTTP_PORT"], "21946")
        self.assertEqual(
            values["PAPERLESS_PUBLIC_URL"],
            "http://paperless.localhost:21946",
        )

    def test_generated_environment_allows_explicit_tailscale_qa_host(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / ".pocket-id.env"
            with patch.dict(
                os.environ,
                {
                    "USL_POCKET_ID_DEV_PRIVATE_QA": "1",
                    "USL_POCKET_ID_DEV_ODOO_HOSTNAME": "100.100.10.20",
                    "USL_POCKET_ID_DEV_POCKET_HOSTNAME": "100.100.10.20",
                    "USL_POCKET_ID_DEV_PAPERLESS_URL": (
                        "http://100.100.10.20:21946"
                    ),
                },
                clear=True,
            ):
                POCKET_ID_DEV._write_new_env(path)
            values = POCKET_ID_DEV._read_env(path)

        self.assertEqual(
            values["ODOO_PUBLIC_BASE_URL"],
            "http://100.100.10.20:8069",
        )
        self.assertEqual(
            values["POCKET_ID_APP_URL"],
            "http://100.100.10.20:1411",
        )
        self.assertIn("100.100.10.20", values["PAPERLESS_ALLOWED_HOSTS"])

    def test_generated_environment_rejects_unapproved_remote_host(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / ".pocket-id.env"
            with (
                patch.dict(
                    os.environ,
                    {"USL_POCKET_ID_DEV_ODOO_HOSTNAME": "100.100.10.20"},
                    clear=True,
                ),
                self.assertRaisesRegex(
                    POCKET_ID_DEV.PocketIDError,
                    "explicit private QA",
                ),
            ):
                POCKET_ID_DEV._write_new_env(path)

    def test_admin_api_can_use_a_private_origin_separate_from_public_issuer(self):
        values = {
            "POCKET_ID_APP_URL": "https://pocket-id.example.test",
            "POCKET_ID_STATIC_API_KEY": "test-key",
        }
        with patch.dict(
            os.environ,
            {"USL_POCKET_ID_ADMIN_API_URL": "http://100.79.30.44:19025"},
            clear=True,
        ):
            api = POCKET_ID_DEV.PocketIDAPI(values)

        self.assertEqual(api.base_url, "http://100.79.30.44:19025")

    def test_admin_api_override_rejects_a_public_origin(self):
        values = {
            "POCKET_ID_APP_URL": "https://pocket-id.example.test",
            "POCKET_ID_STATIC_API_KEY": "test-key",
        }
        with (
            patch.dict(
                os.environ,
                {"USL_POCKET_ID_ADMIN_API_URL": "https://api.example.test"},
                clear=True,
            ),
            self.assertRaisesRegex(
                POCKET_ID_DEV.PocketIDError,
                "private or localhost",
            ),
        ):
            POCKET_ID_DEV.PocketIDAPI(values)

    def test_existing_environment_is_upgraded_with_separate_paperless_client(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / ".pocket-id.env"
            with patch.dict(os.environ, {}, clear=True):
                POCKET_ID_DEV._write_new_env(path)
            original = POCKET_ID_DEV._read_env(path)
            content = "\n".join(
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if not line.startswith(
                    (
                        "PAPERLESS_PUBLIC_BASE_URL=",
                        "POCKET_ID_PAPERLESS_CLIENT_ID=",
                        "POCKET_ID_PAPERLESS_CLIENT_SECRET=",
                    ),
                )
            )
            path.write_text(content + "\n", encoding="utf-8")
            path.chmod(0o600)

            POCKET_ID_DEV._write_new_env(path)
            upgraded = POCKET_ID_DEV._read_env(path)

        self.assertEqual(
            upgraded["POCKET_ID_PAPERLESS_CLIENT_ID"],
            "usl-paperless-preproduction",
        )
        self.assertNotEqual(
            original["POCKET_ID_PAPERLESS_CLIENT_SECRET"],
            upgraded["POCKET_ID_PAPERLESS_CLIENT_SECRET"],
        )

    def test_paperless_client_has_distinct_supported_oidc_callback(self):
        payload = POCKET_ID_DEV._paperless_client_payload(
            {
                "PAPERLESS_PUBLIC_BASE_URL": "https://documents.example.test/",
                "POCKET_ID_PAPERLESS_CLIENT_ID": "paperless-client",
            },
        )

        self.assertEqual(payload["id"], "paperless-client")
        self.assertEqual(
            payload["callbackURLs"],
            [
                (
                    "https://documents.example.test/"
                    "accounts/oidc/pocket-id/login/callback/"
                ),
            ],
        )
        self.assertTrue(payload["pkceEnabled"])
        self.assertTrue(payload["isGroupRestricted"])

    def test_extra_qa_users_are_validated_and_provisioned(self):
        payloads = POCKET_ID_DEV._extra_user_payloads(
            {
                "POCKET_ID_EXTRA_USERS_JSON": json.dumps(
                    [
                        {
                            "username": "documents-user",
                            "id": "documents-subject",
                            "email": "documents-user@example.test",
                            "display_name": "Documents User",
                        },
                    ],
                ),
                "POCKET_ID_VALENTIN_ID": "valentin-subject",
                "POCKET_ID_ROGER_ID": "roger-subject",
                "POCKET_ID_PROSPER_ID": "prosper-subject",
            },
            "documents-group",
        )

        self.assertEqual(payloads[0]["id"], "documents-subject")
        self.assertEqual(payloads[0]["userGroupIds"], ["documents-group"])

    def test_client_secret_uses_pocket_id_multiple_secret_api(self):
        api = Mock()
        api.request.side_effect = [
            [],
            {
                "id": "secret-id",
                "prefix": "qa-s",
                "isActive": True,
                "secret": "qa-secret-value-for-tests",
            },
        ]

        POCKET_ID_DEV._ensure_client_secret(
            api,
            "odoo-client",
            "qa-secret-value-for-tests",
            existing=True,
            client={},
        )

        self.assertEqual(
            api.request.call_args_list,
            [
                unittest.mock.call(
                    "GET",
                    "/api/oidc/clients/odoo-client/secrets",
                ),
                unittest.mock.call(
                    "POST",
                    "/api/oidc/clients/odoo-client/secrets",
                    {"secret": "qa-secret-value-for-tests"},
                ),
            ],
        )

    def test_client_secret_is_not_rotated_when_active_prefix_matches(self):
        api = Mock()
        api.request.return_value = [
            {"id": "secret-id", "prefix": "qa-s", "isActive": True},
        ]

        POCKET_ID_DEV._ensure_client_secret(
            api,
            "odoo-client",
            "qa-secret-value-for-tests",
            existing=True,
            client={},
        )

        api.request.assert_called_once_with(
            "GET",
            "/api/oidc/clients/odoo-client/secrets",
        )

    def test_policy_reuses_source_aligned_logins_without_synthetic_odoo_email(self):
        values = {
            "POCKET_ID_PROSPER_EMAIL": "prosper@preproduction.invalid",
            "POCKET_ID_PROSPER_ODOO_EMAIL": "",
            "POCKET_ID_PROSPER_ID": "prosper-subject",
            "POCKET_ID_ROGER_ID": "roger-subject",
            "POCKET_ID_VALENTIN_ID": "valentin-subject",
        }
        with patch("builtins.print") as print_mock:
            POCKET_ID_DEV.odoo_policy(values)
        policy = json.loads(print_mock.call_args.args[0])
        users_by_profile = {entry["profile"]: entry for entry in policy}

        self.assertEqual(
            users_by_profile["product_administrator"]["login"],
            "roger@unstaticlabs.com",
        )
        self.assertEqual(
            users_by_profile["product_administrator"]["default_company"],
            "Unstatic Labs",
        )
        self.assertEqual(
            users_by_profile["accountant_reviewer"]["login"],
            "prosper",
        )
        self.assertNotIn("email", users_by_profile["accountant_reviewer"])
        self.assertFalse(
            users_by_profile["accountant_reviewer"]["create_if_missing"],
        )

    def test_sign_qa_policy_uses_profiles_owned_by_the_base_sso_module(self):
        values = {
            "POCKET_ID_PROSPER_EMAIL": "prosper@preproduction.invalid",
            "POCKET_ID_PROSPER_ODOO_EMAIL": "",
            "POCKET_ID_PROSPER_ID": "prosper-subject",
            "POCKET_ID_ROGER_ID": "roger-subject",
            "POCKET_ID_VALENTIN_ID": "valentin-subject",
        }
        with (
            patch.dict(
                os.environ,
                {"USL_POCKET_ID_POLICY_BASE_PROFILES_ONLY": "1"},
                clear=True,
            ),
            patch("builtins.print") as print_mock,
        ):
            POCKET_ID_DEV.odoo_policy(values)
        policy = json.loads(print_mock.call_args.args[0])
        users_by_login = {entry["login"]: entry for entry in policy}

        self.assertEqual(
            users_by_login["roger@unstaticlabs.com"]["profile"],
            "collaborator",
        )
        self.assertEqual(users_by_login["prosper"]["profile"], "collaborator")

    def test_paperless_policy_uses_immutable_pocket_people(self):
        values = {
            "POCKET_ID_PROSPER_EMAIL": "prosper@example.test",
            "POCKET_ID_PROSPER_ID": "prosper-subject",
            "POCKET_ID_ROGER_ID": "roger-subject",
            "POCKET_ID_VALENTIN_ID": "valentin-subject",
        }
        with patch("builtins.print") as print_mock:
            POCKET_ID_DEV.paperless_policy(values)
        policy = json.loads(print_mock.call_args.args[0])
        users = {entry["username"]: entry for entry in policy}

        self.assertEqual(set(users), {"valentin", "roger", "prosper"})
        self.assertEqual(users["valentin"]["subject"], "valentin-subject")
        self.assertEqual(users["roger"]["email"], "roger@unstaticlabs.com")
        self.assertEqual(users["prosper"]["subject"], "prosper-subject")
        self.assertEqual(users["prosper"]["email"], "prosper@example.test")

    def test_noncanonical_database_cannot_be_selected_as_target(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / ".pocket-id.env"
            with (
                patch.dict(
                    os.environ,
                    {"USL_POCKET_ID_DEV_ODOO_DB": "odoo_dev_sso_qa"},
                    clear=True,
                ),
                self.assertRaisesRegex(
                    POCKET_ID_DEV.PocketIDError,
                    "canonical odoo_dev",
                ),
            ):
                POCKET_ID_DEV._write_new_env(path)

    def test_local_pocket_helper_has_no_database_clone_lifecycle(self):
        script = (ROOT / "scripts" / "pocket-id-dev").read_text(
            encoding="utf-8",
        )

        self.assertNotIn("cleanup-qa-clone", script)
        self.assertNotIn("createdb", script)
        self.assertNotIn("dropdb", script)
        self.assertIn("canonical odoo_dev", script)
        self.assertNotIn("--git-common-dir", script)
        self.assertIn(
            'DEFAULT_ENV_FILE="$ROOT/.pocket-id.env"',
            script,
        )
        self.assertIn("usl_verify_compose_scope", script)
        self.assertIn(
            "Linked-worktree Pocket ID bootstrap requires",
            script,
        )
        self.assertIn("Use the configured QA environment with:", script)
        self.assertIn(
            "make COMPOSE_PROJECT=%s login-link USER=%s",
            script,
        )
        self.assertIn('POCKET_ID_LOGIN_HINT_USER="$username"', script)
        self.assertIn("USL_POCKET_ID_DEV_PAPERLESS_PORT", script)
        self.assertIn("requested_paperless_http_port", script)
        self.assertIn(
            "local -a init_modules=() requested_modules=() "
            "update_modules=() module_args=()",
            script,
        )
        for name in (
            "init_modules",
            "requested_modules",
            "update_modules",
            "module_args",
        ):
            self.assertIn(f"{name}=()", script)
        self.assertIn('module_args+=("--init=', script)
        self.assertIn('module_args+=("--update=', script)
        self.assertIn("init_modules=()", script)
        self.assertIn("update_modules=()", script)
        self.assertLess(
            script.index("init_modules=()"),
            script.index("${#init_modules[@]}"),
        )
        self.assertLess(
            script.index("update_modules=()"),
            script.index("${#update_modules[@]}"),
        )
        self.assertIn("SELECT state FROM ir_module_module", script)
        self.assertIn(
            'export PAPERLESS_HTTP_PORT="$requested_paperless_http_port"',
            script,
        )

    def test_login_link_resolves_any_exact_pocket_username(self):
        api = Mock()
        api.request.return_value = {
            "data": [
                {"id": "other-subject", "username": "other.user"},
                {"id": "requested-subject", "username": "finance.operator"},
            ],
        }

        user = POCKET_ID_DEV._find_user(api, {}, "finance.operator")

        self.assertEqual(user["id"], "requested-subject")
        api.request.assert_called_once_with(
            "GET",
            "/api/users?pagination%5Blimit%5D=100",
        )

    def test_login_link_rejects_missing_or_ambiguous_username(self):
        api = Mock()
        api.request.return_value = {
            "data": [
                {"id": "first", "username": "duplicate"},
                {"id": "second", "username": "duplicate"},
            ],
        }
        with self.assertRaisesRegex(
            POCKET_ID_DEV.PocketIDError,
            "ambiguous",
        ):
            POCKET_ID_DEV._find_user(api, {}, "duplicate")

        api.request.return_value = {"data": []}
        with self.assertRaisesRegex(
            POCKET_ID_DEV.PocketIDError,
            "not provisioned",
        ):
            POCKET_ID_DEV._find_user(api, {}, "missing")

    def test_make_login_link_requires_an_explicit_username(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

        self.assertIn("login-link:", makefile)
        self.assertIn('if [ "$(origin USER)" != "command line" ]', makefile)
        self.assertIn(
            'scripts/pocket-id-dev one-time-link "$(USER)"',
            makefile,
        )

    def test_documents_compose_bootstraps_bounded_paperless_sso_access(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        pocket_overlay = (ROOT / "compose.pocket-id.yaml").read_text(
            encoding="utf-8",
        )
        initializer = (
            ROOT / "deploy" / "documents" / "paperless_access_init.py"
        ).read_text(encoding="utf-8")
        identity_initializer = (
            ROOT / "deploy" / "documents" / "paperless_identity_sync.py"
        ).read_text(encoding="utf-8")
        pocket_helper = (ROOT / "scripts" / "pocket-id-dev").read_text(
            encoding="utf-8",
        )

        self.assertIn("paperless-access-init:", compose)
        self.assertIn(
            "PAPERLESS_SOCIAL_ACCOUNT_DEFAULT_GROUPS:",
            pocket_overlay,
        )
        self.assertIn(
            "PAPERLESS_ACCOUNT_DEFAULT_HTTP_PROTOCOL:",
            pocket_overlay,
        )
        self.assertEqual(
            pocket_overlay.count("POCKET_ID_EXTRA_USERS_JSON: >-"),
            2,
        )
        self.assertIn('provider="pocket-id"', initializer)
        self.assertIn('f"view_{model}"', initializer)
        self.assertNotIn("view_user", initializer)
        self.assertNotIn("change_document", initializer)
        self.assertIn("paperless-identity-init:", compose)
        self.assertIn("transaction.atomic()", identity_initializer)
        self.assertIn("provider=provider_id", identity_initializer)
        self.assertIn("SocialAccount.objects.create", identity_initializer)
        self.assertIn('"usl_odoo_managed": True', identity_initializer)
        self.assertIn("account.user.is_active = False", identity_initializer)
        self.assertIn("sync_paperless_identities", pocket_helper)
        configure_body = pocket_helper[
            pocket_helper.index("configure_odoo() {") :
            pocket_helper.index("\nsync_paperless_users() {")
        ]
        self.assertIn("sync_paperless_identities", configure_body)
        self.assertIn("configure_documents_runtime", configure_body)
        sync_users_body = pocket_helper[
            pocket_helper.index("\nsync_paperless_users() {") :
            pocket_helper.index("\nstart_runtime() {")
        ]
        self.assertIn("configure_documents_runtime", sync_users_body)
        self.assertLess(
            sync_users_body.index("configure_documents_runtime"),
            sync_users_body.index("sync_paperless_identities"),
        )
        integration_access = (
            ROOT / "deploy" / "documents" / "paperless_integration_access.py"
        ).read_text(encoding="utf-8")
        self.assertIn('username = "odoo-integration"', integration_access)
        self.assertIn('migration_username = "odoo-migration"', integration_access)
        self.assertIn(
            "Document.global_objects.filter(owner=migration_user).update(owner=user)",
            integration_access,
        )
        self.assertIn("USL_PAPERLESS_OWNERS_CLAIMED=", integration_access)

    def test_target_finalization_reconciles_paperless_people(self):
        finalizer = (ROOT / "migration" / "internal" / "finalize").read_text(
            encoding="utf-8",
        )
        apply_script = (
            ROOT / "scripts" / "odoo" / "documents_identity_apply.py"
        ).read_text(encoding="utf-8")

        self.assertIn("scripts/pocket-id-dev configure-odoo", finalizer)
        self.assertIn("_identity_is_safe", apply_script)
        self.assertIn('remote.get("is_active") is not True', apply_script)
        self.assertIn("action_sync_permissions", apply_script)
        self.assertIn("USL_PAPERLESS_FORCE_PERMISSION_SYNC", apply_script)
        self.assertIn("stale_mappings_disabled", apply_script)
        product_modules = {
            "rebuild_account_migration",
            "usl_accounting",
            "usl_documents",
            "usl_documents_accounting",
            "usl_expense_batch",
            "usl_locale",
            "usl_platform_billing",
            "usl_platform_billing_pocketid",
            "usl_pocketid",
            "usl_project",
            "usl_tese_accounting",
            "usl_tese_payroll",
        }
        for module_name in product_modules:
            with self.subTest(module_name=module_name):
                self.assertIn(module_name, finalizer)
        self.assertNotIn("--update=all", finalizer)

    def test_existing_pocket_environment_file_is_guarded(self):
        script = (ROOT / "scripts" / "pocket-id-dev").read_text(
            encoding="utf-8",
        )

        self.assertIn("verify_existing_pocket_environment", script)
        self.assertIn(
            'com.docker.compose.project.environment_file',
            script,
        )
        self.assertIn("No containers were changed.", script)

if __name__ == "__main__":
    unittest.main()
