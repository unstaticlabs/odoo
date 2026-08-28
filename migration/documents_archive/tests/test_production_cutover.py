import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "production_cutover",
    ROOT / "scripts/production_cutover.py",
)
cutover = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cutover)


class ProductionCutoverSafetyTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fingerprint = "a" * 64
        self.image = f"ghcr.io/usl/odoo@sha256:{'b' * 64}"
        self.paperless_image = f"ghcr.io/usl/paperless@sha256:{'c' * 64}"
        self.ollama_image = f"docker.io/ollama/ollama@sha256:{'d' * 64}"
        self.renderer_image = f"ghcr.io/usl/renderer@sha256:{'e' * 64}"
        self.step_ca_image = f"docker.io/smallstep/step-ca@sha256:{'f' * 64}"
        self.dss_image = f"ghcr.io/usl/sign-dss@sha256:{'1' * 64}"
        self.project = "usl-odoo-production-main"
        self.candidate = {
            "identity": {
                "image_digest": self.image,
                "paperless_image_digest": self.paperless_image,
                "ollama_image_digest": self.ollama_image,
            },
        }
        self.key_ring = self.root / "personal-ai-keys.json"
        self.key_ring.write_text("{}\n", encoding="utf-8")
        self.key_ring.chmod(0o600)
        self.sign_secret_directories = {}
        for key, required_files in cutover.SIGN_SECRET_DIRECTORIES.items():
            directory = self.root / key.lower()
            directory.mkdir(mode=0o700)
            for relative in required_files:
                path = directory / relative
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                path.write_text("qualified\n", encoding="utf-8")
            self.sign_secret_directories[key] = str(directory)
        self.values = {
            "COMPOSE_PROJECT_NAME": self.project,
            "USL_DEPLOYMENT_ENV": "production",
            "POSTGRES_PASSWORD": "p" * 32,
            "ODOO_ADMIN_PASSWORD": "a" * 32,
            "ODOO_DB_PASSWORD": "p" * 32,
            "ODOO_DB_NAME": "odoo_production",
            "ODOO_DB_MAXCONN": "12",
            "ODOO_INIT_DB": "odoo_production",
            "ODOO_DB_FILTER": "^odoo_production$",
            "ODOO_GEVENT_PORT": "18072",
            "ODOO_HTTP_PORT": "18069",
            "ODOO_IMAGE": self.image,
            "ODOO_LIST_DB": "False",
            "ODOO_LIMIT_MEMORY_HARD": "1342177280",
            "ODOO_LIMIT_MEMORY_SOFT": "1073741824",
            "ODOO_LIMIT_REQUEST": "8192",
            "ODOO_MAX_CRON_THREADS": "0",
            "ODOO_PUBLIC_BASE_URL": "https://odoo.usl.example",
            "ODOO_WORKERS": "4",
            "PAPERLESS_ALLOWED_HOSTS": "documents.usl.example,paperless-webserver",
            "PAPERLESS_DB_NAME": "paperless",
            "PAPERLESS_DB_PASSWORD": "d" * 32,
            "PAPERLESS_DB_USER": "paperless",
            "PAPERLESS_HTTP_PORT": "18010",
            "PAPERLESS_IMAGE": self.paperless_image,
            "PAPERLESS_SECRET_KEY": "s" * 64,
            "PAPERLESS_PUBLIC_URL": "https://documents.usl.example",
            "PAPERLESS_PUBLIC_BASE_URL": "https://documents.usl.example",
            "PAPERLESS_SSO_BASE_GROUP": "USL Odoo document users",
            "OLLAMA_IMAGE": self.ollama_image,
            "POCKET_ID_APP_URL": "https://identity.usl.example",
            "POCKET_ID_CLIENT_ID": "usl-odoo-production",
            "POCKET_ID_CLIENT_SECRET": "o" * 32,
            "POCKET_ID_GROUP_NAME": "odoo-production",
            "POCKET_ID_PAPERLESS_CLIENT_ID": "usl-paperless-production",
            "POCKET_ID_PAPERLESS_CLIENT_SECRET": "q" * 32,
            "USL_EINVOICE_LIVE_ENABLED": "0",
            "USL_EREPORTING_LIVE_ENABLED": "0",
            "USL_EXTERNAL_IDENTITY_NETWORK": "identity-production",
            "USL_EXTERNAL_INGRESS_NETWORK": "ingress-production",
            "USL_POCKET_ID_BREAK_GLASS_PASSWORD": "g" * 32,
            "USL_PRODUCTION_CRON_THREADS": "1",
            "USL_PERSONAL_AI_MASTER_KEYS_HOST_PATH": str(self.key_ring),
            "USL_DOCUMENT_RENDERER_CERT_DIR": self.sign_secret_directories[
                "USL_DOCUMENT_RENDERER_CERT_DIR"
            ],
            "USL_DOCUMENT_RENDERER_IMAGE": self.renderer_image,
            "USL_SIGN_DSS_IMAGE": self.dss_image,
            "USL_SIGN_DSS_SECRET_DIR": self.sign_secret_directories[
                "USL_SIGN_DSS_SECRET_DIR"
            ],
            "USL_SIGN_ODOO_SECRET_DIR": self.sign_secret_directories[
                "USL_SIGN_ODOO_SECRET_DIR"
            ],
            "USL_SIGN_STEP_CA_DIR": self.sign_secret_directories[
                "USL_SIGN_STEP_CA_DIR"
            ],
            "USL_SIGN_STEP_CA_IMAGE": self.step_ca_image,
        }
        for key in cutover.VOLUME_KEYS:
            suffix = key.lower().removeprefix("usl_").replace("_volume", "").replace("_", "-")
            self.values[key] = f"{self.project}-{suffix}"

    def tearDown(self):
        self.temporary.cleanup()

    def test_preproduction_overlay_uses_packaged_distribution_paths(self):
        overlay = (ROOT / "compose.preprod.yaml").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        packaged_addons = (
            "/opt/odoo/addons,/opt/odoo/odoo/addons,"
            "/opt/odoo/custom-addons,/opt/odoo/oca-addons"
        )
        self.assertEqual(overlay.count(f"ODOO_ADDONS_PATH: {packaged_addons}"), 2)
        self.assertEqual(
            overlay.count("USL_USER_DOCS_PATH: /opt/odoo/docs/users"),
            2,
        )
        self.assertNotIn("/opt/usl/custom-addons", overlay)
        self.assertIn("COPY --link --chown=1000:1000 custom-addons ./custom-addons", dockerfile)
        self.assertIn("COPY --link --chown=1000:1000 docs/users ./docs/users", dockerfile)

    def test_preproduction_clean_install_enforces_product_module_scope(self):
        release_script = (ROOT / "scripts/preprod-release").read_text(
            encoding="utf-8",
        )
        clean_install = release_script.split("clean_install_product() {", 1)[1].split(
            "\n}\n\nreconstruct_target()",
            1,
        )[0]

        install_position = clean_install.index('--init="$product_modules"')
        scope_position = clean_install.index(
            'scripts/odoo/enforce_product_module_scope.py',
        )
        boundary_position = clean_install.index(
            'scripts/check-product-database-boundary',
        )
        self.assertLess(install_position, scope_position)
        self.assertLess(scope_position, boundary_position)

    def test_production_admission_owns_sign_services(self):
        overlay = (ROOT / "compose.preprod.yaml").read_text(encoding="utf-8")
        release_script = (ROOT / "scripts/production-cutover").read_text(
            encoding="utf-8",
        )

        self.assertIn("usl-document-renderer:\n", overlay)
        self.assertIn("usl-sign-dss:\n", overlay)
        self.assertGreaterEqual(overlay.count("build: !reset null"), 4)
        self.assertIn("start_sign_services()", release_script)
        self.assertIn("python /usr/local/bin/usl-sign-services-smoke", release_script)
        self.assertIn("--profile document-renderer config", release_script)

    def test_compose_passes_worker_budget_to_odoo(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        odoo_service = compose.split("\n  odoo:\n", 1)[1].split(
            "\n  init-db:\n",
            1,
        )[0]

        self.assertIn("ODOO_WORKERS: ${ODOO_WORKERS:-0}", odoo_service)

    def _private_json(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")
        path.chmod(0o600)
        return path

    def test_environment_is_strict_and_candidate_image_bound(self):
        cutover.validate_environment(self.values, self.candidate)

        self.values["ODOO_IMAGE"] = f"ghcr.io/usl/odoo@sha256:{'c' * 64}"
        with self.assertRaisesRegex(cutover.CutoverError, "approved candidate"):
            cutover.validate_environment(self.values, self.candidate)

    def test_unsafe_urls_database_manager_and_live_flags_are_rejected(self):
        for key, value, message in (
            ("POCKET_ID_APP_URL", "http://identity.usl.example", "HTTPS origin"),
            ("ODOO_LIST_DB", "True", "database manager"),
            ("USL_EINVOICE_LIVE_ENABLED", "1", "live flags"),
            ("ODOO_DB_NAME", "odoo_dev", "database name"),
        ):
            with self.subTest(key=key):
                changed = dict(self.values)
                changed[key] = value
                with self.assertRaisesRegex(cutover.CutoverError, message):
                    cutover.validate_environment(changed, self.candidate)

    def test_documents_runtime_and_private_key_are_candidate_bound(self):
        for key, value, message in (
            (
                "PAPERLESS_IMAGE",
                f"ghcr.io/usl/paperless@sha256:{'e' * 64}",
                "approved candidate",
            ),
            ("OLLAMA_IMAGE", "ollama/ollama:latest", "not immutable"),
            (
                "USL_PERSONAL_AI_MASTER_KEYS_HOST_PATH",
                "relative-key-ring.json",
                "must be absolute",
            ),
        ):
            with self.subTest(key=key):
                changed = dict(self.values)
                changed[key] = value
                with self.assertRaisesRegex(cutover.CutoverError, message):
                    cutover.validate_environment(changed, self.candidate)

        self.key_ring.chmod(0o640)
        with self.assertRaisesRegex(cutover.CutoverError, "0600"):
            cutover.validate_environment(self.values, self.candidate)

    def test_sign_images_and_secret_directories_fail_closed(self):
        changed = dict(self.values, USL_SIGN_DSS_IMAGE="usl-sign-dss:latest")
        with self.assertRaisesRegex(cutover.CutoverError, "not immutable"):
            cutover.validate_environment(changed, self.candidate)

        renderer = Path(self.values["USL_DOCUMENT_RENDERER_CERT_DIR"])
        (renderer / "renderer.key").unlink()
        with self.assertRaisesRegex(cutover.CutoverError, "incomplete"):
            cutover.validate_environment(self.values, self.candidate)

    def test_unsafe_odoo_resource_budgets_are_rejected(self):
        for key, value, message in (
            ("ODOO_WORKERS", "0", "HTTP workers"),
            ("ODOO_DB_MAXCONN", "33", "database pool"),
            ("ODOO_DB_MAXCONN", "14", "connection budget"),
            ("ODOO_LIMIT_MEMORY_SOFT", "536870911", "memory limits"),
            ("ODOO_LIMIT_MEMORY_HARD", "1140850688", "128 MiB headroom"),
            ("ODOO_LIMIT_REQUEST", "999", "request recycling"),
        ):
            with self.subTest(key=key, value=value):
                changed = dict(self.values)
                changed[key] = value
                with self.assertRaisesRegex(cutover.CutoverError, message):
                    cutover.validate_environment(changed, self.candidate)

    def test_non_integer_odoo_resource_budget_is_rejected(self):
        changed = dict(self.values)
        changed["ODOO_DB_MAXCONN"] = "many"

        with self.assertRaisesRegex(cutover.CutoverError, "must be integers"):
            cutover.validate_environment(changed, self.candidate)

    def test_compose_rejects_managed_pocket_and_public_staging_ports(self):
        config = {
            "services": {
                "odoo": {
                    "image": self.image,
                    "ports": [{"host_ip": "127.0.0.1"}],
                    "networks": {"external-identity": None, "external-ingress": None},
                },
                "paperless-webserver": {
                    "image": self.paperless_image,
                    "ports": [{"host_ip": "127.0.0.1"}],
                    "networks": {"external-identity": None, "external-ingress": None},
                },
                "paperless-ollama": {"image": self.ollama_image},
                "usl-document-renderer": {"image": self.renderer_image},
                "usl-sign-dss": {"image": self.dss_image},
                "usl-sign-step-ca": {"image": self.step_ca_image},
            },
            "networks": {
                "identity": {"name": "identity-production", "external": True},
                "ingress": {"name": "ingress-production", "external": True},
            },
            "volumes": {
                str(index): {
                    "name": self.values[key],
                    "labels": {
                        "com.unstaticlabs.migration.project": self.project,
                    },
                }
                for index, key in enumerate(cutover.VOLUME_KEYS)
            },
        }
        cutover.validate_compose(config, self.values)

        config["services"]["pocket-id"] = {"image": "pocket-id"}
        with self.assertRaisesRegex(cutover.CutoverError, "managed Pocket"):
            cutover.validate_compose(config, self.values)
        del config["services"]["pocket-id"]
        config["services"]["odoo"]["ports"][0]["host_ip"] = "0.0.0.0"
        with self.assertRaisesRegex(cutover.CutoverError, "loopback"):
            cutover.validate_compose(config, self.values)

    def test_foreign_and_non_empty_volumes_are_rejected(self):
        volume = {
            "name": self.values[cutover.VOLUME_KEYS[0]],
            "labels": {"com.unstaticlabs.migration.project": self.project},
            "file_count": 0,
        }
        cutover.validate_volume_state([volume], self.values, require_empty=True)

        foreign = dict(volume, labels={"com.unstaticlabs.migration.project": "other"})
        with self.assertRaisesRegex(cutover.CutoverError, "foreign/unowned"):
            cutover.validate_volume_state([foreign], self.values, require_empty=True)
        nonempty = dict(volume, file_count=1)
        with self.assertRaisesRegex(cutover.CutoverError, "not empty"):
            cutover.validate_volume_state([nonempty], self.values, require_empty=True)

    def test_reset_restage_and_permanent_refusal_after_admission(self):
        state = self.root / "state.json"
        cutover.transition(state, self.fingerprint, "preflight")
        cutover.transition(state, self.fingerprint, "stage")
        cutover.transition(state, self.fingerprint, "reset")
        cutover.transition(state, self.fingerprint, "stage")
        cutover.transition(state, self.fingerprint, "configure")
        cutover.transition(state, self.fingerprint, "gate")
        admitted = cutover.transition(state, self.fingerprint, "admit")
        self.assertFalse(admitted["reset_allowed"])
        with self.assertRaisesRegex(cutover.CutoverError, "cannot reset"):
            cutover.transition(state, self.fingerprint, "reset")

    def test_policy_is_mode_0600_and_candidate_bound(self):
        policy = self._private_json("identity-policy.json", {
            "schema": cutover.POLICY_SCHEMA,
            "candidate_fingerprint": self.fingerprint,
            "approved_cron_xmlids": [],
            "outbound_integrations_enabled": False,
            "odoo_users": [{"login": "admin", "profile": "break_glass", "companies": "all"}],
            "paperless_identities": [{
                "subject": "subject",
                "username": "user",
                "email": "user@usl.example",
                "display_name": "User",
            }],
        })
        self.assertEqual(
            cutover.validate_policy(policy, self.fingerprint)["schema"],
            cutover.POLICY_SCHEMA,
        )
        os.chmod(policy, 0o640)
        with self.assertRaisesRegex(cutover.CutoverError, "0600"):
            cutover.validate_policy(policy, self.fingerprint)

    def test_browser_journeys_prove_required_roles_and_unchanged_pocket_state(self):
        evidence = self._private_json("journeys.json", {
            "schema": cutover.JOURNEY_SCHEMA,
            "candidate_fingerprint": self.fingerprint,
            "status": "passed",
            "external_pocket_state_before_sha256": "d" * 64,
            "external_pocket_state_after_sha256": "d" * 64,
            "journeys": [
                {"name": name, "status": "passed"}
                for name in (
                    "odoo_administrator",
                    "odoo_collaborator",
                    "accounting_read_only",
                    "multi_company_isolation",
                    "paperless_documents",
                )
            ],
        })
        self.assertEqual(
            cutover.validate_journeys(evidence, self.fingerprint)["status"],
            "passed",
        )
        value = json.loads(evidence.read_text(encoding="utf-8"))
        value["external_pocket_state_after_sha256"] = "e" * 64
        evidence.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(cutover.CutoverError, "Pocket ID state changed"):
            cutover.validate_journeys(evidence, self.fingerprint)


if __name__ == "__main__":
    unittest.main()
