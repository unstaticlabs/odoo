"""Run the generation loader against disposable, network-isolated PostgreSQL 16."""
import json
import os
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from operations import cohort
from operations.runtime import CommandRunner, Target
from operations.stack import (
    _generation_database_entrypoint, _start_generation_database, _start_recovery_proof_database,
)


@unittest.skipUnless(os.environ.get("USL_GENERATION_DATABASE_QA") == "1", "native PostgreSQL QA is opt-in")
class GenerationDatabaseTests(unittest.TestCase):
    role = "fixture_writer"
    db = "fixture_odoo"
    password = "fixture-only"
    image = os.environ.get("USL_QA_POSTGRES_IMAGE", "postgres:16-bookworm")

    def test_release_and_recovery_each_database_receives_only_its_own_password(self):
        project = "usl-pg-env-" + uuid.uuid4().hex[:12]
        network = project + "-isolated"
        subprocess.run(["docker", "network", "create", "--internal", network], check=True, capture_output=True)
        self.addCleanup(subprocess.run, ["docker", "network", "rm", network], capture_output=True)
        self.password = "  fixture # \"double\" '$DOLLAR' = back\\slash  "
        passwords = {"ODOO_DB_PASSWORD": self.password, "PAPERLESS_DB_PASSWORD": "other # fixture value"}
        roles = {
            "odoo": {"name": self.db, "user": self.role, "password_key": "ODOO_DB_PASSWORD", "service": "db"},
            "paperless": {"name": "fixture_docs", "user": "fixture_docs", "password_key": "PAPERLESS_DB_PASSWORD", "service": "docs-db"},
        }
        with tempfile.TemporaryDirectory() as directory:
            release_env = Path(directory) / "release.env"
            common = "".join(f"{key}={value}\r\n" for key, value in passwords.items())
            release_env.write_bytes(("\ufeff" + common + "FIXTURE_OFFSITE_KEY=unrelated\n").encode())
            proof_env = Path(directory) / "proof.env"
            proof_env.write_text(common + "PAPERLESS_DBPASS=other # fixture value\n")
            target = Target(release_env, {
                "compose": {"project": project}, "secrets": {"env_file": str(release_env)},
                "databases": roles, "services": {"odoo_db": "db", "paperless_db": "docs-db"},
            })
            runner = CommandRunner()
            sources = {}
            for stage in ("release", "proof"):
                for key, database in roles.items():
                    role = key + "_db"
                    name = f"{project}-{stage}-{key}-db"
                    volume = name + "-data"
                    subprocess.run(["docker", "volume", "create", volume], check=True, capture_output=True)
                    self.addCleanup(subprocess.run, ["docker", "volume", "rm", volume], capture_output=True)
                    self.addCleanup(subprocess.run, ["docker", "rm", "-f", name], capture_output=True)
                    if stage == "release":
                        started = _start_generation_database(
                            target, runner, stage, network, volume, self.image, key, database["service"],
                        )
                    else:
                        names = {"network": network, "containers": {role: name},
                                 "volumes": {key + "_postgres": volume}}
                        started = _start_recovery_proof_database(
                            target, runner, "fixture-proof", names, {database["service"]: self.image}, key, str(proof_env),
                        )
                    self.assertEqual(started, name)
                    actual = json.loads(runner.run(["docker", "inspect", name, "--format", "{{json .Config.Env}}"]).stdout)
                    source_values = dict(item.split("=", 1) for item in actual)
                    password_key = database["password_key"]
                    self.assertEqual(source_values[password_key], passwords[password_key])
                    self.assertEqual(source_values.keys() & (passwords.keys() | {"FIXTURE_OFFSITE_KEY", "PAPERLESS_DBPASS"}),
                                     {password_key})
                    if key == "odoo":
                        sources[stage] = name
            # Exercise the normal prepared-database restore, with the literal
            # password checked by native PostgreSQL during the TCP connection.
            self.sql(sources["release"], "CREATE TABLE env_fixture (id integer); INSERT INTO env_fixture VALUES (7)")
            backup_dir = Path(directory) / "archive"
            backup_dir.mkdir()
            destination = backup_dir / "fixture.dump"

            def backup_client(command, *, environment=None, capture=False):
                return subprocess.run([
                    "docker", "run", "--rm", "--network", "container:" + sources["release"],
                    "--volume", f"{backup_dir}:{backup_dir}", "--env", "PGPASSWORD",
                    self.image, *command,
                ], env=environment, check=True, text=True, capture_output=True)

            with patch.dict(os.environ, {
                "ODOO_DB_NAME": self.db, "ODOO_DB_USER": self.role, "ODOO_DB_PASSWORD": self.password,
                "ODOO_DB_HOST": "127.0.0.1", "ODOO_DB_PORT": "5432",
            }), patch.object(cohort, "run", backup_client):
                receipt = cohort.dump_database("ODOO", destination)
            self.assertEqual(receipt["sha256"], cohort.sha256_file(destination))
            self.materialize(sources["proof"], destination.read_bytes())
            self.assertEqual(self.sql(sources["proof"], "SELECT id FROM env_fixture"), "7")

    def new_generation(self, *, extra_mounts=()):
        name = "usl-generation-qa-" + uuid.uuid4().hex[:12]
        database = {"name": self.db, "user": self.role, "password_key": "FIXTURE_PASSWORD"}
        subprocess.run([
            "docker", "run", "--detach", "--rm", "--network", "none", "--name", name,
            "--tmpfs", "/var/lib/postgresql/data", "--env", f"FIXTURE_PASSWORD={self.password}", *extra_mounts,
            *_generation_database_entrypoint("odoo", database, self.image),
        ], check=True, capture_output=True)
        self.addCleanup(subprocess.run, ["docker", "stop", name], check=False, capture_output=True)
        for _ in range(60):
            if subprocess.run(["docker", "exec", name, "pg_isready", "-h", "127.0.0.1", "-d", self.db],
                              capture_output=True).returncode == 0:
                return name
            time.sleep(1)
        self.fail("fixture did not start")

    def sql(self, container, statement):
        return subprocess.check_output([
            "docker", "exec", "-e", "PGPASSWORD", container, "psql", "-XAtq",
            "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1", "-U", self.role, "-d", self.db, "-c", statement,
        ], text=True, env={**os.environ, "PGPASSWORD": self.password}).strip()

    def materialize(self, container, archive):
        subprocess.run(["docker", "exec", "-i", container, "sh", "-c", "cat >/tmp/synthetic.dump"],
                       input=archive, check=True)
        environment = {
            "ODOO_DB_NAME": self.db, "ODOO_DB_USER": self.role, "ODOO_DB_PASSWORD": self.password,
            "ODOO_DB_HOST": "127.0.0.1", "ODOO_DB_PORT": "5432", "USL_RESTORE_ODOO_PREPARED_DATABASE": "1",
            "USL_RESTORE_GENERATION_CONFIRMED": "gfixture", "USL_EXPECTED_RESTORE_CONFIRMATION": "gfixture",
        }
        def in_container(command, *, environment, capture=False):
            return subprocess.run(["docker", "exec", "-e", "PGPASSWORD", container, *command],
                                  env=environment, check=True, text=True, capture_output=True)
        with patch.dict(os.environ, environment), patch.object(cohort, "run", in_container):
            cohort._reset_database("ODOO", Path("/tmp/synthetic.dump"))

    def test_restore_upgrades_and_local_operator(self):
        original = self.new_generation()
        restored = self.new_generation()
        for name in (original, restored):
            self.assertEqual(self.sql(name, """
                SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
                FROM pg_roles WHERE rolname = current_user"""), "f|f|f|f|f")
            self.assertEqual(self.sql(name, """
                SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()"""), "postgres")
            local = subprocess.check_output(["docker", "exec", name, "psql", "-XAtq", "-U", self.role, "-d",
                                             self.db, "-c", "SELECT current_user"], text=True)
            self.assertEqual(local.strip(), self.role)
            state = subprocess.check_output(["docker", "exec", "--user", "postgres", name, "psql", "-XAtq",
                                            "-d", "postgres", "-c", "SELECT rolpassword IS NULL FROM pg_authid "
                                            "WHERE rolname=current_user"], text=True)
            self.assertEqual(state.strip(), "t")
        self.sql(original, "CREATE EXTENSION pg_trgm; CREATE EXTENSION unaccent; "
                 "CREATE TABLE fixture (id integer PRIMARY KEY); INSERT INTO fixture VALUES(7)")
        for _ in range(2):
            self.sql(original, "ALTER TABLE fixture ADD COLUMN IF NOT EXISTS verified boolean DEFAULT true")
        archive = subprocess.check_output(["docker", "exec", original, "pg_dump", "-U", self.role, "-Fc", self.db])
        self.materialize(restored, archive)
        self.assertEqual(self.sql(restored, "SELECT id,verified FROM fixture"), "7|t")
        self.assertEqual(self.sql(restored, "SELECT tableowner FROM pg_tables WHERE tablename='fixture'"), self.role)
        self.assertEqual(self.sql(restored, "SELECT string_agg(extname,',' ORDER BY extname) FROM pg_extension"),
                         "pg_trgm,plpgsql,unaccent")
        with self.assertRaisesRegex(cohort.CohortError, "empty"):
            self.materialize(restored, archive)
        self.assertEqual(self.sql(restored, "SELECT count(*) FROM fixture"), "1")

    def test_keeps_image_initializers_before_and_after_ordinary_role_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            mounts = []
            for key, file in (("before", "10-fixture.sql"), ("after", "95-fixture.sql")):
                path = Path(directory) / file
                path.write_text(f"ALTER DATABASE {self.db} SET fixture.{key}='present';\n")
                mounts.extend(["--volume", f"{path}:/docker-entrypoint-initdb.d/{file}:ro"])
            database = self.new_generation(extra_mounts=mounts)
            self.assertEqual(self.sql(database, "SELECT current_setting('fixture.before'), current_setting('fixture.after')"),
                             "present|present")

    def test_old_archive_and_runtime_rollback(self):
        # The release overlay is normally composed with the entire distribution.
        # Render it once, then run only the database in an isolated QA project.
        config = subprocess.check_output([
            "docker", "compose", "--env-file", ".env.example", "-f", "compose.yaml",
            "-f", "compose.production.yaml", "config", "--no-env-resolution", "--no-interpolate", "--format", "json",
        ])
        db = json.loads(config)["services"]["db"]
        runtime = {
            "image": self.image, "network_mode": "none", "entrypoint": db["entrypoint"],
            "environment": {"PGUSER": self.role, "PGDATABASE": "postgres"}, "volumes": ["data:/var/lib/postgresql/data"],
        }
        bootstrap = {**runtime, "entrypoint": None, "environment": {
            "POSTGRES_DB": self.db, "POSTGRES_USER": self.role, "POSTGRES_PASSWORD": self.password,
        }}
        project = "usl-role-rollback-" + uuid.uuid4().hex[:12]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "compose.json"
            cli = ["docker", "compose", "--project-name", project, "--file", str(path)]
            def configure(service):
                path.write_text(json.dumps({"services": {"db": service}, "volumes": {"data": {}}}))
            def start(service):
                configure(service)
                subprocess.run([*cli, "up", "--detach", "db"], check=True, capture_output=True)
                name = subprocess.check_output([*cli, "ps", "-q", "db"], text=True).strip()
                for _ in range(30):
                    ready = subprocess.run(["docker", "exec", name, "pg_isready", "-h", "127.0.0.1"],
                                           capture_output=True)
                    if ready.returncode == 0:
                        return name
                    time.sleep(1)
                self.fail("retained fixture did not start")
            try:
                configure(runtime)
                empty = subprocess.run([*cli, "run", "--rm", "db"], text=True, capture_output=True)
                self.assertNotEqual(empty.returncode, 0)
                self.assertIn("Restore the generation database", empty.stderr)
                original = start(bootstrap)
                self.sql(original, "CREATE TABLE preserved (id integer); INSERT INTO preserved VALUES(42)")
                archive = subprocess.check_output(["docker", "exec", original, "pg_dump", "-U", self.role, "-Fc", self.db])
                restored = self.new_generation()
                self.materialize(restored, archive)
                self.assertEqual(self.sql(restored, "SELECT id FROM preserved"), "42")
                reopened = start(runtime)
                self.assertEqual(self.sql(reopened, "SELECT id FROM preserved"), "42")
                self.assertEqual(self.sql(reopened, "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"), "t")
                self.assertEqual(self.sql(restored, "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"), "f")
            finally:
                subprocess.run([*cli, "down", "--volumes"], check=True, capture_output=True)

    @unittest.skipUnless(os.environ.get("USL_QA_ODOO_IMAGE"), "supply the Odoo image to exercise the registry")
    def test_odoo_install_two_upgrades_and_restored_registry(self):
        original = self.new_generation()
        restored = self.new_generation()
        def start_odoo(database):
            name = "usl-odoo-role-qa-" + uuid.uuid4().hex[:12]
            subprocess.run([
                "docker", "run", "--detach", "--rm", "--name", name, "--network", f"container:{database}",
                "--entrypoint", "sleep", os.environ["USL_QA_ODOO_IMAGE"], "infinity",
            ], check=True, capture_output=True)
            self.addCleanup(subprocess.run, ["docker", "stop", name], check=False, capture_output=True)
            return name
        odoo = [
            "/opt/odoo/odoo-bin", "--without-demo=all", "--db_host=127.0.0.1", f"--db_user={self.role}",
            f"--db_password={self.password}", f"--database={self.db}", "--data-dir=/tmp/odoo-role-qa",
            "--addons-path=/opt/odoo/odoo/addons,/opt/odoo/addons",
        ]
        def load(container, *action):
            command = ["docker", "exec", container, *odoo, "--stop-after-init", "--no-http", *action]
            result = subprocess.run(command, text=True, capture_output=True, timeout=240)
            self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-3000:])
        writer = start_odoo(original)
        load(writer, "--init=web,bus")
        for _ in range(2):
            load(writer, "--update=base")
        archive = subprocess.check_output(["docker", "exec", original, "pg_dump", "-U", self.role, "-Fc", self.db])
        self.materialize(restored, archive)
        server = start_odoo(restored)
        load(server)
        self.assertEqual(self.sql(restored, "SELECT state FROM ir_module_module WHERE name='base'"), "installed")

        # A healthy connection to the system DB does not exercise the first
        # anonymous visit or the evented worker against the restored database.
        subprocess.run(["docker", "exec", "--detach", server, *odoo, "--workers=2", "--max-cron-threads=0",
                        "--http-port=18069", "--gevent-port=18072", f"--db-filter=^{self.db}$", "--no-database-list"], check=True)
        probe = Path(__file__).with_name("fixtures") / "generation_database_web_probe.py"
        with probe.open() as script:
            receipt = subprocess.check_output(["docker", "exec", "-i", server, "python", "-"],
                                              stdin=script, text=True, timeout=80)
        self.assertEqual(json.loads(receipt), [200, True, 101, "odoo.exceptions.AccessDenied"])
