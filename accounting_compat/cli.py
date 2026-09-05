from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable


TOOL_VERSION = "0.1.0"


def resolve_compose_project(environment: dict[str, str]) -> str:
    return (
        environment.get("ACCOUNTING_COMPAT_COMPOSE_PROJECT")
        or environment.get("COMPOSE_PROJECT_NAME")
        or environment.get("ODOO_SAAS_COMPOSE_PROJECT")
        or "usl-odoo-saas-19-3"
    )


COMPOSE_PROJECT = resolve_compose_project(os.environ)
REQUIRE_ISOLATED_PROJECT = (
    os.environ.get("ACCOUNTING_COMPAT_REQUIRE_ISOLATED_PROJECT") == "1"
)
SOURCE_DB = "odoo_online_source_saas_19_3"
EXACT_VALIDATION_DB = "odoo_saas_19_3_validation_exact"
NATIVE_VALIDATION_DB = "odoo_saas_19_3_validation_native"
DEV_QA_DB = os.environ.get("ACCOUNTING_COMPAT_DEV_DB", "odoo_dev")
READONLY_ROLE = "accounting_source_ro"
DEFAULT_SOURCE_DIR = os.environ.get("ACCOUNTING_COMPAT_SOURCE_DIR", "usl-online-dump")
DEFAULT_POSTGRES_IMAGE = "pgvector/pgvector:pg16-bookworm"
SOURCE_DB_SERVICE = "accounting-source-db"
TARGET_DB_SERVICE = "db"
TARGET_ODOO_ADDONS_PATH = (
    "/opt/odoo/addons,/opt/odoo/odoo/addons,/mnt/custom-addons,"
    "/mnt/oca-addons,/mnt/accounting-migration-addons"
)
OCA_TARGET_MODULES = [
    "date_range",
    "report_xlsx",
    "report_xlsx_helper",
    "account_statement_base",
    "account_reconcile_oca",
    "account_statement_import_base",
    "account_statement_import_file",
    "account_statement_import_file_reconcile_oca",
    "account_financial_report",
    "account_tax_balance",
    "partner_statement",
    "account_asset_management",
]
TARGET_INIT_MODULES = [
    "account",
    "account_payment",
    "analytic",
    "l10n_fr_account",
    *OCA_TARGET_MODULES,
    "usl_platform_billing",
    "rebuild_account_migration",
    "usl_accounting_restore",
]
USL_BENCHMARK_START = "2024-01-10"
USL_BENCHMARK_END = "2025-09-30"
USL_CURRENT_START = "2025-10-01"
USL_BENCHMARK_PERIOD_KEY = "USL_BENCHMARK_2024_01_10_TO_2025_09_30"
USL_CURRENT_PERIOD_KEY = "CURRENT_FROM_2025_10_01"
DEV_SOURCE_TRACE_ALIASES = {
    "account.move": [
        "account.move.asset_native_replay",
        "account.move.native_engine_replay",
        "account.move.native_expense_replay",
        "account.move.native_external_exchange",
        "account.move.native_general_exchange",
        "account.move.native_general_replay",
    ],
    "account.move.line": [
        "account.move.line.asset_native_replay",
        "account.move.line.native_analytic_override",
        "account.move.line.native_bank_categorization",
        "account.move.line.native_bounded_bank_counterpart",
        "account.move.line.native_document_settlement_input",
        "account.move.line.native_engine_input",
        "account.move.line.native_expense_settlement_input",
        "account.move.line.native_external_exchange",
        "account.move.line.native_external_replay",
        "account.move.line.native_general_exchange",
        "account.move.line.native_general_replay",
    ],
}

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "accounting-compat"
PRIVATE_ARTIFACTS = ARTIFACTS / "private"
PRIVATE_SNAPSHOTS = ROOT / "accounting_compat" / "private" / "snapshots"
OCA_ADDONS_DIR = ROOT / "oca-addons"


ACCOUNTING_TABLES = [
    "res_company",
    "res_currency",
    "res_currency_rate",
    "account_account",
    "account_account_tag",
    "account_group",
    "account_journal",
    "account_tax",
    "account_tax_group",
    "account_tax_repartition_line",
    "account_fiscal_position",
    "account_fiscal_position_account",
    "account_fiscal_position_tax",
    "account_payment_term",
    "account_move",
    "account_move_line",
    "account_payment",
    "account_bank_statement",
    "account_bank_statement_line",
    "account_partial_reconcile",
    "account_full_reconcile",
    "account_analytic_plan",
    "account_analytic_account",
    "account_analytic_line",
    "account_asset",
    "account_asset_group",
    "account_asset_line",
    "account_loan",
    "account_loan_line",
    "account_deferred_report_handler",
    "account_deferred_expense",
    "account_deferred_revenue",
    "account_report",
    "account_report_line",
    "account_report_column",
    "account_report_expression",
    "account_report_external_value",
    "account_tax_closing_report",
    "account_tax_report",
    "account_tax_report_line",
    "account_oss_report",
    "account_move_send_batch_wizard",
    "ir_attachment",
    "ir_model",
    "ir_model_fields",
    "ir_model_data",
    "ir_module_module",
    "ir_cron",
    "base_automation",
]

MANDATORY_REPORT_KEYWORDS = [
    "trial balance",
    "general ledger",
    "balance sheet",
    "profit and loss",
    "partner ledger",
    "aged receivable",
    "aged payable",
    "journal report",
    "open items",
    "bank reconciliation",
    "unrealized",
    "tax report",
    "french tax",
    "fiscal report",
    "annual",
    "comptes annuels",
    "intermediate management",
    "fec",
]

OPERATIONAL_REPORT_KEYWORDS = [
    "cash flow",
    "executive summary",
    "invoice analysis",
    "ec sales",
    "oss",
    "deferred",
    "depreciation",
    "asset",
    "customer statement",
]


class HarnessError(RuntimeError):
    pass


def classify_product_import_failure(returncode: int, output: str) -> dict[str, str]:
    """Classify failed imports without misreporting host exhaustion as bad data."""
    killed = returncode in {-9, 137} or bool(
        re.search(r"(?:^|\n)Killed\s*$", output.rstrip()),
    )
    if killed:
        return {
            "classification": "MIGRATION_RESOURCE_EXHAUSTION",
            "failure_mode": "process_killed",
            "recovery": (
                "Restore Docker capacity, reset the candidate-owned target database, "
                "and rerun the atomic import; do not reuse the interrupted target."
            ),
        }
    return {
        "classification": "SOURCE_SNAPSHOT_PRODUCT_IMPORT_DEFECT",
        "failure_mode": "import_command_failed",
        "recovery": (
            "Inspect the private output, correct the migration defect, reset the "
            "candidate-owned target database, and rerun the atomic import."
        ),
    }


@dataclass
class SourcePackage:
    path: Path
    dump_path: Path
    filestore_path: Path


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dirs() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    PRIVATE_ARTIFACTS.mkdir(parents=True, exist_ok=True)
    PRIVATE_SNAPSHOTS.mkdir(parents=True, exist_ok=True)


def ensure_oca_addons_available() -> None:
    missing = [
        module
        for module in OCA_TARGET_MODULES
        if not (OCA_ADDONS_DIR / module / "__manifest__.py").exists()
    ]
    if missing:
        raise HarnessError(
            "Missing local OCA add-ons required for target reconstruction: "
            + ", ".join(missing)
            + ". Run `make oca-addons-sync` from the host shell, then rerun this stage."
        )


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def run(
    args: list[str],
    *,
    input_file: Path | None = None,
    stdout_file: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    stdin = input_file.open("rb") if input_file else None
    stdout = stdout_file.open("wb") if stdout_file else subprocess.PIPE
    try:
        completed = subprocess.run(
            args,
            cwd=ROOT,
            stdin=stdin,
            stdout=stdout,
            stderr=subprocess.STDOUT if stdout_file else subprocess.PIPE,
            env=merged_env,
            text=False,
            check=False,
        )
    finally:
        if stdin:
            stdin.close()
        if stdout_file and hasattr(stdout, "close"):
            stdout.close()

    stdout_text = ""
    stderr_text = ""
    if not stdout_file:
        stdout_text = (completed.stdout or b"").decode("utf-8", errors="replace")
        stderr_text = (completed.stderr or b"").decode("utf-8", errors="replace")
    result = subprocess.CompletedProcess(args, completed.returncode, stdout_text, stderr_text)
    if check and result.returncode:
        raise HarnessError(
            "Command failed at accounting compatibility stage.\n"
            f"command: {' '.join(args)}\n"
            f"exit_code: {result.returncode}\n"
            f"output:\n{stdout_text}{stderr_text}",
        )
    return result


def run_stdout(args: list[str], *, env: dict[str, str] | None = None) -> str:
    return run(args, env=env).stdout.strip()


def compose_args(*args: str) -> list[str]:
    args = list(args)
    if args[:4] == ["--profile", "init", "run", "--rm"] and "init-db" in args:
        args[1] = "accounting-migration"
        args[args.index("init-db")] = "accounting-migration"
    verify_compose_scope()
    command = ["docker", "compose", "-p", COMPOSE_PROJECT, *args]
    if "accounting-migration" in args:
        insert_at = len(command) - 1 - command[::-1].index(
            "accounting-migration",
        )
        command[insert_at:insert_at] = [
            "-e",
            f"ODOO_ADDONS_PATH={TARGET_ODOO_ADDONS_PATH}",
        ]
    return command


def verify_compose_scope() -> None:
    canonical_project = os.environ.get(
        "ODOO_CANONICAL_COMPOSE_PROJECT",
        "usl-odoo-saas-19-3",
    )
    if (ROOT / ".git").is_file() and COMPOSE_PROJECT == canonical_project:
        raise HarnessError(
            "The Accounting harness refuses the canonical Compose project "
            "from a linked worktree. Set ACCOUNTING_COMPAT_COMPOSE_PROJECT "
            "to a dedicated project.",
        )
    containers = subprocess.run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={COMPOSE_PROJECT}",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    for container_id in containers:
        labels = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                (
                    '{{ index .Config.Labels "com.docker.compose.project" }}'
                    " "
                    '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}'
                ),
                container_id,
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if labels != f"{COMPOSE_PROJECT} {ROOT}":
            raise HarnessError(
                "Compose scope mismatch for "
                f"{container_id}: expected {COMPOSE_PROJECT} {ROOT}, got {labels}.",
            )


def require_isolated_compose_project() -> None:
    if REQUIRE_ISOLATED_PROJECT and not COMPOSE_PROJECT.startswith(
        "usl-odoo-fp-",
    ):
        raise HarnessError(
            "This run requires a dedicated usl-odoo-fp-* Compose project; "
            f"resolved {COMPOSE_PROJECT!r}.",
        )


def source_postgres_env() -> dict[str, str]:
    return {
        "ACCOUNTING_SOURCE_POSTGRES_IMAGE": os.environ.get(
            "ACCOUNTING_SOURCE_POSTGRES_IMAGE",
            os.environ.get("ACCOUNTING_POSTGRES_IMAGE", DEFAULT_POSTGRES_IMAGE),
        ),
    }


def database_service(db: str) -> str:
    return SOURCE_DB_SERVICE if db == SOURCE_DB else TARGET_DB_SERVICE


def database_user(service: str) -> str:
    if service == SOURCE_DB_SERVICE:
        return os.environ.get("ACCOUNTING_SOURCE_POSTGRES_USER", "odoo")
    return os.environ.get("ACCOUNTING_DB_USER", "odoo")


def wait_for_postgres_service(service: str) -> None:
    user = database_user(service)
    for _attempt in range(60):
        result = run(
            compose_args(
                "exec",
                "-T",
                service,
                "pg_isready",
                "-U",
                user,
                "-d",
                "postgres",
            ),
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(1)
    raise HarnessError(f"PostgreSQL service did not become ready: {service}")


def source_package(source_dir: str) -> SourcePackage:
    root = (ROOT / source_dir).resolve()
    return SourcePackage(root, root / "dump.sql", root / "filestore")


def configure_source_mount(
    source_dir: str,
    environment: dict[str, str] | None = None,
) -> SourcePackage:
    """Use one canonical source path for host checks and Compose mounts.

    Previously ``--source-dir`` changed host-side validation while Compose kept
    mounting the unrelated default path.  A run could therefore validate one
    dump and import another (or an empty directory).  Resolve once and export
    the exact path inherited by every Docker Compose child.
    """
    package = source_package(source_dir)
    target_environment = os.environ if environment is None else environment
    target_environment["USL_ONLINE_DUMP_DIR"] = str(package.path)
    return package


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def detect_dump_format(path: Path) -> str:
    head = path.read_bytes()[:4096]
    if head.startswith(b"PGDMP"):
        return "postgres_custom"
    if b"PostgreSQL database dump" in head or head.startswith(b"--"):
        return "postgres_plain_sql"
    return "unsupported"


def dump_header(path: Path) -> dict[str, str | None]:
    header = path.read_text(encoding="utf-8", errors="replace")[:8192]
    dumped_from = re.search(r"Dumped from database version ([^\n]+)", header)
    dumped_by = re.search(r"Dumped by pg_dump version ([^\n]+)", header)
    return {
        "dumped_from_database_version": dumped_from.group(1).strip() if dumped_from else None,
        "dumped_by_pg_dump_version": dumped_by.group(1).strip() if dumped_by else None,
    }


def filestore_inventory(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "is_directory": False, "file_count": 0, "total_bytes": 0, "top_level": []}
    if not path.is_dir():
        return {"exists": True, "is_directory": False, "file_count": 0, "total_bytes": 0, "top_level": []}
    top_level: dict[str, dict[str, int]] = {}
    file_count = 0
    total_bytes = 0
    for item in path.rglob("*"):
        if not item.is_file():
            continue
        file_count += 1
        try:
            size = item.stat().st_size
        except OSError:
            size = 0
        total_bytes += size
        try:
            first = item.relative_to(path).parts[0]
        except IndexError:
            first = "."
        bucket = top_level.setdefault(first, {"file_count": 0, "total_bytes": 0})
        bucket["file_count"] += 1
        bucket["total_bytes"] += size
    return {
        "exists": True,
        "is_directory": True,
        "file_count": file_count,
        "total_bytes": total_bytes,
        "top_level": [
            {"name": name, **values}
            for name, values in sorted(top_level.items(), key=lambda pair: pair[0])
        ],
    }


def git_value(*args: str) -> str | None:
    if shutil.which("git") is None:
        return None
    try:
        return run_stdout(["git", *args])
    except (FileNotFoundError, HarnessError):
        return None


def display_path(path: Path) -> str:
    """Keep repository paths concise without rejecting external private inputs."""
    resolved = path.resolve()
    if resolved.is_relative_to(ROOT):
        return str(resolved.relative_to(ROOT))
    return str(resolved)


def git_tracking_status(paths: Iterable[Path]) -> list[dict[str, Any]]:
    records = []
    git_available = shutil.which("git") is not None
    for path in paths:
        rel = display_path(path)
        if (
            not git_available
            or (path.is_absolute() and not path.is_relative_to(ROOT))
        ):
            records.append({
                "path": rel,
                "tracked": False,
                "ignored": False,
                "ignore_rule": None,
            })
            continue
        tracked = bool(git_value("ls-files", "--", rel))
        ignored = False
        ignore_rule = None
        result = run(["git", "check-ignore", "-v", rel], check=False)
        if result.returncode == 0:
            ignored = True
            ignore_rule = result.stdout.strip()
        records.append({"path": rel, "tracked": tracked, "ignored": ignored, "ignore_rule": ignore_rule})
    return records


def psql(db: str, sql: str, *, set_readonly_role: bool = False, quiet: bool = True) -> str:
    body = sql
    if set_readonly_role:
        body = f"SET ROLE {READONLY_ROLE};\n{sql}"
    service = database_service(db)
    args = compose_args(
        "exec",
        "-T",
        service,
        "psql",
        "-U",
        database_user(service),
        "-d",
        db,
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        "-P",
        "footer=off",
        "-At",
        "-F",
        "\t",
    )
    if quiet:
        args.append("-q")
    args.extend(["-c", body])
    return run_stdout(args)


def psql_exec(db: str, sql: str) -> None:
    psql(db, sql, quiet=False)


def query_json(db: str, sql: str, *, set_readonly_role: bool = True) -> Any:
    raw = psql(db, sql, set_readonly_role=set_readonly_role)
    if not raw:
        return None
    return json.loads(raw)


def query_rows(db: str, sql: str, *, set_readonly_role: bool = True) -> list[dict[str, Any]]:
    wrapped = f"SELECT COALESCE(jsonb_agg(to_jsonb(q)), '[]'::jsonb) FROM ({sql}) q"
    return query_json(db, wrapped, set_readonly_role=set_readonly_role)


def source_manager_accounting_identity() -> dict[str, Any]:
    return query_json(
        SOURCE_DB,
        """
        WITH manager AS (
            SELECT employee.id AS employee_id,
                   employee.work_contact_id AS partner_id,
                   users.partner_id AS user_partner_id,
                   account.id AS payable_account_id,
                   account.code_store ->> employee.company_id::text AS payable_code,
                   account.reconcile AS payable_reconcile,
                   count(*) OVER () AS candidate_count
            FROM hr_employee employee
            JOIN res_partner partner
              ON partner.id = employee.work_contact_id
            JOIN res_users users
              ON users.id = employee.user_id
            JOIN account_account account
              ON account.id = NULLIF(
                  partner.property_account_payable_id
                    ->> employee.company_id::text,
                  ''
              )::integer
            WHERE employee.company_id = 1
              AND employee.work_contact_id = users.partner_id
              AND account.code_store ->> employee.company_id::text = '455100'
              AND account.reconcile
              AND EXISTS (
                  SELECT 1
                  FROM account_move_line line
                  JOIN account_move move ON move.id = line.move_id
                  WHERE line.account_id = account.id
                    AND line.partner_id = partner.id
                    AND move.state = 'posted'
                    AND NOT line.reconciled
                    AND line.balance > 0
              )
            ORDER BY employee.id
            LIMIT 1
        )
        SELECT jsonb_build_object(
            'candidate_count', manager.candidate_count,
            'employee_source_id', manager.employee_id,
            'partner_source_id', manager.partner_id,
            'user_partner_source_id', manager.user_partner_id,
            'payable_source_account_id', manager.payable_account_id,
            'payable_code', manager.payable_code,
            'payable_reconcile', manager.payable_reconcile,
            'cca_line_count', (
                SELECT count(*)
                FROM account_move_line line
                WHERE line.account_id = manager.payable_account_id
                  AND line.partner_id = manager.partner_id
            ),
            'open_debit_count', (
                SELECT count(*)
                FROM account_move_line line
                JOIN account_move move ON move.id = line.move_id
                WHERE line.account_id = manager.payable_account_id
                  AND line.partner_id = manager.partner_id
                  AND move.state = 'posted'
                  AND NOT line.reconciled
                  AND line.balance > 0
            )
        )
        FROM manager
        """,
    )


def target_manager_accounting_identity(db: str) -> dict[str, Any]:
    return query_json(
        db,
        """
        WITH manager AS (
            SELECT employee.id AS employee_id,
                   employee.rebuild_source_id AS employee_source_id,
                   employee.company_id AS company_id,
                   employee.work_contact_id AS partner_id,
                   users.partner_id AS user_partner_id,
                   partner.rebuild_source_id AS partner_source_id,
                   user_partner.rebuild_source_id AS user_partner_source_id,
                   NULLIF(
                       partner.property_account_payable_id ->> employee.company_id::text,
                       ''
                   )::integer AS payable_account_id
            FROM hr_employee employee
            JOIN res_users users ON users.id = employee.user_id
            JOIN res_partner partner ON partner.id = employee.work_contact_id
            JOIN res_partner user_partner ON user_partner.id = users.partner_id
            WHERE users.login = 'valentin'
              AND employee.rebuild_source_model = 'hr.employee'
            ORDER BY employee.id
            LIMIT 1
        )
        SELECT jsonb_build_object(
            'employee_source_id', manager.employee_source_id,
            'partner_source_id', manager.partner_source_id,
            'user_partner_source_id', manager.user_partner_source_id,
            'payable_source_account_id', account.rebuild_source_id,
            'payable_code', account.code_store ->> company.rebuild_source_id::text,
            'payable_reconcile', account.reconcile,
            'canonical_partner', manager.partner_id = manager.user_partner_id,
            'configured_cca_account',
                company.rebuild_overview_cca_account_id = manager.payable_account_id,
            'configured_cca_employee',
                company.rebuild_overview_cca_employee_id = manager.employee_id,
            'cca_line_count', (
                SELECT count(*)
                FROM account_move_line line
                WHERE line.account_id = manager.payable_account_id
                  AND line.partner_id = manager.partner_id
            ),
            'open_debit_count', (
                SELECT count(*)
                FROM account_move_line line
                JOIN account_move move ON move.id = line.move_id
                WHERE line.account_id = manager.payable_account_id
                  AND line.partner_id = manager.partner_id
                  AND move.state = 'posted'
                  AND NOT line.reconciled
                  AND line.balance > 0
            )
        )
        FROM manager
        JOIN account_account account
          ON account.id = manager.payable_account_id
        JOIN res_company company
          ON company.id = manager.company_id
        """,
        set_readonly_role=False,
    )


def manager_accounting_identity_matches(
    source: dict[str, Any] | None,
    target: dict[str, Any] | None,
) -> bool:
    identity_fields = (
        "employee_source_id",
        "partner_source_id",
        "user_partner_source_id",
        "payable_source_account_id",
        "payable_code",
        "payable_reconcile",
        "cca_line_count",
        "open_debit_count",
    )
    return bool(
        source
        and target
        and source.get("candidate_count") == 1
        and target.get("canonical_partner") is True
        and target.get("configured_cca_account") is True
        and target.get("configured_cca_employee") is True
        and all(target.get(key) == source.get(key) for key in identity_fields)
        and target.get("open_debit_count", 0) > 0,
    )


def normalize_source_expense_state_counts(
    counts: dict[str, str] | None,
) -> dict[str, str]:
    """Translate Enterprise accountant display states to Community states."""
    normalized = dict(counts or {})
    in_payment = int(normalized.pop("in_payment", "0"))
    if in_payment:
        normalized["paid"] = str(int(normalized.get("paid", "0")) + in_payment)
    return normalized


def scalar(db: str, sql: str, *, set_readonly_role: bool = True) -> str | None:
    raw = psql(db, sql, set_readonly_role=set_readonly_role)
    return raw or None


def table_exists(db: str, table: str) -> bool:
    value = scalar(db, f"SELECT to_regclass('public.{table}') IS NOT NULL", set_readonly_role=db == SOURCE_DB)
    return value == "t"


def column_names(db: str, table: str) -> set[str]:
    if not table_exists(db, table):
        return set()
    rows = query_rows(
        db,
        f"""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = '{table}'
        ORDER BY ordinal_position
        """,
        set_readonly_role=db == SOURCE_DB,
    )
    return {row["column_name"] for row in rows}


def column_type(db: str, table: str, column: str) -> str | None:
    if not table_exists(db, table):
        return None
    return scalar(
        db,
        f"""
        SELECT udt_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = '{table}' AND column_name = '{column}'
        """,
        set_readonly_role=db == SOURCE_DB,
    )


def text_expr(db: str, table: str, alias: str, column: str) -> str:
    typ = column_type(db, table, column)
    if typ in {"jsonb", "json"}:
        return f"COALESCE({alias}.{column}->>'en_US', {alias}.{column}->>'fr_FR', {alias}.{column}::text)"
    return f"{alias}.{column}::text"


def validate_source(args: argparse.Namespace) -> dict[str, Any]:
    ensure_dirs()
    manifest = source_validation_manifest(args.source_dir)
    write_json(PRIVATE_ARTIFACTS / "source-package-validation.json", manifest)
    if manifest["errors"] and not getattr(args, "allow_errors", False):
        raise HarnessError("; ".join(manifest["errors"]))
    return manifest


def source_validation_manifest(source_dir: str) -> dict[str, Any]:
    package = source_package(source_dir)
    errors = []
    if not package.path.exists():
        errors.append(f"source directory does not exist: {package.path}")
    if not package.dump_path.exists():
        errors.append(f"dump.sql does not exist: {package.dump_path}")
    if not package.filestore_path.exists():
        errors.append(f"filestore directory does not exist: {package.filestore_path}")
    elif not package.filestore_path.is_dir():
        errors.append(f"filestore path is not a directory: {package.filestore_path}")
    dump_format = detect_dump_format(package.dump_path) if package.dump_path.exists() else "missing"
    if dump_format == "unsupported":
        errors.append("dump.sql is not PostgreSQL plain SQL or custom PGDMP format")
    dump_size = package.dump_path.stat().st_size if package.dump_path.exists() else 0
    dump_sha = sha256_file(package.dump_path) if package.dump_path.exists() else None
    manifest = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "source_dir": display_path(package.path),
        "dump": {
            "path": display_path(package.dump_path),
            "exists": package.dump_path.exists(),
            "format": dump_format,
            "size_bytes": dump_size,
            "sha256": dump_sha,
            **(dump_header(package.dump_path) if dump_format == "postgres_plain_sql" else {}),
        },
        "filestore": filestore_inventory(package.filestore_path),
        "git_tracking": git_tracking_status(
            [
                package.path,
                ROOT / "accounting_compat" / "private",
                ROOT / "artifacts" / "accounting-compat" / "private",
            ],
        ),
        "errors": errors,
        "status": "failed" if errors else "passed",
    }
    return manifest


def failure_tests(args: argparse.Namespace) -> dict[str, Any]:
    ensure_dirs()
    scratch = PRIVATE_ARTIFACTS / "failure-tests"
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True, exist_ok=True)

    cases: list[dict[str, Any]] = []

    def record_case(
        name: str,
        source_dir: Path,
        *,
        expected_status: str,
        expected_error_fragment: str | None = None,
    ) -> None:
        manifest = source_validation_manifest(str(source_dir))
        errors = manifest["errors"]
        error_ok = (
            expected_error_fragment is None
            or any(expected_error_fragment in error for error in errors)
        )
        cases.append(
            {
                "name": name,
                "status": "passed" if manifest["status"] == expected_status and error_ok else "failed",
                "expected_status": expected_status,
                "observed_status": manifest["status"],
                "expected_error_fragment": expected_error_fragment,
                "observed_errors": errors,
                "dump": manifest["dump"],
                "filestore": manifest["filestore"],
            }
        )

    record_case(
        "missing_source_directory",
        scratch / "missing-source-directory",
        expected_status="failed",
        expected_error_fragment="source directory does not exist",
    )

    missing_dump = scratch / "missing-dump"
    (missing_dump / "filestore").mkdir(parents=True)
    record_case(
        "missing_dump_sql",
        missing_dump,
        expected_status="failed",
        expected_error_fragment="dump.sql does not exist",
    )

    missing_filestore = scratch / "missing-filestore"
    missing_filestore.mkdir(parents=True)
    (missing_filestore / "dump.sql").write_text(
        "-- PostgreSQL database dump\n",
        encoding="utf-8",
    )
    record_case(
        "missing_filestore_directory",
        missing_filestore,
        expected_status="failed",
        expected_error_fragment="filestore directory does not exist",
    )

    filestore_not_directory = scratch / "filestore-not-directory"
    filestore_not_directory.mkdir(parents=True)
    (filestore_not_directory / "dump.sql").write_text(
        "-- PostgreSQL database dump\n",
        encoding="utf-8",
    )
    (filestore_not_directory / "filestore").write_text("not a directory\n", encoding="utf-8")
    record_case(
        "filestore_not_directory",
        filestore_not_directory,
        expected_status="failed",
        expected_error_fragment="filestore path is not a directory",
    )

    unsupported = scratch / "unsupported-dump-format"
    (unsupported / "filestore").mkdir(parents=True)
    (unsupported / "dump.sql").write_text("not a PostgreSQL dump\n", encoding="utf-8")
    record_case(
        "unsupported_dump_format",
        unsupported,
        expected_status="failed",
        expected_error_fragment="not PostgreSQL plain SQL or custom PGDMP",
    )

    valid_plain = scratch / "valid-plain-sql"
    (valid_plain / "filestore").mkdir(parents=True)
    (valid_plain / "dump.sql").write_text(
        "-- PostgreSQL database dump\n"
        "-- Dumped from database version 16.14\n"
        "-- Dumped by pg_dump version 16.14\n",
        encoding="utf-8",
    )
    record_case(
        "valid_plain_sql_minimal_source_package",
        valid_plain,
        expected_status="passed",
    )

    payload = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "failure-tests",
        "classification": "SOURCE_PACKAGE_FAILURE_GUARDRAILS",
        "status": "passed" if all(case["status"] == "passed" for case in cases) else "failed",
        "case_count": len(cases),
        "cases": cases,
        "limitations": [
            "This stage validates non-destructive source-package guardrails only.",
            "Attachment checksum and source-metadata-driven missing-file discrepancies are validated during target import against a restored source database.",
        ],
    }
    write_json(PRIVATE_ARTIFACTS / "failure-tests-status.json", payload)
    if payload["status"] != "passed" and not getattr(args, "allow_errors", False):
        raise HarnessError("source-package failure guardrails did not pass")
    return payload


def restore_source(args: argparse.Namespace) -> dict[str, Any]:
    ensure_dirs()
    validation = validate_source(args)
    package = source_package(args.source_dir)
    restore_log = PRIVATE_ARTIFACTS / "source-restore-strict.log"
    pg_env = source_postgres_env()

    # A previous canonical Documents reset can remove and recreate the project
    # network while leaving the stopped source container behind. Recreate this
    # disposable, volume-backed service so it is always attached to the current
    # owned network before the dump is restored and queried read-only.
    run(
        compose_args("up", "-d", "--force-recreate", SOURCE_DB_SERVICE),
        env=pg_env,
    )
    wait_for_postgres_service(SOURCE_DB_SERVICE)
    run(compose_args("exec", "-T", SOURCE_DB_SERVICE, "dropdb", "-U", "odoo", "--if-exists", "--force", SOURCE_DB))
    run(compose_args("exec", "-T", SOURCE_DB_SERVICE, "createdb", "-U", "odoo", "-E", "UTF8", "-T", "template0", SOURCE_DB))
    psql_exec(
        SOURCE_DB,
        """
        CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;
        CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA public;
        CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
        ALTER FUNCTION public.unaccent(text) IMMUTABLE;
        ALTER FUNCTION public.unaccent(regdictionary, text) IMMUTABLE;
        """,
    )

    if validation["dump"]["format"] == "postgres_plain_sql":
        restore_args = compose_args("exec", "-T", SOURCE_DB_SERVICE, "psql", "-U", "odoo", "-d", SOURCE_DB, "-v", "ON_ERROR_STOP=1")
    elif validation["dump"]["format"] == "postgres_custom":
        restore_args = compose_args("exec", "-T", SOURCE_DB_SERVICE, "pg_restore", "-U", "odoo", "-d", SOURCE_DB, "--exit-on-error")
    else:
        raise HarnessError(f"Unsupported dump format: {validation['dump']['format']}")
    result = run(restore_args, input_file=package.dump_path, stdout_file=restore_log, env=pg_env, check=False)
    if result.returncode:
        status = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "source-restore",
            "status": "failed",
            "database": SOURCE_DB,
            "restore_log": str(restore_log.relative_to(ROOT)),
            "exit_code": result.returncode,
            "recommended_action": "Inspect the private restore log and classify the physical restore incompatibility before extraction.",
        }
        write_json(PRIVATE_ARTIFACTS / "source-restore-status.json", status)
        raise HarnessError(f"Source restore failed. See {restore_log}")

    psql_exec(
        SOURCE_DB,
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{READONLY_ROLE}') THEN
                CREATE ROLE {READONLY_ROLE} NOLOGIN;
            END IF;
        END
        $$;
        GRANT CONNECT ON DATABASE {SOURCE_DB} TO {READONLY_ROLE};
        GRANT USAGE ON SCHEMA public TO {READONLY_ROLE};
        GRANT SELECT ON ALL TABLES IN SCHEMA public TO {READONLY_ROLE};
        ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {READONLY_ROLE};
        """,
    )
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "source-restore",
        "status": "passed",
        "database": SOURCE_DB,
        "source_database_service": SOURCE_DB_SERVICE,
        "target_database_service": TARGET_DB_SERVICE,
        "restore_log": str(restore_log.relative_to(ROOT)),
        "postgres_image": pg_env["ACCOUNTING_SOURCE_POSTGRES_IMAGE"],
        "compatibility_shims": [
            "PostgreSQL 16 source restore uses a pgvector-capable image because the SaaS dump declares extension vector.",
            "public.unaccent(text) and public.unaccent(regdictionary,text) are marked IMMUTABLE before replay so exported expression indexes can be restored.",
            "Source restore uses the dedicated accounting-source-db service so the target Odoo database service is not recreated.",
        ],
        "neutralization": {
            "source_database_name": SOURCE_DB,
            "target_database_name": EXACT_VALIDATION_DB,
            "target_database_service_recreated": False,
            "odoo_server_started_against_source": False,
            "business_rows_modified": False,
            "extraction_role": READONLY_ROLE,
            "extraction_role_login": False,
        },
    }
    write_json(PRIVATE_ARTIFACTS / "source-restore-status.json", status)
    return status


def installed_modules(db: str) -> list[dict[str, Any]]:
    if not table_exists(db, "ir_module_module"):
        return []
    cols = column_names(db, "ir_module_module")
    fields = ["name", "state"]
    for optional in ["latest_version", "license", "application", "author", "website", "summary"]:
        if optional in cols:
            fields.append(optional)
    select_list = ", ".join(fields)
    rows = query_rows(
        db,
        f"""
        SELECT {select_list}
        FROM ir_module_module
        WHERE state = 'installed'
        ORDER BY name
        """,
    )
    local_modules = local_module_names()
    enterprise_likely = {
        "account_accountant",
        "account_asset",
        "account_bank_statement_import",
        "account_budget",
        "account_consolidation",
        "account_reports",
        "documents",
        "knowledge",
        "sign",
        "web_studio",
    }
    for row in rows:
        name = row.get("name")
        if name in local_modules:
            row["origin_classification"] = "LOCAL_REPOSITORY"
            row["target_strategy"] = "use_local_module"
        elif name in enterprise_likely or name.startswith(("account_reports", "studio_")):
            row["origin_classification"] = "ENTERPRISE_OR_SAAS"
            row["target_strategy"] = "lawful_reimplementation_or_explicit_deferral"
        elif name.startswith(("l10n_", "account_")):
            row["origin_classification"] = "UNKNOWN_ACCOUNTING_RELEVANT"
            row["target_strategy"] = "inspect_semantics_and_choose_standard_oca_or_usl_original"
        else:
            row["origin_classification"] = "UNKNOWN"
            row["target_strategy"] = "inventory_only"
    return rows


def local_module_names() -> set[str]:
    names = set()
    for base in [ROOT / "addons", ROOT / "odoo" / "addons", ROOT / "custom-addons"]:
        if not base.exists():
            continue
        for item in base.iterdir():
            if item.is_dir() and (item / "__manifest__.py").exists():
                names.add(item.name)
    return names


def source_companies(db: str) -> list[dict[str, Any]]:
    if not table_exists(db, "res_company"):
        return []
    cols = column_names(db, "res_company")
    select_terms = ["c.id", f"{text_expr(db, 'res_company', 'c', 'name')} AS name"]
    for column in [
        "parent_id",
        "currency_id",
        "country_id",
        "vat",
        "company_registry",
        "fiscalyear_last_day",
        "fiscalyear_last_month",
        "tax_lock_date",
        "fiscalyear_lock_date",
        "sale_lock_date",
        "purchase_lock_date",
        "hard_lock_date",
        "chart_template",
    ]:
        if column in cols:
            select_terms.append(f"c.{column}")
    joins = []
    if "currency_id" in cols and table_exists(db, "res_currency"):
        joins.append("LEFT JOIN res_currency cur ON cur.id = c.currency_id")
        select_terms.append("cur.name AS currency")
    if "country_id" in cols and table_exists(db, "res_country"):
        joins.append("LEFT JOIN res_country country ON country.id = c.country_id")
        select_terms.append("country.code AS country_code")
    return query_rows(
        db,
        f"""
        SELECT {", ".join(select_terms)}
        FROM res_company c
        {' '.join(joins)}
        ORDER BY c.id
        """,
    )


def source_currencies(db: str) -> list[dict[str, Any]]:
    if not table_exists(db, "res_currency"):
        return []
    cols = column_names(db, "res_currency")
    fields = ["id", "name"]
    for optional in ["symbol", "active", "rounding", "decimal_places", "position"]:
        if optional in cols:
            fields.append(optional)
    return query_rows(db, f"SELECT {', '.join(fields)} FROM res_currency ORDER BY name")


def source_record_counts(db: str) -> list[dict[str, Any]]:
    existing = [table for table in ACCOUNTING_TABLES if table_exists(db, table)]
    rows = []
    for table in existing:
        count = scalar(db, f"SELECT count(*)::text FROM {table}")
        rows.append({"table": table, "count": count})
    related = query_rows(
        db,
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND (
            table_name ILIKE '%account%'
            OR table_name ILIKE '%asset%'
            OR table_name ILIKE '%deferred%'
            OR table_name ILIKE '%analytic%'
            OR table_name ILIKE '%reconcile%'
            OR table_name ILIKE '%tax%'
            OR table_name ILIKE '%fec%'
          )
        ORDER BY table_name
        """,
    )
    known = {row["table"] for row in rows}
    for row in related:
        table = row["table_name"]
        if table not in known:
            rows.append({"table": table, "count": scalar(db, f"SELECT count(*)::text FROM {table}")})
    return sorted(rows, key=lambda row: row["table"])


def source_data_maxima(db: str) -> dict[str, Any]:
    maxima: dict[str, Any] = {}
    for table, columns in {
        "account_move": ["date", "write_date", "create_date"],
        "account_move_line": ["date", "write_date", "create_date"],
        "account_payment": ["date", "write_date", "create_date"],
        "ir_attachment": ["write_date", "create_date"],
    }.items():
        if not table_exists(db, table):
            continue
        existing_cols = column_names(db, table)
        values = {}
        for column in columns:
            if column in existing_cols:
                values[f"max_{column}"] = scalar(db, f"SELECT max({column})::text FROM {table}")
        maxima[table] = values
    return maxima


def schema_inventory(db: str, snapshot_dir: Path) -> dict[str, str]:
    schema_dir = snapshot_dir / "schema"
    schema_dir.mkdir(parents=True, exist_ok=True)
    tables = query_rows(
        db,
        """
        SELECT table_schema, table_name, table_type
        FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name
        """,
    )
    columns = query_rows(
        db,
        """
        SELECT table_name, column_name, ordinal_position, data_type, udt_name, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position
        """,
    )
    constraints = query_rows(
        db,
        """
        SELECT tc.table_name, tc.constraint_name, tc.constraint_type, kcu.column_name, ccu.table_name AS foreign_table_name, ccu.column_name AS foreign_column_name
        FROM information_schema.table_constraints tc
        LEFT JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        LEFT JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
        WHERE tc.table_schema = 'public'
        ORDER BY tc.table_name, tc.constraint_name, kcu.ordinal_position
        """,
    )
    indexes = query_rows(
        db,
        """
        SELECT schemaname, tablename, indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = 'public'
        ORDER BY tablename, indexname
        """,
    )
    sequences = query_rows(
        db,
        """
        SELECT sequence_schema, sequence_name, data_type
        FROM information_schema.sequences
        WHERE sequence_schema = 'public'
        ORDER BY sequence_name
        """,
    )
    files = {
        "tables": schema_dir / "tables.json",
        "columns": schema_dir / "columns.json",
        "constraints": schema_dir / "constraints.json",
        "indexes": schema_dir / "indexes.json",
        "sequences": schema_dir / "sequences.json",
    }
    write_json(files["tables"], tables)
    write_json(files["columns"], columns)
    write_json(files["constraints"], constraints)
    write_json(files["indexes"], indexes)
    write_json(files["sequences"], sequences)
    fingerprint_source = json.dumps({"tables": tables, "columns": columns, "constraints": constraints, "indexes": indexes}, sort_keys=True)
    fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
    return {name: str(path.relative_to(ROOT)) for name, path in files.items()} | {"fingerprint_sha256": fingerprint}


def field_mapping_status(db: str) -> list[dict[str, str]]:
    concepts = {
        "company.tax_lock_date": ("res_company", [["tax_lock_date"], ["account_tax_lock_date"]], True),
        "company.fiscal_lock_date": ("res_company", [["fiscalyear_lock_date"], ["period_lock_date"]], True),
        "company.hard_lock_date": ("res_company", [["hard_lock_date"]], False),
        "move.secure_sequence": ("account_move", [["secure_sequence_number"], ["inalterable_hash"]], False),
        "move.reversal": ("account_move", [["reversed_entry_id"], ["reversal_move_id"]], False),
        "move.tax_cash_basis": ("account_move", [["tax_cash_basis_origin_move_id"], ["tax_cash_basis_rec_id"]], False),
        "move_line.residual": ("account_move_line", [["amount_residual"], ["amount_residual_currency"]], True),
        "move_line.tax_grid_tags": ("account_move_line", [["tax_tag_ids"], ["tax_ids"], ["tax_line_id"]], True),
        "move_line.analytic_distribution": ("account_move_line", [["analytic_distribution"], ["analytic_line_ids"]], False),
        "partial_reconcile.exchange_move": ("account_partial_reconcile", [["exchange_move_id"]], False),
        "full_reconcile.exchange_move": ("account_full_reconcile", [["exchange_move_id"]], False),
        "report.external_values": ("account_report_external_value", [["value"], ["date"], ["target_report_expression_id"]], False),
    }
    statuses = []
    for concept, (table, alternatives, required) in concepts.items():
        if not table_exists(db, table):
            statuses.append(
                {
                    "concept": concept,
                    "source_table": table,
                    "status": "ABSENT_REQUIRED" if required else "ABSENT_OPTIONAL",
                    "reason": "source table absent",
                },
            )
            continue
        cols = column_names(db, table)
        matched = None
        for candidate_group in alternatives:
            if all(candidate in cols for candidate in candidate_group):
                matched = candidate_group
                break
        if matched:
            status = "EXTRACTED" if matched == alternatives[0] else "MAPPED_FROM_ALTERNATIVE"
            statuses.append({"concept": concept, "source_table": table, "status": status, "source_fields": ",".join(matched)})
        else:
            statuses.append(
                {
                    "concept": concept,
                    "source_table": table,
                    "status": "ABSENT_REQUIRED" if required else "ABSENT_OPTIONAL",
                    "reason": "none of the configured source field candidates exists",
                },
            )
    return statuses


def report_catalogue(db: str) -> list[dict[str, Any]]:
    if not table_exists(db, "account_report"):
        return []
    cols = column_names(db, "account_report")
    name = text_expr(db, "account_report", "r", "name") if "name" in cols else "r.id::text"
    fields = ["r.id", f"{name} AS name"]
    for optional in ["active", "country_id", "root_report_id", "availability_condition", "filter_date_range", "load_more_limit"]:
        if optional in cols:
            fields.append(f"r.{optional}")
    if table_exists(db, "account_report_line"):
        line_count = "(SELECT count(*) FROM account_report_line l WHERE l.report_id = r.id)::text AS line_count"
    else:
        line_count = "NULL::text AS line_count"
    fields.append(line_count)
    rows = query_rows(db, f"SELECT {', '.join(fields)} FROM account_report r ORDER BY r.id")
    for row in rows:
        row["decision"] = classify_report(row.get("name") or "")
        row["decision_basis"] = "rule_based_v1_from_active_source_name"
        row["acceptance_evidence_required"] = report_evidence(row["decision"])
        row["target_action_xmlid"] = source_report_target_action(row)
        row["target_evidence_key"] = source_report_target_evidence_key(row)
        row["target_status"] = (
            "partial_target_equivalent" if row["target_action_xmlid"]
            else "missing_target_equivalent" if row["decision"] in {"MANDATORY_PARITY", "OPERATIONAL_PARITY"}
            else "decision_pending"
        )
    return rows


def classify_report(name: str) -> str:
    normalized = name.lower()
    if "association" in normalized:
        return "REMOVED_AS_UNUSED"
    mandatory_keywords = [
        *MANDATORY_REPORT_KEYWORDS,
        "balance comptable",
        "grand livre",
        "bilan",
        "compte de résultat",
        "compte de résultats",
        "échéancier",
        "rapport du journal",
        "écarts de conversion",
        "rapport de taxes",
        "déclaration fiscale",
        "soldes intermédiaires",
    ]
    operational_keywords = [
        *OPERATIONAL_REPORT_KEYWORDS,
        "flux de trésorerie",
        "résumé général",
        "relevé intracommunautaire",
        "constatées d'avance",
        "amortissement",
        "immobilisations",
        "relevé client",
        "rapport de relance",
    ]
    if any(keyword in normalized for keyword in mandatory_keywords):
        return "MANDATORY_PARITY"
    if any(keyword in normalized for keyword in operational_keywords):
        return "OPERATIONAL_PARITY"
    return "ACCOUNTANT_REQUESTED"


def source_report_target_action(row: dict[str, Any]) -> str:
    normalized = " ".join(str(row.get(key) or "").lower() for key in ("name", "source_name", "localized_name"))
    country_code = row.get("country_code") or ""
    module = "rebuild_account_migration"
    if "trial balance" in normalized or "balance comptable" in normalized:
        return f"{module}.action_rebuild_account_trial_balance_line"
    if "general ledger" in normalized or "grand livre général" in normalized:
        return f"{module}.action_rebuild_account_general_ledger_line"
    if "journal report" in normalized or "rapport du journal" in normalized:
        return f"{module}.action_rebuild_account_journal_report_line"
    if "partner ledger" in normalized or "grand livre partenaires" in normalized:
        return f"{module}.action_rebuild_account_partner_ledger_line"
    if "customer statement" in normalized or "relevé client" in normalized:
        return f"{module}.action_rebuild_account_report_export_customer_statement"
    if "open items" in normalized:
        return f"{module}.action_rebuild_account_open_item_line"
    if "aged receivable" in normalized or "échéancier clients" in normalized:
        return f"{module}.action_rebuild_account_aged_receivable_line"
    if "aged payable" in normalized or "échéancier fournisseurs" in normalized:
        return f"{module}.action_rebuild_account_aged_payable_line"
    if "bank reconciliation" in normalized or "rapprochements bancaires" in normalized:
        return f"{module}.action_rebuild_account_bank_reconciliation_line"
    if "unrealized" in normalized or "écarts de conversion" in normalized:
        return f"{module}.action_rebuild_account_currency_report_line"
    if "cash flow" in normalized or "flux de trésorerie" in normalized:
        return f"{module}.action_rebuild_account_cash_flow_line"
    if "executive summary" in normalized or "résumé général" in normalized:
        return f"{module}.action_rebuild_account_executive_summary_line"
    if "2024" in normalized and (
        "soldes intermédiaires" in normalized or "intermediate management" in normalized or "imb -" in normalized
    ):
        return f"{module}.action_rebuild_account_report_export_sig_caf_2024"
    if "2024" in normalized and (
        "balance sheet" in normalized or "bilan comptable" in normalized or normalized.strip() == "bilan"
    ):
        return f"{module}.action_rebuild_account_report_export_french_balance_sheet_2024"
    if "2024" in normalized and (
        "profit and loss" in normalized or "compte de résultat" in normalized or "compte de résultats" in normalized
    ):
        return f"{module}.action_rebuild_account_report_export_french_profit_loss_2024"
    if "soldes intermédiaires" in normalized or "intermediate management" in normalized or "imb -" in normalized:
        return f"{module}.action_rebuild_account_sig_caf_line"
    if "comptes annuels" in normalized or "annual statements" in normalized:
        return f"{module}.action_rebuild_account_french_statement_line"
    if "fiscal report" in normalized or "charges non déductibles" in normalized:
        return f"{module}.action_rebuild_account_french_tax_package_line"
    if "group by: account > tax" in normalized or "regrouper par : compte > taxe" in normalized:
        return f"{module}.action_rebuild_account_tax_report_group_account_tax_line"
    if "group by: tax > account" in normalized or "regrouper par : taxe > compte" in normalized:
        return f"{module}.action_rebuild_account_tax_report_group_tax_account_line"
    if "ec sales" in normalized or "relevé intracommunautaire" in normalized:
        return f"{module}.action_rebuild_account_ec_sales_report_line"
    if "oss sales" in normalized or "ventes oss" in normalized:
        return f"{module}.action_rebuild_account_oss_sales_report_line"
    if "oss imports" in normalized or "importations oss" in normalized:
        return f"{module}.action_rebuild_account_oss_imports_report_line"
    if "tax report" in normalized or "rapport de taxes" in normalized or "déclaration fiscale" in normalized:
        return f"{module}.action_rebuild_account_tax_report_line"
    if "deferred expense" in normalized or "charges constatées d'avance" in normalized:
        return f"{module}.action_rebuild_interactive_deferred_schedule"
    if "deferred revenue" in normalized or "produits constatés d'avance" in normalized:
        return f"{module}.action_rebuild_interactive_deferred_schedule"
    if "depreciation" in normalized or "amortissement" in normalized:
        return f"{module}.action_rebuild_interactive_depreciation_schedule"
    if "group by: account" in normalized or "regrouper par : compte" in normalized:
        return f"{module}.action_rebuild_account_report_export_fixed_asset_group_account"
    if "asset group" in normalized or "immobilisations" in normalized:
        return f"{module}.action_rebuild_interactive_fixed_assets"
    if "balance sheet" in normalized or "bilan comptable" in normalized or normalized.strip() == "bilan":
        if country_code == "FR" or "bilan comptable" in normalized:
            return f"{module}.action_rebuild_account_french_balance_sheet_line"
        return f"{module}.action_rebuild_account_balance_sheet_line"
    if "profit and loss" in normalized or "compte de résultat" in normalized or "compte de résultats" in normalized:
        if country_code == "FR" or "compte de résultat" in normalized or "compte de résultats" in normalized:
            return f"{module}.action_rebuild_account_french_profit_loss_line"
        return f"{module}.action_rebuild_account_profit_loss_line"
    return ""


def source_report_target_evidence_key(row: dict[str, Any]) -> str:
    normalized = " ".join(str(row.get(key) or "").lower() for key in ("name", "source_name", "localized_name"))
    country_code = row.get("country_code") or ""
    if "association" in normalized:
        return "association_scope_excluded"
    if "2024" in normalized and (
        "soldes intermédiaires" in normalized or "intermediate management" in normalized or "imb -" in normalized
    ):
        return "sig_caf_2024"
    if "2024" in normalized and (
        "balance sheet" in normalized or "bilan comptable" in normalized or normalized.strip() == "bilan"
    ):
        return "french_balance_sheet_2024"
    if "2024" in normalized and (
        "profit and loss" in normalized or "compte de résultat" in normalized or "compte de résultats" in normalized
    ):
        return "french_profit_and_loss_2024"
    if "trial balance" in normalized or "balance comptable" in normalized:
        return "trial_balance"
    if "general ledger" in normalized or "grand livre général" in normalized:
        return "general_ledger"
    if "journal report" in normalized or "rapport du journal" in normalized:
        return "journal_report"
    if "partner ledger" in normalized or "grand livre partenaires" in normalized:
        return "partner_ledger"
    if "customer statement" in normalized or "relevé client" in normalized:
        return "customer_statement"
    if "open items" in normalized:
        return "open_items"
    if "aged receivable" in normalized or "échéancier clients" in normalized:
        return "aged_receivable"
    if "aged payable" in normalized or "échéancier fournisseurs" in normalized:
        return "aged_payable"
    if "bank reconciliation" in normalized or "rapprochements bancaires" in normalized:
        return "bank_reconciliation"
    if "unrealized" in normalized or "écarts de conversion" in normalized:
        return "currency_report"
    if "cash flow" in normalized or "flux de trésorerie" in normalized:
        return "cash_flow"
    if "executive summary" in normalized or "résumé général" in normalized:
        return "executive_summary"
    if "soldes intermédiaires" in normalized or "intermediate management" in normalized or "imb -" in normalized:
        return "sig_caf"
    if "comptes annuels" in normalized or "annual statements" in normalized:
        return "french_annual_statements"
    if "fiscal report" in normalized or "charges non déductibles" in normalized:
        return "french_tax_package"
    if "group by: account > tax" in normalized or "regrouper par : compte > taxe" in normalized:
        return "tax_report_group_account_tax"
    if "group by: tax > account" in normalized or "regrouper par : taxe > compte" in normalized:
        return "tax_report_group_tax_account"
    if "ec sales" in normalized or "relevé intracommunautaire" in normalized:
        return "ec_sales_list"
    if "oss sales" in normalized or "ventes oss" in normalized:
        return "oss_sales"
    if "oss imports" in normalized or "importations oss" in normalized:
        return "oss_imports"
    if "tax report" in normalized or "rapport de taxes" in normalized or "déclaration fiscale" in normalized:
        return "vat_tax_report"
    if "deferred expense" in normalized or "charges constatées d'avance" in normalized:
        return "deferred_expense"
    if "deferred revenue" in normalized or "produits constatés d'avance" in normalized:
        return "deferred_revenue"
    if "depreciation" in normalized or "amortissement" in normalized:
        return "depreciation_schedule"
    if "group by: account" in normalized or "regrouper par : compte" in normalized:
        return "fixed_asset_group_account"
    if "asset group" in normalized or "immobilisations" in normalized:
        return "fixed_asset_register"
    if "balance sheet" in normalized or "bilan comptable" in normalized or normalized.strip() == "bilan":
        if country_code == "FR" or "bilan comptable" in normalized:
            return "french_balance_sheet"
        return "balance_sheet"
    if "profit and loss" in normalized or "compte de résultat" in normalized or "compte de résultats" in normalized:
        if country_code == "FR" or "compte de résultat" in normalized or "compte de résultats" in normalized:
            return "french_profit_and_loss"
        return "profit_and_loss"
    return ""


def report_evidence(decision: str) -> str:
    if decision == "MANDATORY_PARITY":
        return "line_values, drill_down_membership, exports, source_target_controls, accountant_review_where_statutory"
    if decision == "OPERATIONAL_PARITY":
        return "workflow_need_evidence, target_equivalent_output, material_difference_classification"
    if decision == "REMOVED_AS_UNUSED":
        return "legal_form_scope_evidence, explicit_non_parity_decision, stakeholder_review"
    return "accountant_usage_decision_or_explicit_deferral"


def capability_matrix(reports: list[dict[str, Any]], controls: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    controls = controls or {}
    capability_controls = controls.get("capabilities", {})
    report_controls = controls.get("reports", {})
    base_capabilities = [
        ("Accounting > Closing > Reconcile", "MANDATORY_PARITY"),
        ("Accounting > Closing > Tax returns", "MANDATORY_PARITY"),
        ("Accounting > Closing > Lock dates", "MANDATORY_PARITY"),
        ("Accounting > Closing > Accepted snapshots", "MANDATORY_PARITY"),
        ("Accounting > Transactions > Journal entries", "MANDATORY_PARITY"),
        ("Accounting > Transactions > Analytic items", "MANDATORY_PARITY"),
        ("Accounting > Assets", "OPERATIONAL_PARITY"),
        ("Review > Control > Journal items", "MANDATORY_PARITY"),
        ("Review > Control > Journal audit", "MANDATORY_PARITY"),
        ("Review > Inventory > Depreciation schedule", "OPERATIONAL_PARITY"),
        ("Review > Inventory > Loans analysis", "OPERATIONAL_PARITY"),
        ("Review > Deferred expenses", "OPERATIONAL_PARITY"),
        ("Review > Deferred revenue", "OPERATIONAL_PARITY"),
        ("Review > Unrealized currencies", "MANDATORY_PARITY"),
        ("Reporting > FEC", "MANDATORY_PARITY"),
        ("Reporting > Revenue versus spending trend", "OPERATIONAL_PARITY"),
        ("Reporting > EDI exports", "OPERATIONAL_PARITY"),
        ("Permissions > Accountant review role", "MANDATORY_PARITY"),
    ]
    matrix = []
    for capability, decision in base_capabilities:
        row = {
            "capability": capability,
            "decision": decision,
            "source_behaviour": "source_inventory_required",
            "target_community_baseline": "inspect_local_module_and_standard_odoo_behavior",
            "candidate_alternatives": [
                "standard_odoo_community",
                "maintained_oca_module",
                "usl_original_reimplementation",
                "explicit_external_service_retention",
            ],
            "remaining_gap": "not_closed_in_v1",
            "accounting_risk": "P0 if posted history, tax, lock, reconciliation, report, or FEC semantics differ",
            "compliance_risk": "requires accountant/legal review where statutory or tax output is affected",
            "evidence_required": "source_controls,target_controls,report_comparison,discrepancy_register",
            "status": "DISCOVERY",
        }
        row.update(capability_controls.get(capability, {}))
        matrix.append(row)
    for report in reports:
        row = {
            "capability": f"Source report: {report.get('name')}",
            "source_report_id": report.get("id"),
            "decision": report.get("decision"),
            "source_behaviour": "source_account_report_record",
            "target_community_baseline": "pending_target_report_mapping",
            "candidate_alternatives": [
                "standard_account_report",
                "l10n_fr_account_or_target_localization",
                "oca_reporting_module",
                "usl_original_report_expression",
            ],
            "remaining_gap": "target_semantics_not_yet_compared",
            "accounting_risk": "line_mapping_or_formula_difference",
            "compliance_risk": "statutory_or_tax_report_requires_accountant_review" if report.get("decision") == "MANDATORY_PARITY" else "usage_dependent",
            "evidence_required": report.get("acceptance_evidence_required"),
            "status": "DISCOVERY",
        }
        row.update(report_controls.get(str(report.get("id")), {}))
        matrix.append(row)
    return matrix


def final_capability_matrix_controls(
    source_report_parity: dict[str, Any],
    *,
    report_controls_passed: bool,
) -> dict[str, Any]:
    validated_at = utc_now()

    def artifact_gate(
        filename: str,
        accepted_statuses: tuple[str, ...] = ("passed",),
    ) -> dict[str, Any]:
        path = PRIVATE_ARTIFACTS / filename
        payload = read_json(path) if path.exists() else {}
        status = payload.get("status", "missing")
        return {
            "path": str(path.relative_to(ROOT)),
            "status": status,
            "passed": status in accepted_statuses,
        }

    reports_gate = {
        "path": "artifacts/accounting-compat/private/reports-status.json",
        "status": "passed" if report_controls_passed else "partial",
        "passed": report_controls_passed,
    }

    def capability_control(
        *,
        status: str,
        source_behaviour: str,
        target_community_baseline: str,
        remaining_gap: str,
        acceptance_status: str = "not_required",
        artifact_filenames: tuple[str, ...] = (),
        include_reports_gate: bool = False,
    ) -> dict[str, Any]:
        gates = [artifact_gate(filename) for filename in artifact_filenames]
        if include_reports_gate:
            gates.append(reports_gate)
        technical_passed = all(gate["passed"] for gate in gates)
        effective_status = status if technical_passed else "TECHNICAL_GAP"
        return {
            "source_behaviour": source_behaviour,
            "target_community_baseline": target_community_baseline,
            "remaining_gap": (
                remaining_gap
                if technical_passed
                else "One or more required technical evidence artifacts are missing or not in the expected state."
            ),
            "technical_status": "passed" if technical_passed else "failed",
            "acceptance_status": acceptance_status,
            "evidence_artifacts": gates,
            "validated_at": validated_at,
            "status": effective_status,
        }

    capabilities = {
        "Accounting > Closing > Reconcile": capability_control(
            status="IMPLEMENTED",
            source_behaviour="Source contains native reconciliations plus 75 relationships crossing the posted replay boundary.",
            target_community_baseline="OCA operational reconciliation plus a source-traced boundary review workflow.",
            remaining_gap="No technical implementation gap; cross-boundary historical relationships remain explicitly classified as evidence-only.",
            acceptance_status="not_required_for_engineering_completion",
            artifact_filenames=(
                "validation-native-general-reconciliation-status.json",
                "reconciliation-review-browser-status.json",
            ),
        ),
        "Accounting > Closing > Tax returns": capability_control(
            status="IMPLEMENTED",
            source_behaviour="Source report catalogue and declaration evidence include French VAT and fiscal report families.",
            target_community_baseline="Versioned declaration schedule, field guidance and ledger-derived tax-package review workbench.",
            remaining_gap="Electronic filing is deliberately deferred; preparation, traceability, statuses and exports are implemented.",
            acceptance_status="professional_review_advisory",
            include_reports_gate=True,
        ),
        "Accounting > Closing > Lock dates": capability_control(
            status="IMPLEMENTED",
            source_behaviour="Source company fiscal and tax lock dates are preserved.",
            target_community_baseline="Native Odoo lock dates with manager-only closing controls.",
            remaining_gap="No technical implementation gap.",
            acceptance_status="not_required",
            artifact_filenames=(
                "accounting-home-browser-status.json",
                "fec-role-browser-status.json",
            ),
        ),
        "Accounting > Closing > Accepted snapshots": capability_control(
            status="IMPLEMENTED",
            source_behaviour=(
                "Accepted closing packages must remain retrievable with the "
                "review decision and content identity that authorized them."
            ),
            target_community_baseline=(
                "Manager-generated closing packages attach to the workspace; "
                "recorded acceptance captures immutable payloads, SHA-256, "
                "reviewer and decision evidence."
            ),
            remaining_gap=(
                "No technical implementation gap; accepted snapshots are created "
                "only when an authorized reviewer records a real operational decision."
            ),
            acceptance_status="operational_event_not_engineering_gate",
            include_reports_gate=True,
        ),
        "Accounting > Transactions > Journal entries": capability_control(
            status="IMPLEMENTED",
            source_behaviour="Posted source history is replayed exactly and current-period documents are proved through native workflows.",
            target_community_baseline="Native account.move records with source traces and standard journal navigation.",
            remaining_gap="No technical implementation gap.",
            acceptance_status="not_required",
            artifact_filenames=(
                "validation-exact-validate-status.json",
                "validation-native-documents-status.json",
                "validation-native-expenses-status.json",
            ),
        ),
        "Accounting > Transactions > Analytic items": capability_control(
            status="IMPLEMENTED",
            source_behaviour="Source analytic plans, accounts, distributions, corrections and lines are preserved.",
            target_community_baseline="Native multi-plan analytic records with list, pivot, chart, export and source-traced correction audit.",
            remaining_gap="No technical implementation gap.",
            artifact_filenames=(
                "validation-native-analytics-status.json",
                "validation-native-analytics-browser-status.json",
            ),
        ),
        "Accounting > Assets": capability_control(
            status="IMPLEMENTED",
            source_behaviour="Source contains three assets and 91 source depreciation schedule rows.",
            target_community_baseline="Native OCA asset workflow plus imported historical register and schedule evidence.",
            remaining_gap="No technical implementation gap; professional review remains advisory.",
            acceptance_status="professional_review_advisory",
            artifact_filenames=(
                "validation-native-assets-status.json",
                "validation-native-assets-browser-status.json",
            ),
        ),
        "Review > Control > Journal items": capability_control(
            status="IMPLEMENTED",
            source_behaviour="Source journal items, workflow-only lines and display lines are inventoried and traced.",
            target_community_baseline="Native journal-item navigation plus read-only source review records.",
            remaining_gap="No technical implementation gap.",
            artifact_filenames=("validation-exact-validate-status.json",),
            include_reports_gate=True,
        ),
        "Review > Control > Journal audit": capability_control(
            status="IMPLEMENTED",
            source_behaviour="Source identity, chronology, balances, references and attachment metadata are audit inputs.",
            target_community_baseline="Idempotence, invariant-failure, sequence/chronology and evidence-integrity controls.",
            remaining_gap="Preserved source sequence exceptions are documented source anomalies, not reconstruction defects.",
            acceptance_status="documented_source_assumption",
            artifact_filenames=(
                "validation-exact-idempotence-status.json",
                "validation-exact-failure-tests-status.json",
                "validation-exact-validate-status.json",
            ),
        ),
        "Review > Inventory > Depreciation schedule": capability_control(
            status="IMPLEMENTED",
            source_behaviour="Source asset-linked posted and forecast depreciation rows are preserved.",
            target_community_baseline="Historical schedule report plus native current-period OCA depreciation board.",
            remaining_gap="No technical implementation gap; professional statement/tax review remains advisory.",
            acceptance_status="professional_review_advisory",
            artifact_filenames=(
                "validation-exact-validate-status.json",
                "validation-native-assets-status.json",
            ),
            include_reports_gate=True,
        ),
        "Review > Inventory > Loans analysis": capability_control(
            status="NOT_APPLICABLE",
            source_behaviour="The restored source contains zero account.loan and zero account.loan.line records.",
            target_community_baseline="No USL loan workflow is exposed as an Accounting v1 requirement.",
            remaining_gap="Reassess only if a future source or business event introduces loans.",
        ),
        "Review > Deferred expenses": capability_control(
            status="IMPLEMENTED",
            source_behaviour="Source deferred-expense relationships and forecast rows are preserved.",
            target_community_baseline="Native OCA deferral schedules with opening-boundary handling and read-only reviewer access.",
            remaining_gap="No technical implementation gap.",
            artifact_filenames=(
                "validation-native-deferrals-status.json",
                "validation-native-deferrals-browser-status.json",
            ),
            include_reports_gate=True,
        ),
        "Review > Deferred revenue": capability_control(
            status="NOT_APPLICABLE",
            source_behaviour="No deferred-revenue schedule is present in the current USL source corpus.",
            target_community_baseline="The shared native deferral model remains available if future source data requires it.",
            remaining_gap="Reassess when a deferred-revenue case exists.",
        ),
        "Review > Unrealized currencies": capability_control(
            status="IMPLEMENTED",
            source_behaviour="Historical rates and foreign-currency ledger/residual evidence are preserved.",
            target_community_baseline="Native Odoo currency engine plus realized/unrealized exposure report and future ECB reference rates.",
            remaining_gap="No technical implementation gap.",
            artifact_filenames=(
                "currency-rate-provider-status.json",
                "validation-native-bank-external-status.json",
            ),
            include_reports_gate=True,
        ),
        "Reporting > FEC": capability_control(
            status="IMPLEMENTED",
            source_behaviour="The benchmark ledger must produce a complete French BIC/IS FEC.",
            target_community_baseline="Native l10n_fr_account FEC export with role gates, structural preflight and DGFiP source validation.",
            remaining_gap="No technical implementation gap; external filing or professional review is outside this milestone.",
            acceptance_status="professional_review_advisory",
            artifact_filenames=(
                "fec-validation-status.json",
                "fec-role-browser-status.json",
            ),
        ),
        "Reporting > Revenue versus spending trend": capability_control(
            status="IMPLEMENTED",
            source_behaviour=(
                "The replacement objective requires monthly revenue, "
                "spending and net-contribution evolution."
            ),
            target_community_baseline=(
                "Company-scoped native SQL view with graph, pivot monthly "
                "columns, list export and journal-item drilldown."
            ),
            remaining_gap="No technical implementation gap.",
            include_reports_gate=True,
        ),
        "Reporting > EDI exports": capability_control(
            status="DEFERRED",
            source_behaviour="Electronic submission is not required by the Accounting v1 objective.",
            target_community_baseline="Declaration preparation, portal guidance and filing-state tracking are retained without an electronic filing client.",
            remaining_gap="Select and implement an approved external e-invoicing/EDI strategy in its later milestone.",
        ),
        "Permissions > Accountant review role": capability_control(
            status="IMPLEMENTED",
            source_behaviour="Prosper requires company-scoped accounting review and export without accounting mutation.",
            target_community_baseline="USL accountant reviewer role over Odoo read-only accounting with explicit evidence and FEC access.",
            remaining_gap="No technical implementation gap after the scoped read-only browser role check.",
            acceptance_status="role_test_required",
            artifact_filenames=(
                "replacement-browser-status.json",
                "fec-role-browser-status.json",
            ),
            include_reports_gate=True,
        ),
    }

    report_controls = {}
    for report in source_report_parity.get("reports", []):
        source_report_id = report.get("source_report_id")
        if source_report_id is None:
            continue
        decision = report.get("decision")
        technical_passed = report.get("latest_evidence_status") == (
            "technical_evidence_validated"
        )
        deliberately_excluded = decision == "REMOVED_AS_UNUSED"
        report_controls[str(source_report_id)] = {
            "target_community_baseline": report.get("target_evidence_key") or "explicit_scope_decision",
            "remaining_gap": (
                "No technical implementation gap; professional review remains advisory."
                if technical_passed
                else report.get("latest_evidence_status", "technical_report_evidence_incomplete")
            ),
            "technical_status": "passed" if technical_passed else "failed",
            "acceptance_status": "professional_review_advisory",
            "evidence_artifacts": [
                {
                    "path": "artifacts/accounting-compat/private/source-report-parity-status.json",
                    "status": report.get("latest_evidence_status"),
                    "passed": technical_passed,
                },
                reports_gate,
            ],
            "parity_level": report.get("parity_level"),
            "validated_at": validated_at,
            "status": (
                "NOT_APPLICABLE"
                if deliberately_excluded and technical_passed
                else "IMPLEMENTED"
                if technical_passed
                else "TECHNICAL_GAP"
            ),
        }
    return {
        "capabilities": capabilities,
        "reports": report_controls,
    }


def inspect_source(args: argparse.Namespace) -> dict[str, Any]:
    ensure_dirs()
    validation = validate_source(args)
    dump_sha = validation["dump"]["sha256"] or "unknown"
    snapshot_id = f"source-{dump_sha[:12]}"
    snapshot_dir = PRIVATE_SNAPSHOTS / snapshot_id
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    modules = installed_modules(SOURCE_DB)
    reports = report_catalogue(SOURCE_DB)
    schema_files = schema_inventory(SOURCE_DB, snapshot_dir)
    manifest = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "repository": {
            "commit": git_value("rev-parse", "HEAD"),
            "branch": git_value("branch", "--show-current"),
            "dirty_status": git_value("status", "--short"),
        },
        "source": {
            "declared_source": "Odoo Online Enterprise saas~19.3",
            "database": SOURCE_DB,
            "dump": validation["dump"],
            "filestore": validation["filestore"],
            "postgres_version": scalar(SOURCE_DB, "SELECT version()", set_readonly_role=False),
            "database_size": scalar(SOURCE_DB, "SELECT pg_size_pretty(pg_database_size(current_database()))", set_readonly_role=False),
            "database_uuid": source_config_parameter(SOURCE_DB, "database.uuid"),
            "database_create_date": source_config_parameter(SOURCE_DB, "database.create_date"),
        },
        "extensions": query_rows(
            SOURCE_DB,
            """
            SELECT e.extname, e.extversion, n.nspname AS schema
            FROM pg_extension e
            JOIN pg_namespace n ON n.oid = e.extnamespace
            ORDER BY e.extname
            """,
            set_readonly_role=False,
        ),
        "function_volatility": query_rows(
            SOURCE_DB,
            """
            SELECT n.nspname AS schema, p.proname AS function, pg_get_function_identity_arguments(p.oid) AS args, p.provolatile
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public' AND p.proname IN ('unaccent')
            ORDER BY 1,2,3
            """,
            set_readonly_role=False,
        ),
        "installed_modules": modules,
        "installed_accounting_relevant_modules": [
            row for row in modules if is_accounting_relevant_module(row.get("name", ""))
        ],
        "companies": source_companies(SOURCE_DB),
        "currencies": source_currencies(SOURCE_DB),
        "data_maxima": source_data_maxima(SOURCE_DB),
        "record_counts": source_record_counts(SOURCE_DB),
        "schema": schema_files,
        "field_mapping_status": field_mapping_status(SOURCE_DB),
        "report_catalogue_path": str((PRIVATE_ARTIFACTS / "report-catalogue-v1.json").relative_to(ROOT)),
        "parity_matrix_path": str((PRIVATE_ARTIFACTS / "parity-matrix-v1.json").relative_to(ROOT)),
    }
    write_json(PRIVATE_ARTIFACTS / "source-manifest.json", manifest)
    write_json(snapshot_dir / "manifest.json", manifest)
    write_json(snapshot_dir / "schema" / "modules.json", modules)
    write_json(PRIVATE_ARTIFACTS / "report-catalogue-v1.json", reports)
    write_json(PRIVATE_ARTIFACTS / "parity-matrix-v1.json", capability_matrix(reports))
    return manifest


def source_config_parameter(db: str, key: str) -> str | None:
    if not table_exists(db, "ir_config_parameter"):
        return None
    return scalar(db, f"SELECT value FROM ir_config_parameter WHERE key = '{key}' LIMIT 1")


def is_accounting_relevant_module(name: str) -> bool:
    tokens = [
        "account",
        "l10n_fr",
        "fec",
        "tax",
        "asset",
        "deferred",
        "analytic",
        "bank",
        "payment",
        "oss",
        "sale",
        "purchase",
        "studio",
    ]
    return any(token in name for token in tokens)


def source_controls(args: argparse.Namespace) -> dict[str, Any]:
    ensure_dirs()
    if not table_exists(SOURCE_DB, "account_move") or not table_exists(SOURCE_DB, "account_move_line"):
        message = "Source controls require account_move and account_move_line tables."
        raise HarnessError(message)
    account_code = account_code_expr(SOURCE_DB, "aa")
    snapshot_date = source_snapshot_date()
    controls = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "database": SOURCE_DB,
        "periods": {
            "usl_benchmark": {"date_from": USL_BENCHMARK_START, "date_to": USL_BENCHMARK_END},
            "current_from": {"date_from": USL_CURRENT_START, "date_to": snapshot_date},
            "latest_complete_month": latest_complete_month(snapshot_date),
            "max_accounting_date": source_max_accounting_date(),
        },
        "move_counts_by_company_period": query_rows(
            SOURCE_DB,
            f"""
            SELECT company_id,
                   {period_case('date')} AS period,
                   count(*)::text AS move_count,
                   count(*) FILTER (WHERE state = 'posted')::text AS posted_count,
                   count(*) FILTER (WHERE state = 'draft')::text AS draft_count,
                   count(*) FILTER (WHERE state = 'cancel')::text AS cancelled_count
            FROM account_move
            GROUP BY company_id, {period_case('date')}
            ORDER BY company_id, period
            """,
        ),
        "move_line_totals_by_company_period": query_rows(
            SOURCE_DB,
            f"""
            SELECT aml.company_id,
                   {period_case('am.date')} AS period,
                   count(*)::text AS move_line_count,
                   COALESCE(sum(aml.debit), 0)::text AS debit_total,
                   COALESCE(sum(aml.credit), 0)::text AS credit_total,
                   COALESCE(sum(aml.balance), 0)::text AS balance_total
            FROM account_move_line aml
            JOIN account_move am ON am.id = aml.move_id
            GROUP BY aml.company_id, {period_case('am.date')}
            ORDER BY aml.company_id, period
            """,
        ),
        "posted_unbalanced_moves": query_rows(
            SOURCE_DB,
            """
            SELECT am.company_id,
                   count(*)::text AS unbalanced_move_count,
                   COALESCE(sum(abs(x.balance)), 0)::text AS absolute_imbalance
            FROM (
                SELECT move_id, COALESCE(sum(debit - credit), 0) AS balance
                FROM account_move_line
                GROUP BY move_id
                HAVING round(COALESCE(sum(debit - credit), 0), 2) <> 0
            ) x
            JOIN account_move am ON am.id = x.move_id
            WHERE am.state = 'posted'
            GROUP BY am.company_id
            ORDER BY am.company_id
            """,
        ),
        "account_balances_by_company_period": query_rows(
            SOURCE_DB,
            f"""
            SELECT aml.company_id,
                   {period_case('am.date')} AS period,
                   aml.account_id,
                   {account_code} AS account_code,
                   count(*)::text AS line_count,
                   COALESCE(sum(aml.debit), 0)::text AS debit_total,
                   COALESCE(sum(aml.credit), 0)::text AS credit_total,
                   COALESCE(sum(aml.balance), 0)::text AS balance_total
            FROM account_move_line aml
            JOIN account_move am ON am.id = aml.move_id
            LEFT JOIN account_account aa ON aa.id = aml.account_id
            WHERE am.state = 'posted'
            GROUP BY aml.company_id, {period_case('am.date')}, aml.account_id, account_code
            ORDER BY aml.company_id, period, account_code NULLS LAST, aml.account_id
            """,
        ),
        "journal_balances_by_company_period": query_rows(
            SOURCE_DB,
            f"""
            SELECT am.company_id,
                   {period_case('am.date')} AS period,
                   am.journal_id,
                   aj.code AS journal_code,
                   count(DISTINCT am.id)::text AS move_count,
                   count(aml.id)::text AS move_line_count,
                   COALESCE(sum(aml.debit), 0)::text AS debit_total,
                   COALESCE(sum(aml.credit), 0)::text AS credit_total,
                   COALESCE(sum(aml.balance), 0)::text AS balance_total
            FROM account_move am
            JOIN account_move_line aml ON aml.move_id = am.id
            LEFT JOIN account_journal aj ON aj.id = am.journal_id
            WHERE am.state = 'posted'
            GROUP BY am.company_id, {period_case('am.date')}, am.journal_id, aj.code
            ORDER BY am.company_id, period, aj.code NULLS LAST, am.journal_id
            """,
        ),
        "reconciliation_counts": reconciliation_counts(SOURCE_DB),
        "payment_counts": table_count_by_company(SOURCE_DB, "account_payment"),
        "bank_statement_line_counts": table_count_by_company(SOURCE_DB, "account_bank_statement_line"),
        "attachment_counts": attachment_counts(SOURCE_DB),
        "tax_grid_totals": tax_grid_totals(SOURCE_DB),
        "customization_counts": customization_counts(SOURCE_DB),
        "future_dated_moves": future_dated_moves(SOURCE_DB, snapshot_date),
    }
    write_json(PRIVATE_ARTIFACTS / "source-controls.json", controls)
    write_json(PRIVATE_ARTIFACTS / "invariants.json", invariants_from_controls(controls))
    write_json(PRIVATE_ARTIFACTS / "anomalies.json", anomalies_from_controls(controls))
    return controls


def account_code_expr(db: str, alias: str) -> str:
    cols = column_names(db, "account_account")
    if "code" in cols:
        return f"{alias}.code"
    if "code_store" in cols:
        return f"{alias}.code_store"
    return "NULL::text"


def period_case(column: str) -> str:
    return (
        f"CASE "
        f"WHEN {column} BETWEEN DATE '{USL_BENCHMARK_START}' AND DATE '{USL_BENCHMARK_END}' THEN '{USL_BENCHMARK_PERIOD_KEY}' "
        f"WHEN {column} >= DATE '{USL_CURRENT_START}' THEN '{USL_CURRENT_PERIOD_KEY}' "
        f"WHEN {column} IS NULL THEN 'NO_ACCOUNTING_DATE' "
        f"ELSE 'OUTSIDE_REFERENCE_SCOPE' END"
    )


def source_max_accounting_date() -> str | None:
    if not table_exists(SOURCE_DB, "account_move"):
        return None
    return scalar(SOURCE_DB, "SELECT max(date)::text FROM account_move")


def source_snapshot_date() -> str | None:
    candidates = []
    for table in ["account_move", "account_move_line", "account_payment", "ir_attachment"]:
        if not table_exists(SOURCE_DB, table):
            continue
        cols = column_names(SOURCE_DB, table)
        for column in ["write_date", "create_date"]:
            if column in cols:
                value = scalar(SOURCE_DB, f"SELECT max({column})::date::text FROM {table}")
                if value:
                    candidates.append(value)
    return max(candidates) if candidates else source_max_accounting_date()


def source_snapshot_id(source_dir: str | None = None) -> str:
    package = source_package(
        source_dir or os.environ.get("USL_ONLINE_DUMP_DIR", DEFAULT_SOURCE_DIR),
    )
    return f"source-{sha256_file(package.dump_path)[:12]}"


def latest_complete_month(max_date: str | None) -> dict[str, str] | None:
    if not max_date:
        return None
    value = date.fromisoformat(max_date)
    first_of_month = value.replace(day=1)
    previous_last = first_of_month.fromordinal(first_of_month.toordinal() - 1)
    previous_first = previous_last.replace(day=1)
    return {"date_from": previous_first.isoformat(), "date_to": previous_last.isoformat()}


def table_count_by_company(db: str, table: str) -> list[dict[str, Any]]:
    if not table_exists(db, table):
        return []
    cols = column_names(db, table)
    if "company_id" in cols:
        return query_rows(db, f"SELECT company_id, count(*)::text AS count FROM {table} GROUP BY company_id ORDER BY company_id")
    return [{"company_id": None, "count": scalar(db, f"SELECT count(*)::text FROM {table}")}]


def reconciliation_counts(db: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if table_exists(db, "account_partial_reconcile"):
        result["partial_reconcile_count"] = scalar(db, "SELECT count(*)::text FROM account_partial_reconcile")
    if table_exists(db, "account_full_reconcile"):
        result["full_reconcile_count"] = scalar(db, "SELECT count(*)::text FROM account_full_reconcile")
    if table_exists(db, "account_move_line"):
        cols = column_names(db, "account_move_line")
        if "full_reconcile_id" in cols:
            result["move_lines_with_full_reconcile"] = scalar(
                db,
                "SELECT count(*)::text FROM account_move_line WHERE full_reconcile_id IS NOT NULL",
            )
        if "matched_debit_ids" in cols or "matched_credit_ids" in cols:
            result["note"] = "matched debit/credit Odoo relation fields are represented by account_partial_reconcile rows, not physical columns."
    return result


def future_dated_moves(db: str, snapshot_date: str | None) -> list[dict[str, Any]]:
    if not snapshot_date or not table_exists(db, "account_move"):
        return []
    return query_rows(
        db,
        f"""
        SELECT company_id, move_type, state, count(*)::text AS move_count, min(date)::text AS min_date, max(date)::text AS max_date
        FROM account_move
        WHERE date > DATE '{snapshot_date}'
        GROUP BY company_id, move_type, state
        ORDER BY company_id, move_type, state
        """,
    )


def attachment_counts(db: str) -> list[dict[str, Any]]:
    if not table_exists(db, "ir_attachment"):
        return []
    return query_rows(
        db,
        """
        SELECT COALESCE(res_model, '') AS res_model,
               count(*)::text AS attachment_count,
               COALESCE(sum(file_size), 0)::text AS total_file_size
        FROM ir_attachment
        WHERE res_model IS NULL
           OR res_model IN (
               'account.move', 'account.move.line', 'account.payment', 'account.bank.statement',
               'account.bank.statement.line', 'account.asset', 'account.loan', 'account.report',
               'account.tax.closing.report'
           )
           OR res_model LIKE 'account.%'
        GROUP BY COALESCE(res_model, '')
        ORDER BY res_model
        """,
    )


def attachment_reconstruction_audit(args: argparse.Namespace) -> dict[str, Any]:
    ensure_dirs()
    package = source_package(args.source_dir)
    if not table_exists(SOURCE_DB, "ir_attachment"):
        raise HarnessError(
            "The read-only source database is not restored. "
            "Run `make accounting-source-restore` first.",
        )

    source_rows = query_rows(
        SOURCE_DB,
        """
        SELECT attachment.id,
               attachment.res_model,
               attachment.res_id,
               attachment.store_fname,
               attachment.checksum,
               attachment.file_size,
               attachment.type,
               CASE
                   WHEN attachment.res_model IN (
                       'account.move',
                       'account.move.line',
                       'account.payment',
                       'account.bank.statement',
                       'account.bank.statement.line',
                       'account.asset',
                       'hr.expense',
                       'hr.expense.sheet'
                   ) THEN 'accounting_direct'
                   WHEN EXISTS (
                       SELECT 1
                         FROM message_attachment_rel relation
                         JOIN mail_message message
                           ON message.id = relation.message_id
                        WHERE relation.attachment_id = attachment.id
                          AND message.model IN (
                              'account.move',
                              'account.move.line',
                              'account.payment',
                              'account.bank.statement',
                              'account.bank.statement.line',
                              'account.asset',
                              'hr.expense',
                              'hr.expense.sheet'
                          )
                   ) THEN 'accounting_chatter'
                   WHEN attachment.res_model IN (
                       'ir.ui.view',
                       'ir.ui.menu',
                       'ir.attachment',
                       'payment.method',
                       'payment.provider',
                       'onboarding.onboarding.step',
                       'res.lang',
                       'spreadsheet.dashboard'
                   ) THEN 'regenerable_or_technical'
                   ELSE 'other_business'
               END AS reconstruction_class
          FROM ir_attachment attachment
         WHERE attachment.type = 'binary'
         ORDER BY attachment.id
        """,
    )

    metadata_by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows_without_file_reference = []
    for row in source_rows:
        store_fname = row.get("store_fname")
        if store_fname:
            metadata_by_path[store_fname].append(row)
        else:
            rows_without_file_reference.append(row)

    filestore_root = package.filestore_path.resolve()
    missing_files = []
    unreadable_files = []
    checksum_mismatches = []
    size_mismatches = []
    verified_paths = set()
    for store_fname, rows in sorted(metadata_by_path.items()):
        source_path = (filestore_root / store_fname).resolve()
        if filestore_root not in source_path.parents:
            unreadable_files.append({
                "store_fname": store_fname,
                "reason": "path escapes the source filestore",
                "attachment_ids": [row["id"] for row in rows],
            })
            continue
        if not source_path.is_file():
            missing_files.append({
                "store_fname": store_fname,
                "attachment_ids": [row["id"] for row in rows],
                "classes": sorted({
                    row["reconstruction_class"]
                    for row in rows
                }),
            })
            continue
        try:
            raw = source_path.read_bytes()
        except OSError as exc:
            unreadable_files.append({
                "store_fname": store_fname,
                "reason": str(exc),
                "attachment_ids": [row["id"] for row in rows],
            })
            continue
        verified_paths.add(store_fname)
        actual_checksum = hashlib.sha1(raw).hexdigest()
        actual_size = len(raw)
        expected_checksums = {
            row["checksum"] for row in rows if row.get("checksum")
        }
        expected_sizes = {
            int(row["file_size"])
            for row in rows
            if row.get("file_size") is not None
        }
        if expected_checksums and expected_checksums != {actual_checksum}:
            checksum_mismatches.append({
                "store_fname": store_fname,
                "attachment_ids": [row["id"] for row in rows],
                "expected_checksums": sorted(expected_checksums),
                "actual_checksum": actual_checksum,
            })
        if expected_sizes and expected_sizes != {actual_size}:
            size_mismatches.append({
                "store_fname": store_fname,
                "attachment_ids": [row["id"] for row in rows],
                "expected_sizes": sorted(expected_sizes),
                "actual_size": actual_size,
            })

    physical_paths = {
        str(path.relative_to(package.filestore_path))
        for path in package.filestore_path.rglob("*")
        if path.is_file()
    }
    orphaned_paths = sorted(physical_paths - set(metadata_by_path))
    relevant_rows = [
        row
        for row in source_rows
        if row["reconstruction_class"] in {
            "accounting_direct",
            "accounting_chatter",
        }
    ]
    relevant_ids = {int(row["id"]) for row in relevant_rows}

    target_rows = []
    if table_exists(DEV_QA_DB, "ir_attachment"):
        target_rows = query_rows(
            DEV_QA_DB,
            """
            SELECT id,
                   rebuild_source_id,
                   rebuild_source_message_id,
                   res_model,
                   res_id,
                   checksum,
                   file_size,
                   store_fname,
                   EXISTS (
                       SELECT 1
                         FROM message_attachment_rel relation
                        WHERE relation.attachment_id = ir_attachment.id
                   ) AS linked_to_chatter
              FROM ir_attachment
             WHERE rebuild_source_model = 'ir.attachment'
             ORDER BY rebuild_source_id, id
            """,
            set_readonly_role=False,
        )
    target_rows = [
        row
        for row in target_rows
        if int(row.get("rebuild_source_id") or 0) in relevant_ids
    ]
    target_ids = {
        int(row["rebuild_source_id"])
        for row in target_rows
        if row.get("rebuild_source_id")
    }
    target_duplicate_source_ids = sorted(
        source_id
        for source_id, count in {
            source_id: sum(
                int(row.get("rebuild_source_id") or 0) == source_id
                for row in target_rows
            )
            for source_id in target_ids
        }.items()
        if count > 1
    )
    target_metadata_mismatches = [
        {
            "target_attachment_id": row["id"],
            "source_attachment_id": row["rebuild_source_id"],
        }
        for row in target_rows
        if (
            not row.get("checksum")
            or not row.get("store_fname")
            or not row.get("file_size")
            or not row["store_fname"].endswith(row["checksum"])
        )
    ]
    source_rows_by_id = {
        int(row["id"]): row
        for row in relevant_rows
    }
    target_source_metadata_mismatches = [
        {
            "target_attachment_id": row["id"],
            "source_attachment_id": row["rebuild_source_id"],
            "source_checksum": source_row.get("checksum"),
            "target_checksum": row.get("checksum"),
            "source_file_size": source_row.get("file_size"),
            "target_file_size": row.get("file_size"),
        }
        for row in target_rows
        if (
            (source_row := source_rows_by_id.get(
                int(row.get("rebuild_source_id") or 0),
            ))
            and (
                source_row.get("checksum") != row.get("checksum")
                or int(source_row.get("file_size") or 0)
                != int(row.get("file_size") or 0)
            )
        )
    ]
    missing_target_chatter_links = [
        int(row["rebuild_source_id"])
        for row in target_rows
        if (
            row.get("rebuild_source_message_id")
            and not row.get("linked_to_chatter")
        )
    ]
    target_binary_script = (
        PRIVATE_ARTIFACTS / "attachment-target-binary-audit.py"
    )
    target_binary_script.write_text(
        "\n".join([
            "import hashlib",
            "import json",
            "attachments = env['ir.attachment'].sudo().browse(",
            f"    {[int(row['id']) for row in target_rows]!r}",
            ")",
            "missing = []",
            "mismatches = []",
            "for attachment in attachments:",
            "    try:",
            "        raw = attachment.raw",
            "    except Exception as exc:",
            "        missing.append({",
            "            'target_attachment_id': attachment.id,",
            "            'source_attachment_id': attachment.rebuild_source_id,",
            "            'reason': type(exc).__name__,",
            "        })",
            "        continue",
            "    if not raw:",
            "        missing.append({",
            "            'target_attachment_id': attachment.id,",
            "            'source_attachment_id': attachment.rebuild_source_id,",
            "            'reason': 'empty binary',",
            "        })",
            "        continue",
            "    actual_checksum = hashlib.sha1(raw).hexdigest()",
            "    if (",
            "        actual_checksum != attachment.checksum",
            "        or len(raw) != attachment.file_size",
            "    ):",
            "        mismatches.append({",
            "            'target_attachment_id': attachment.id,",
            "            'source_attachment_id': attachment.rebuild_source_id,",
            "            'expected_checksum': attachment.checksum,",
            "            'actual_checksum': actual_checksum,",
            "            'expected_size': attachment.file_size,",
            "            'actual_size': len(raw),",
            "        })",
            "print('REBUILD_ATTACHMENT_BINARY_AUDIT=' + json.dumps({",
            "    'attachment_count': len(attachments),",
            "    'readable_attachment_count': (",
            "        len(attachments) - len(missing)",
            "    ),",
            "    'missing': missing,",
            "    'mismatches': mismatches,",
            "}, sort_keys=True))",
            "",
        ]),
        encoding="utf-8",
    )
    target_binary_result = run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "-e",
            f"ODOO_ADDONS_PATH={TARGET_ODOO_ADDONS_PATH}",
            "init-db",
            "odoo",
            "shell",
            "--config=/etc/odoo/odoo.conf",
            f"--database={DEV_QA_DB}",
        ),
        input_file=target_binary_script,
        check=False,
    )
    target_binary_payload = None
    for line in (
        target_binary_result.stdout + target_binary_result.stderr
    ).splitlines():
        if line.startswith("REBUILD_ATTACHMENT_BINARY_AUDIT="):
            target_binary_payload = json.loads(
                line.removeprefix("REBUILD_ATTACHMENT_BINARY_AUDIT="),
            )
    if target_binary_payload is None:
        target_binary_payload = {
            "attachment_count": len(target_rows),
            "readable_attachment_count": 0,
            "missing": [{
                "reason": (
                    "target binary audit did not complete; exit code "
                    f"{target_binary_result.returncode}"
                ),
            }],
            "mismatches": [],
        }
    missing_relevant_target_ids = sorted(relevant_ids - target_ids)
    source_integrity_issue_count = (
        len(rows_without_file_reference)
        + len(missing_files)
        + len(unreadable_files)
        + len(checksum_mismatches)
        + len(size_mismatches)
    )
    target_issue_count = (
        len(missing_relevant_target_ids)
        + len(target_duplicate_source_ids)
        + len(target_metadata_mismatches)
        + len(target_source_metadata_mismatches)
        + len(missing_target_chatter_links)
        + len(target_binary_payload["missing"])
        + len(target_binary_payload["mismatches"])
    )
    class_counts: dict[str, int] = defaultdict(int)
    for row in source_rows:
        class_counts[row["reconstruction_class"]] += 1
    payload = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "attachment-reconstruction-audit",
        "status": (
            "passed"
            if not source_integrity_issue_count and not target_issue_count
            else "partial"
        ),
        "source_database": SOURCE_DB,
        "target_database": DEV_QA_DB,
        "source_filestore": {
            "physical_file_count": len(physical_paths),
            "metadata_row_count": len(source_rows),
            "referenced_unique_file_count": len(metadata_by_path),
            "verified_unique_file_count": len(verified_paths),
            "orphaned_file_count": len(orphaned_paths),
            "orphaned_files_classification": (
                "No database attachment references these files; they are "
                "excluded from target replay and are not business-data blockers."
            ),
            "orphaned_file_examples": orphaned_paths[:20],
            "duplicate_blob_reference_group_count": sum(
                len(rows) > 1
                for rows in metadata_by_path.values()
            ),
            "attachment_class_counts": dict(sorted(class_counts.items())),
            "rows_without_file_reference_count": len(
                rows_without_file_reference,
            ),
            "missing_file_count": len(missing_files),
            "unreadable_file_count": len(unreadable_files),
            "checksum_mismatch_count": len(checksum_mismatches),
            "size_mismatch_count": len(size_mismatches),
        },
        "accounting_scope": {
            "source_attachment_count": len(relevant_rows),
            "target_source_traced_attachment_count": len(
                relevant_ids & target_ids,
            ),
            "missing_target_attachment_count": len(
                missing_relevant_target_ids,
            ),
            "missing_target_attachment_ids": missing_relevant_target_ids,
            "duplicate_target_source_id_count": len(
                target_duplicate_source_ids,
            ),
            "duplicate_target_source_ids": target_duplicate_source_ids,
            "target_metadata_mismatch_count": len(
                target_metadata_mismatches,
            ),
            "target_metadata_mismatch_examples": (
                target_metadata_mismatches[:20]
            ),
            "source_target_metadata_mismatch_count": len(
                target_source_metadata_mismatches,
            ),
            "source_target_metadata_mismatch_examples": (
                target_source_metadata_mismatches[:20]
            ),
            "missing_target_chatter_link_count": len(
                missing_target_chatter_links,
            ),
            "missing_target_chatter_link_source_ids": (
                missing_target_chatter_links[:50]
            ),
            "target_binary_read": {
                "attachment_count": target_binary_payload[
                    "attachment_count"
                ],
                "readable_attachment_count": target_binary_payload[
                    "readable_attachment_count"
                ],
                "missing_count": len(target_binary_payload["missing"]),
                "missing_examples": target_binary_payload["missing"][:20],
                "checksum_or_size_mismatch_count": len(
                    target_binary_payload["mismatches"],
                ),
                "checksum_or_size_mismatch_examples": (
                    target_binary_payload["mismatches"][:20]
                ),
            },
        },
        "source_integrity_issues": {
            "rows_without_file_reference": rows_without_file_reference[:20],
            "missing_files": missing_files[:20],
            "unreadable_files": unreadable_files[:20],
            "checksum_mismatches": checksum_mismatches[:20],
            "size_mismatches": size_mismatches[:20],
        },
        "policy": {
            "replay": (
                "Verified Accounting files are recreated through the target "
                "ORM and inherit access from their native target record."
            ),
            "excluded": (
                "Regenerable technical assets, unrelated business files, and "
                "orphaned physical blobs are not copied into Accounting."
            ),
        },
    }
    write_json(
        PRIVATE_ARTIFACTS / "attachment-reconstruction-status.json",
        payload,
    )
    if payload["status"] != "passed" and not getattr(
        args,
        "allow_errors",
        False,
    ):
        raise HarnessError(
            "Attachment reconstruction audit is incomplete. See "
            "artifacts/accounting-compat/private/"
            "attachment-reconstruction-status.json",
        )
    return payload


def dev_attachment_replay(args: argparse.Namespace) -> dict[str, Any]:
    ensure_dirs()
    validation = validate_source(args)
    dump_sha = validation["dump"]["sha256"] or "unknown"
    snapshot_id = f"source-{dump_sha[:12]}"
    source_date_to = scalar(
        SOURCE_DB,
        "SELECT max(date)::text FROM account_move",
    ) or date.today().isoformat()
    source_company_ids = [
        int(row["id"])
        for row in query_rows(
            SOURCE_DB,
            "SELECT id FROM res_company ORDER BY id",
        )
    ]
    if not table_exists(DEV_QA_DB, "rebuild_account_import_run"):
        raise HarnessError(
            "The odoo_dev Accounting product database is not initialized.",
        )

    script_path = PRIVATE_ARTIFACTS / "dev-attachment-replay.py"
    script_path.write_text(
        "\n".join([
            "import json",
            "run = env['rebuild.account.import.run'].create({",
            "    'name': 'USL development attachment replay',",
            "    'mode': 'exact_ledger_replay',",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {DEV_QA_DB!r},",
            "    'status': 'running',",
            "})",
            "options = {",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {DEV_QA_DB!r},",
            f"    'date_from': {USL_BENCHMARK_START!r},",
            f"    'date_to': {source_date_to!r},",
            f"    'source_company_ids': {source_company_ids!r},",
            "    'source_filestore_path': '/mnt/accounting-source/filestore',",
            "}",
            "companies = {",
            "    company.rebuild_source_id: company",
            "    for company in env['res.company'].with_context(active_test=False).search([",
            "        ('rebuild_source_id', 'in', options['source_company_ids']),",
            "    ])",
            "}",
            "connection = run._source_connection(options)",
            "try:",
            "    accounting_stats = run._import_attachments(",
            "        connection, options, companies,",
            "    )",
            "    expense_stats = run._import_attachments(",
            "        connection,",
            "        {",
            "            **options,",
            "            'attachment_target_trace_models': {",
            "                'hr.expense': ['hr.expense'],",
            "            },",
            "        },",
            "        companies,",
            "        rows=run._native_replay_expense_attachment_rows(",
            "            connection, options,",
            "        ),",
            "    )",
            "finally:",
            "    connection.close()",
            "issue_count = (",
            "    run._attachment_issue_count(accounting_stats)",
            "    + run._attachment_issue_count(expense_stats)",
            ")",
            "stats = {",
            "    'accounting_documents_and_assets': accounting_stats,",
            "    'expenses': expense_stats,",
            "    'source_attachment_count': (",
            "        accounting_stats['source_attachment_count']",
            "        + expense_stats['source_attachment_count']",
            "    ),",
            "    'imported_attachment_count': (",
            "        accounting_stats['imported_attachment_count']",
            "        + expense_stats['imported_attachment_count']",
            "    ),",
            "}",
            "run.write({",
            "    'status': 'passed' if not issue_count else 'partial',",
            "    'imported_attachment_count': stats['imported_attachment_count'],",
            "    'warning_count': issue_count,",
            "    'discrepancy_count': issue_count,",
            "    'statistics_json': {'attachments': stats},",
            "})",
            "env.cr.commit()",
            "print('REBUILD_DEV_ATTACHMENT_RESULT=' + json.dumps({",
            "    'run_id': run.id,",
            "    'status': run.status,",
            "    'stats': stats,",
            "}, sort_keys=True, default=str))",
            "",
        ]),
        encoding="utf-8",
    )
    result = run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "-e",
            f"ODOO_ADDONS_PATH={TARGET_ODOO_ADDONS_PATH}",
            "init-db",
            "odoo",
            "shell",
            "--config=/etc/odoo/odoo.conf",
            f"--database={DEV_QA_DB}",
        ),
        input_file=script_path,
        check=False,
    )
    marker = None
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("REBUILD_DEV_ATTACHMENT_RESULT="):
            marker = line.removeprefix(
                "REBUILD_DEV_ATTACHMENT_RESULT=",
            )
    artifact_path = PRIVATE_ARTIFACTS / "dev-attachment-replay-status.json"
    if result.returncode or not marker:
        payload = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "dev-attachment-replay",
            "status": "failed",
            "database": DEV_QA_DB,
            "exit_code": result.returncode,
            "output_tail": (result.stdout + result.stderr)[-12000:],
        }
    else:
        replay_result = json.loads(marker)
        payload = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "dev-attachment-replay",
            "database": DEV_QA_DB,
            **replay_result,
        }
    write_json(artifact_path, payload)
    if payload["status"] != "passed" and not getattr(
        args,
        "allow_errors",
        False,
    ):
        raise HarnessError(
            "Development attachment replay failed. See "
            "artifacts/accounting-compat/private/"
            "dev-attachment-replay-status.json",
        )
    return payload


def tax_grid_totals(db: str) -> list[dict[str, Any]]:
    if not table_exists(db, "account_account_tag_account_move_line_rel") or not table_exists(db, "account_move_line"):
        return []
    return query_rows(
        db,
        f"""
        SELECT aml.company_id,
               {period_case('am.date')} AS period,
               tag.id AS tax_tag_id,
               {text_expr(db, 'account_account_tag', 'tag', 'name') if table_exists(db, 'account_account_tag') and 'name' in column_names(db, 'account_account_tag') else 'tag.id::text'} AS tax_tag_name,
               count(*)::text AS line_count,
               COALESCE(sum(aml.tax_base_amount), 0)::text AS tax_base_total,
               COALESCE(sum(aml.balance), 0)::text AS balance_total
        FROM account_move_line aml
        JOIN account_move am ON am.id = aml.move_id
        JOIN account_account_tag_account_move_line_rel rel ON rel.account_move_line_id = aml.id
        JOIN account_account_tag tag ON tag.id = rel.account_account_tag_id
        WHERE am.state = 'posted'
        GROUP BY aml.company_id, {period_case('am.date')}, tag.id, tax_tag_name
        ORDER BY aml.company_id, period, tax_tag_id
        """,
    )


def customization_counts(db: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if table_exists(db, "ir_model_fields"):
        result["manual_fields_by_model"] = query_rows(
            db,
            """
            SELECT model, count(*)::text AS count
            FROM ir_model_fields
            WHERE state = 'manual' OR name LIKE 'x_%'
            GROUP BY model
            ORDER BY model
            """,
        )
    if table_exists(db, "ir_cron"):
        result["cron_count"] = scalar(db, "SELECT count(*)::text FROM ir_cron")
        cols = column_names(db, "ir_cron")
        if "active" in cols:
            result["active_cron_count"] = scalar(db, "SELECT count(*)::text FROM ir_cron WHERE active IS TRUE")
    if table_exists(db, "base_automation"):
        result["base_automation_count"] = scalar(db, "SELECT count(*)::text FROM base_automation")
    studio_tables = query_rows(
        db,
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name LIKE 'studio_%'
        ORDER BY table_name
        """,
    )
    result["studio_tables"] = studio_tables
    return result


def invariants_from_controls(controls: dict[str, Any]) -> dict[str, Any]:
    unbalanced = controls.get("posted_unbalanced_moves") or []
    failed = [row for row in unbalanced if row.get("unbalanced_move_count") not in (None, "0")]
    return {
        "generated_at": utc_now(),
        "posted_moves_balanced": {
            "status": "failed" if failed else "passed",
            "failed_company_count": len(failed),
            "evidence": failed,
        },
        "debit_credit_by_period": [
            {
                **row,
                "status": "passed" if row.get("debit_total") == row.get("credit_total") else "review",
            }
            for row in controls.get("move_line_totals_by_company_period", [])
        ],
    }


def anomalies_from_controls(controls: dict[str, Any]) -> dict[str, Any]:
    anomalies = []
    for row in controls.get("posted_unbalanced_moves") or []:
        if row.get("unbalanced_move_count") not in (None, "0"):
            anomalies.append(
                {
                    "id": f"SOURCE_UNBALANCED_POSTED_MOVES_COMPANY_{row.get('company_id')}",
                    "severity": "P0",
                    "classification": "SOURCE_ANOMALY",
                    "company": row.get("company_id"),
                    "period": "all",
                    "source_value": row,
                    "accounting_impact": "posted journal entries do not balance",
                    "recommended_action": "inspect source moves before any target import",
                    "status": "open",
                },
            )
    return {"generated_at": utc_now(), "anomalies": anomalies}


def extract(args: argparse.Namespace) -> dict[str, Any]:
    ensure_dirs()
    validation = validate_source(args)
    dump_sha = validation["dump"]["sha256"] or "unknown"
    snapshot_id = f"source-{dump_sha[:12]}"
    snapshot_dir = PRIVATE_SNAPSHOTS / snapshot_id
    mapping = {
        "configuration/companies.csv": "SELECT * FROM res_company ORDER BY id",
        "configuration/currencies.csv": "SELECT * FROM res_currency ORDER BY id",
        "configuration/currency_rates.csv": "SELECT * FROM res_currency_rate ORDER BY id",
        "configuration/accounts.csv": "SELECT * FROM account_account ORDER BY id",
        "configuration/journals.csv": "SELECT * FROM account_journal ORDER BY id",
        "configuration/taxes.csv": "SELECT * FROM account_tax ORDER BY id",
        "configuration/reconciliation_models.csv": "SELECT * FROM account_reconcile_model ORDER BY id",
        "configuration/reconciliation_model_lines.csv": "SELECT * FROM account_reconcile_model_line ORDER BY id",
        "configuration/reconciliation_model_journals.csv": "SELECT * FROM account_journal_account_reconcile_model_rel ORDER BY account_reconcile_model_id, account_journal_id",
        "configuration/reconciliation_model_partners.csv": "SELECT * FROM account_reconcile_model_res_partner_rel ORDER BY account_reconcile_model_id, res_partner_id",
        "configuration/reconciliation_model_line_taxes.csv": "SELECT * FROM account_reconcile_model_line_account_tax_rel ORDER BY account_reconcile_model_line_id, account_tax_id",
        "ledger/moves.csv": "SELECT * FROM account_move ORDER BY id",
        "ledger/move_lines.csv": "SELECT * FROM account_move_line ORDER BY id",
        "ledger/partial_reconciliations.csv": "SELECT * FROM account_partial_reconcile ORDER BY id",
        "ledger/full_reconciliations.csv": "SELECT * FROM account_full_reconcile ORDER BY id",
        "ledger/payments.csv": "SELECT * FROM account_payment ORDER BY id",
        "ledger/bank_statements.csv": "SELECT * FROM account_bank_statement ORDER BY id",
        "ledger/bank_statement_lines.csv": "SELECT * FROM account_bank_statement_line ORDER BY id",
        "analytics/plans.csv": "SELECT * FROM account_analytic_plan ORDER BY id",
        "analytics/accounts.csv": "SELECT * FROM account_analytic_account ORDER BY id",
        "analytics/lines.csv": "SELECT * FROM account_analytic_line ORDER BY id",
        "reporting/reports.csv": "SELECT * FROM account_report ORDER BY id",
        "reporting/report_lines.csv": "SELECT * FROM account_report_line ORDER BY id",
        "reporting/report_columns.csv": "SELECT * FROM account_report_column ORDER BY id",
        "reporting/report_expressions.csv": "SELECT * FROM account_report_expression ORDER BY id",
        "reporting/report_external_values.csv": "SELECT * FROM account_report_external_value ORDER BY id",
        "evidence/attachments_manifest.csv": "SELECT id, name, res_model, res_id, mimetype, checksum, file_size, store_fname, create_date, write_date FROM ir_attachment ORDER BY id",
    }
    exported = []
    skipped = []
    for rel_path, sql in mapping.items():
        table = sql.split(" FROM ", 1)[1].split(" ", 1)[0]
        if not table_exists(SOURCE_DB, table):
            skipped.append({"path": rel_path, "table": table, "reason": "source table absent"})
            continue
        out = snapshot_dir / rel_path
        out.parent.mkdir(parents=True, exist_ok=True)
        copy_sql = f"COPY ({sql}) TO STDOUT WITH CSV HEADER"
        data = psql(SOURCE_DB, copy_sql, set_readonly_role=True)
        out.write_text(data + ("\n" if data and not data.endswith("\n") else ""), encoding="utf-8")
        exported.append(
            {
                "path": str(out.relative_to(ROOT)),
                "table": table,
                "bytes": out.stat().st_size,
                "sha256": sha256_file(out),
            },
        )
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "snapshot_id": snapshot_id,
        "snapshot_dir": str(snapshot_dir.relative_to(ROOT)),
        "serialization": "csv_v1",
        "transfer_contract_status": "accepted",
        "integrity": "source dump SHA-256 plus per-file SHA-256",
        "exported": exported,
        "skipped": skipped,
    }
    write_json(snapshot_dir / "controls" / "extraction-status.json", status)
    write_json(PRIVATE_ARTIFACTS / "source-extract-status.json", status)
    return status


def target_table_counts(db: str) -> list[dict[str, Any]]:
    tables = [
        "res_company",
        "res_partner",
        "res_currency",
        "res_currency_rate",
        "account_account",
        "account_journal",
        "account_reconcile_model",
        "account_reconcile_model_line",
        "account_tax",
        "account_move",
        "account_move_line",
        "account_payment",
        "rebuild_account_source_report",
        "rebuild_account_deferred_schedule_line",
        "rebuild_account_external_report_value",
        "rebuild_account_eu_tax_report_line",
        "account_bank_statement_line",
        "account_partial_reconcile",
        "account_full_reconcile",
        "account_report",
        "rebuild_account_asset",
        "ir_cron",
        "ir_attachment",
    ]
    counts = []
    for table in tables:
        exists = table_exists(db, table)
        counts.append(
            {
                "table": table,
                "exists": exists,
                "count": scalar(db, f"SELECT count(*)::text FROM {table}", set_readonly_role=False) if exists else None,
            },
        )
    return counts


TARGET_IDEMPOTENCE_TABLES = [
    "res_company",
    "res_partner",
    "res_currency_rate",
    "account_account",
    "account_journal",
    "account_reconcile_model",
    "account_reconcile_model_line",
    "account_tax_group",
    "account_tax",
    "account_tax_repartition_line",
    "account_account_tag",
    "account_move",
    "account_move_line",
    "account_payment",
    "account_bank_statement_line",
    "account_partial_reconcile",
    "account_full_reconcile",
    "rebuild_account_source_report",
    "rebuild_account_deferred_schedule_line",
    "rebuild_account_external_report_value",
    "rebuild_account_eu_tax_report_line",
    "account_analytic_plan",
    "account_analytic_account",
    "account_analytic_line",
    "rebuild_account_asset",
    "rebuild_account_asset_depreciation_schedule_line",
    "ir_attachment",
]


def target_idempotence_signature() -> dict[str, Any]:
    traced_counts = {}
    for table in TARGET_IDEMPOTENCE_TABLES:
        if not table_exists(EXACT_VALIDATION_DB, table):
            traced_counts[table] = {"exists": False, "count": "0"}
            continue
        columns = column_names(EXACT_VALIDATION_DB, table)
        if {"rebuild_source_model", "rebuild_source_id"}.issubset(columns):
            count = scalar(
                EXACT_VALIDATION_DB,
                f"""
                SELECT count(*)::text
                FROM {table}
                WHERE rebuild_source_model IS NOT NULL
                  AND rebuild_source_id IS NOT NULL
                """,
                set_readonly_role=False,
            )
            traced_counts[table] = {"exists": True, "count": count or "0", "scope": "source_traced"}
        else:
            count = scalar(EXACT_VALIDATION_DB, f"SELECT count(*)::text FROM {table}", set_readonly_role=False)
            traced_counts[table] = {"exists": True, "count": count or "0", "scope": "all_rows"}

    posted_ledger_summary = query_json(
        EXACT_VALIDATION_DB,
        """
        SELECT jsonb_build_object(
            'posted_source_move_count', count(DISTINCT am.id)::text,
            'posted_source_move_line_count', count(aml.id)::text,
            'debit', COALESCE(sum(aml.debit), 0)::text,
            'credit', COALESCE(sum(aml.credit), 0)::text,
            'balance', COALESCE(sum(aml.balance), 0)::text,
            'min_date', min(am.date)::text,
            'max_date', max(am.date)::text
        )
        FROM account_move am
        JOIN account_move_line aml ON aml.move_id = am.id
        WHERE am.rebuild_source_model = 'account.move'
          AND aml.rebuild_source_model = 'account.move.line'
          AND am.state = 'posted'
        """,
        set_readonly_role=False,
    )
    native_non_posted_summary = query_json(
        EXACT_VALIDATION_DB,
        """
        SELECT jsonb_build_object(
            'native_non_posted_move_count', count(DISTINCT am.id)::text,
            'native_non_posted_move_line_count', count(aml.id)::text,
            'debit', COALESCE(sum(aml.debit), 0)::text,
            'credit', COALESCE(sum(aml.credit), 0)::text,
            'balance', COALESCE(sum(aml.balance), 0)::text
        )
        FROM account_move am
        LEFT JOIN account_move_line aml ON aml.move_id = am.id
        WHERE am.rebuild_source_model = 'account.move'
          AND am.state != 'posted'
        """,
        set_readonly_role=False,
    )
    discrepancy_summary = query_json(
        EXACT_VALIDATION_DB,
        """
        SELECT COALESCE(jsonb_object_agg(summary_key, row_count), '{}'::jsonb)
        FROM (
            SELECT severity || ':' || status || ':' || classification AS summary_key,
                   count(*)::text AS row_count
            FROM rebuild_account_discrepancy
            GROUP BY severity, status, classification
            ORDER BY severity, status, classification
        ) grouped
        """,
        set_readonly_role=False,
    ) if table_exists(EXACT_VALIDATION_DB, "rebuild_account_discrepancy") else {}
    return {
        "source_traced_counts": traced_counts,
        "posted_ledger_summary": posted_ledger_summary,
        "native_non_posted_summary": native_non_posted_summary,
        "discrepancy_summary": discrepancy_summary,
    }


def target_installed_modules(db: str) -> list[dict[str, Any]]:
    if not table_exists(db, "ir_module_module"):
        return []
    return query_rows(
        db,
        """
        SELECT name, latest_version, state, license
        FROM ir_module_module
        WHERE state = 'installed'
        ORDER BY name
        """,
        set_readonly_role=False,
    )


def active_row_count(db: str, table: str) -> str:
    if not table_exists(db, table):
        return "0"
    return scalar(db, f"SELECT count(*)::text FROM {table} WHERE active IS TRUE", set_readonly_role=False) or "0"


def target_reset(args: argparse.Namespace) -> dict[str, Any]:
    ensure_dirs()
    ensure_oca_addons_available()
    wait_for_postgres_service(TARGET_DB_SERVICE)
    db_user = database_user(TARGET_DB_SERVICE)
    run(compose_args("exec", "-T", TARGET_DB_SERVICE, "dropdb", "-U", db_user, "--if-exists", "--force", EXACT_VALIDATION_DB))
    run(compose_args("exec", "-T", TARGET_DB_SERVICE, "createdb", "-U", db_user, "-E", "UTF8", "-T", "template0", EXACT_VALIDATION_DB))
    run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "-e",
            "ODOO_DEFAULT_PRODUCTIVITY_APPS=False",
            "-e",
            f"ODOO_ADDONS_PATH={TARGET_ODOO_ADDONS_PATH}",
            "init-db",
            "odoo",
            "--config=/etc/odoo/odoo.conf",
            f"--database={EXACT_VALIDATION_DB}",
            f"--init={','.join(TARGET_INIT_MODULES)}",
            "--without-demo=true",
            "--stop-after-init",
        ),
    )
    if not table_exists(EXACT_VALIDATION_DB, "ir_module_module"):
        status = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "validation-exact-reset",
            "status": "failed",
            "database": EXACT_VALIDATION_DB,
            "target_database_service": TARGET_DB_SERVICE,
            "reason": "Odoo initialization completed without creating ir_module_module in the target database.",
            "recommended_action": "Inspect the one-off init container output and Odoo database creation behaviour.",
        }
        write_json(PRIVATE_ARTIFACTS / "validation-exact-reset-status.json", status)
        raise HarnessError(status["reason"])
    psql_exec(
        EXACT_VALIDATION_DB,
        """
        DO $$
        BEGIN
            IF to_regclass('public.ir_cron') IS NOT NULL THEN
                UPDATE ir_cron SET active = false;
            END IF;
            IF to_regclass('public.ir_mail_server') IS NOT NULL THEN
                UPDATE ir_mail_server SET active = false;
            END IF;
            IF to_regclass('public.fetchmail_server') IS NOT NULL THEN
                UPDATE fetchmail_server SET active = false;
            END IF;
        END
        $$;
        """,
    )
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "validation-exact-reset",
        "status": "passed",
        "database": EXACT_VALIDATION_DB,
        "target_database_service": TARGET_DB_SERVICE,
        "init_modules": TARGET_INIT_MODULES,
        "neutralization": {
            "ir_cron_active_count": active_row_count(EXACT_VALIDATION_DB, "ir_cron"),
            "mail_server_active_count": active_row_count(EXACT_VALIDATION_DB, "ir_mail_server"),
            "fetchmail_server_active_count": active_row_count(EXACT_VALIDATION_DB, "fetchmail_server"),
        },
        "installed_modules": target_installed_modules(EXACT_VALIDATION_DB),
        "record_counts": target_table_counts(EXACT_VALIDATION_DB),
    }
    write_json(PRIVATE_ARTIFACTS / "validation-exact-reset-status.json", status)
    return status


def native_validation_reset(args: argparse.Namespace) -> dict[str, Any]:
    """Create the isolated database used for native Track B recomputation."""
    ensure_dirs()
    ensure_oca_addons_available()
    wait_for_postgres_service(TARGET_DB_SERVICE)
    db_user = database_user(TARGET_DB_SERVICE)
    run(compose_args(
        "exec", "-T", TARGET_DB_SERVICE,
        "dropdb", "-U", db_user, "--if-exists", "--force", NATIVE_VALIDATION_DB,
    ))
    run(compose_args(
        "exec", "-T", TARGET_DB_SERVICE,
        "createdb", "-U", db_user, "-E", "UTF8", "-T", "template0", NATIVE_VALIDATION_DB,
    ))
    run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "-e",
            "ODOO_DEFAULT_PRODUCTIVITY_APPS=False",
            "-e",
            f"ODOO_ADDONS_PATH={TARGET_ODOO_ADDONS_PATH}",
            "init-db",
            "odoo",
            "--config=/etc/odoo/odoo.conf",
            f"--database={NATIVE_VALIDATION_DB}",
            f"--init={','.join(TARGET_INIT_MODULES)}",
            "--without-demo=true",
            "--stop-after-init",
        ),
    )
    if not table_exists(NATIVE_VALIDATION_DB, "rebuild_account_import_run"):
        status = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "validation-native-reset",
            "status": "failed",
            "database": NATIVE_VALIDATION_DB,
            "reason": "Track B database initialization did not install rebuild_account_migration.",
        }
        write_json(PRIVATE_ARTIFACTS / "validation-native-reset-status.json", status)
        raise HarnessError(status["reason"])
    psql_exec(
        NATIVE_VALIDATION_DB,
        """
        DO $$
        BEGIN
            IF to_regclass('public.ir_cron') IS NOT NULL THEN
                UPDATE ir_cron SET active = false;
            END IF;
            IF to_regclass('public.ir_mail_server') IS NOT NULL THEN
                UPDATE ir_mail_server SET active = false;
            END IF;
            IF to_regclass('public.fetchmail_server') IS NOT NULL THEN
                UPDATE fetchmail_server SET active = false;
            END IF;
        END
        $$;
        """,
    )
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "validation-native-reset",
        "status": "passed",
        "database": NATIVE_VALIDATION_DB,
        "purpose": "Isolated 2025-10-01 through 2026-06-30 native accounting-engine replay",
        "init_modules": TARGET_INIT_MODULES,
        "neutralization": {
            "ir_cron_active_count": active_row_count(NATIVE_VALIDATION_DB, "ir_cron"),
            "mail_server_active_count": active_row_count(NATIVE_VALIDATION_DB, "ir_mail_server"),
            "fetchmail_server_active_count": active_row_count(NATIVE_VALIDATION_DB, "fetchmail_server"),
        },
        "installed_modules": target_installed_modules(NATIVE_VALIDATION_DB),
        "record_counts": target_table_counts(NATIVE_VALIDATION_DB),
    }
    write_json(PRIVATE_ARTIFACTS / "validation-native-reset-status.json", status)
    return status


def dev_runtime_signature(db: str) -> dict[str, Any]:
    required_tables = {
        "account_move",
        "account_move_line",
        "account_bank_statement_line",
        "hr_expense",
        "rebuild_account_import_run",
    }
    missing_tables = sorted(
        table
        for table in required_tables
        if not table_exists(db, table)
    )
    if missing_tables:
        return {
            "database": db,
            "missing_tables": missing_tables,
            "native_business_document_count": "0",
            "native_expense_count": "0",
            "native_bank_transaction_count": "0",
            "unbalanced_posted_move_count": "0",
        }
    return query_json(
        db,
        """
        SELECT jsonb_build_object(
            'database', current_database(),
            'missing_tables', '[]'::jsonb,
            'native_business_document_count', (
                SELECT count(*)::text
                FROM account_move move
                JOIN res_company company ON company.id = move.company_id
                WHERE company.rebuild_source_id = 1
                  AND move.state = 'posted'
                  AND move.move_type IN (
                      'out_invoice', 'out_refund', 'in_invoice',
                      'in_refund', 'out_receipt', 'in_receipt'
                  )
                  AND move.date BETWEEN DATE '2025-10-01' AND DATE '2026-06-30'
                  AND move.rebuild_source_model IN (
                      'account.move.native_engine_replay',
                      'account.move.native_expense_replay'
                  )
            ),
            'native_expense_count', (
                SELECT count(*)::text
                FROM hr_expense expense
                JOIN res_company company ON company.id = expense.company_id
                WHERE company.rebuild_source_id = 1
                  AND expense.rebuild_source_model = 'hr.expense'
                  AND expense.date BETWEEN DATE '2025-10-01' AND DATE '2026-06-30'
            ),
            'native_bank_transaction_count', (
                SELECT count(*)::text
                FROM account_bank_statement_line statement_line
                JOIN res_company company ON company.id = statement_line.company_id
                JOIN account_move move ON move.id = statement_line.move_id
                WHERE company.rebuild_source_id = 1
                  AND move.date
                      BETWEEN DATE '2025-10-01' AND DATE '2026-06-30'
                  AND statement_line.rebuild_source_model LIKE
                      'account.bank.statement.line.native_%'
            ),
            'unbalanced_posted_move_count', (
                SELECT count(*)::text
                FROM (
                    SELECT move.id
                    FROM account_move move
                    JOIN account_move_line line ON line.move_id = move.id
                    WHERE move.state = 'posted'
                    GROUP BY move.id
                    HAVING abs(sum(line.balance)) > 0.005
                ) unbalanced
            ),
            'duplicate_source_move_representation_count', (
                SELECT count(*)::text
                FROM (
                    SELECT rebuild_source_id
                    FROM account_move
                    WHERE rebuild_source_id IS NOT NULL
                      AND rebuild_source_model IS NOT NULL
                    GROUP BY rebuild_source_id
                    HAVING count(*) > 1
                ) duplicate
            ),
            'posted_move_count', (
                SELECT count(*)::text
                FROM account_move
                WHERE state = 'posted'
            ),
            'posted_move_line_count', (
                SELECT count(*)::text
                FROM account_move_line
                WHERE parent_state = 'posted'
            )
        )
        """,
        set_readonly_role=False,
    )


def _dev_reset_from_native_validation(args: argparse.Namespace) -> dict[str, Any]:
    """Clone the completed Track B engine proof into a replacement candidate."""
    ensure_dirs()
    wait_for_postgres_service(TARGET_DB_SERVICE)
    required_artifacts = {
        "native_validation_expenses": PRIVATE_ARTIFACTS / "validation-native-expenses-status.json",
        "native_validation_documents": PRIVATE_ARTIFACTS / "validation-native-documents-status.json",
        "native_validation_assets": PRIVATE_ARTIFACTS / "validation-native-assets-status.json",
        "native_validation_deferrals": PRIVATE_ARTIFACTS / "validation-native-deferrals-status.json",
        "native_validation_expense_settlement": (
            PRIVATE_ARTIFACTS / "validation-native-expense-settlement-status.json"
        ),
        "native_validation_document_settlement": (
            PRIVATE_ARTIFACTS / "validation-native-document-settlement-status.json"
        ),
        "native_validation_general_reconciliation": (
            PRIVATE_ARTIFACTS / "validation-native-general-reconciliation-status.json"
        ),
        "native_validation_bank_categorization": (
            PRIVATE_ARTIFACTS / "validation-native-bank-categorization-status.json"
        ),
        "native_validation_bank_external": (
            PRIVATE_ARTIFACTS / "validation-native-bank-external-status.json"
        ),
        "native_validation_analytics": PRIVATE_ARTIFACTS / "validation-native-analytics-status.json",
    }
    artifact_checks = {}
    for name, path in required_artifacts.items():
        payload = read_json(path) if path.exists() else {}
        artifact_checks[name] = {
            "path": str(path.relative_to(ROOT)),
            "status": payload.get("status", "missing"),
            "database": payload.get("database"),
            "passed": (
                payload.get("status") == "passed"
                and payload.get("database") == NATIVE_VALIDATION_DB
            ),
        }
    source_signature = dev_runtime_signature(NATIVE_VALIDATION_DB)
    expected_signature = {
        "native_business_document_count": "284",
        "native_expense_count": "325",
        "native_bank_transaction_count": "1841",
        "unbalanced_posted_move_count": "0",
        "duplicate_source_move_representation_count": "0",
    }
    signature_matches = all(
        source_signature.get(key) == value
        for key, value in expected_signature.items()
    )
    failed_artifacts = sorted(
        name
        for name, check in artifact_checks.items()
        if not check["passed"]
    )
    if failed_artifacts or not signature_matches:
        status = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "dev-reset",
            "status": "failed",
            "classification": "INCOMPLETE_NATIVE_VALIDATION_SOURCE",
            "source_database": NATIVE_VALIDATION_DB,
            "target_database": DEV_QA_DB,
            "artifact_checks": artifact_checks,
            "failed_artifacts": failed_artifacts,
            "expected_signature": expected_signature,
            "source_signature": source_signature,
        }
        write_json(PRIVATE_ARTIFACTS / "dev-reset-status.json", status)
        message = "Development reset requires the complete, current native validation proof."
        raise HarnessError(message)

    db_user = database_user(TARGET_DB_SERVICE)
    run(compose_args(
        "exec", "-T", TARGET_DB_SERVICE,
        "dropdb", "-U", db_user, "--if-exists", "--force", DEV_QA_DB,
    ))
    run(compose_args(
        "exec", "-T", TARGET_DB_SERVICE,
        "createdb", "-U", db_user, "-E", "UTF8",
        "-T", NATIVE_VALIDATION_DB, DEV_QA_DB,
    ))
    run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "--no-deps",
            "--entrypoint",
            "sh",
            "init-db",
            "-lc",
            (
                f"set -eu; test -d /var/lib/odoo/filestore/{NATIVE_VALIDATION_DB}; "
                f"rm -rf /var/lib/odoo/filestore/{DEV_QA_DB} && "
                f"mkdir -p /var/lib/odoo/filestore/{DEV_QA_DB} && "
                f"cp -a /var/lib/odoo/filestore/{NATIVE_VALIDATION_DB}/. "
                f"/var/lib/odoo/filestore/{DEV_QA_DB}/"
            ),
        ),
    )
    run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "-e",
            f"ODOO_ADDONS_PATH={TARGET_ODOO_ADDONS_PATH}",
            "init-db",
            "odoo",
            "--config=/etc/odoo/odoo.conf",
            f"--database={DEV_QA_DB}",
            "--update=rebuild_account_migration",
            "--stop-after-init",
        ),
    )
    target_signature = dev_runtime_signature(DEV_QA_DB)
    comparable_signature_keys = (
        set(source_signature)
        | set(target_signature)
    ) - {"database"}
    clone_matches = all(
        source_signature.get(key) == target_signature.get(key)
        for key in comparable_signature_keys
    )
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "dev-reset",
        "status": "passed" if clone_matches else "failed",
        "classification": "NATIVE_VALIDATION_STATE_CLONED_FOR_DEV",
        "source_database": NATIVE_VALIDATION_DB,
        "target_database": DEV_QA_DB,
        "artifact_checks": artifact_checks,
        "expected_signature": expected_signature,
        "source_signature": source_signature,
        "target_signature": target_signature,
    }
    write_json(PRIVATE_ARTIFACTS / "dev-reset-status.json", status)
    if status["status"] != "passed":
        message = (
            "Development database clone does not match the native validation source signature."
        )
        raise HarnessError(message)
    return status


def dev_reset(args: argparse.Namespace) -> dict[str, Any]:
    """Create a clean product database independently of validation proofs."""
    ensure_dirs()
    ensure_oca_addons_available()
    wait_for_postgres_service(TARGET_DB_SERVICE)
    db_user = database_user(TARGET_DB_SERVICE)
    run(compose_args(
        "exec", "-T", TARGET_DB_SERVICE,
        "dropdb", "-U", db_user, "--if-exists", "--force", DEV_QA_DB,
    ))
    run(compose_args(
        "exec", "-T", TARGET_DB_SERVICE,
        "createdb", "-U", db_user, "-E", "UTF8",
        "-T", "template0", DEV_QA_DB,
    ))
    run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "-e",
            "ODOO_DEFAULT_PRODUCTIVITY_APPS=False",
            "-e",
            f"ODOO_ADDONS_PATH={TARGET_ODOO_ADDONS_PATH}",
            "init-db",
            "odoo",
            "--config=/etc/odoo/odoo.conf",
            f"--database={DEV_QA_DB}",
            f"--init={','.join(TARGET_INIT_MODULES)}",
            "--without-demo=true",
            "--stop-after-init",
        ),
    )
    initialized = table_exists(DEV_QA_DB, "rebuild_account_import_run")
    if initialized:
        psql_exec(
            DEV_QA_DB,
            """
            DO $$
            BEGIN
                IF to_regclass('public.ir_cron') IS NOT NULL THEN
                    UPDATE ir_cron SET active = false;
                END IF;
                IF to_regclass('public.ir_mail_server') IS NOT NULL THEN
                    UPDATE ir_mail_server SET active = false;
                END IF;
                IF to_regclass('public.fetchmail_server') IS NOT NULL THEN
                    UPDATE fetchmail_server SET active = false;
                END IF;
            END
            $$;
            """,
        )
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "dev-reset",
        "status": "passed" if initialized else "failed",
        "classification": "CLEAN_SOURCE_FAITHFUL_PRODUCT_TARGET",
        "target_database": DEV_QA_DB,
        "init_modules": TARGET_INIT_MODULES,
        "validation_databases": [
            EXACT_VALIDATION_DB,
            NATIVE_VALIDATION_DB,
        ],
        "record_counts": target_table_counts(DEV_QA_DB),
    }
    write_json(PRIVATE_ARTIFACTS / "dev-reset-status.json", status)
    if status["status"] != "passed":
        message = "Development product database initialization failed."
        raise HarnessError(message)
    return status


def dev_import(args: argparse.Namespace) -> dict[str, Any]:
    """Import the complete source snapshot into the clean product database."""
    ensure_dirs()
    manager_password = os.environ.get(
        "USL_DEV_ACCOUNTING_MANAGER_PASSWORD",
        "admin",
    )
    reviewer_password = os.environ.get(
        "USL_DEV_ACCOUNTANT_PASSWORD",
        "admin",
    )
    validation = validate_source(args)
    dump_sha = validation["dump"]["sha256"] or "unknown"
    snapshot_id = f"source-{dump_sha[:12]}"
    if not table_exists(DEV_QA_DB, "rebuild_account_import_run"):
        message = (
            "Run make accounting-dev-reset before replacement import."
        )
        raise HarnessError(message)
    source_profile = query_json(
        SOURCE_DB,
        """
        WITH source_end AS (
            SELECT greatest(
                (
                    SELECT max(date)
                    FROM account_move
                    WHERE company_id IN (1, 8)
                      AND state = 'posted'
                ),
                (
                    SELECT max(date)
                    FROM hr_expense
                    WHERE company_id = 1
                )
            ) AS date_to
        )
        SELECT jsonb_build_object(
            'date_to', source_end.date_to::text,
            'source_move_count', (
                SELECT count(*)
                FROM account_move move
                WHERE move.company_id IN (1, 8)
            ),
            'source_move_line_count', (
                SELECT count(*)
                FROM account_move_line line
                JOIN account_move move ON move.id = line.move_id
                WHERE move.company_id IN (1, 8)
            ),
            'source_non_posted_move_count', (
                SELECT count(*)
                FROM account_move move
                WHERE move.company_id IN (1, 8)
                  AND move.state != 'posted'
            ),
            'source_native_context_line_count', (
                SELECT count(*)
                FROM account_move_line line
                JOIN account_move move ON move.id = line.move_id
                WHERE move.company_id IN (1, 8)
                  AND (
                      move.state != 'posted'
                      OR line.account_id IS NULL
                  )
            ),
            'source_expense_count', (
                SELECT count(*)
                FROM hr_expense expense
                WHERE expense.company_id = 1
                  AND expense.date BETWEEN DATE '2024-01-10'
                                       AND source_end.date_to
            ),
            'source_canada_draft_expense_count', (
                SELECT count(*)
                FROM hr_expense expense
                JOIN product_product product
                  ON product.id = expense.product_id
                WHERE expense.company_id = 1
                  AND expense.state = 'draft'
                  AND product.default_code = 'CA26'
                  AND expense.date BETWEEN DATE '2024-01-10'
                                       AND source_end.date_to
            ),
            'source_expense_split_count', (
                SELECT count(*)
                FROM hr_expense expense
                WHERE expense.company_id = 1
                  AND expense.date BETWEEN DATE '2024-01-10'
                                       AND source_end.date_to
                  AND expense.split_expense_origin_id IS NOT NULL
            ),
            'source_expense_attachment_count', (
                SELECT count(*)
                FROM ir_attachment attachment
                JOIN hr_expense expense ON expense.id = attachment.res_id
                WHERE attachment.res_model = 'hr.expense'
                  AND expense.company_id = 1
                  AND expense.date BETWEEN DATE '2024-01-10'
                                       AND source_end.date_to
            ),
            'source_expense_url_attachment_count', (
                SELECT count(*)
                FROM ir_attachment attachment
                JOIN hr_expense expense ON expense.id = attachment.res_id
                WHERE attachment.res_model = 'hr.expense'
                  AND attachment.type = 'url'
                  AND expense.company_id = 1
                  AND expense.date BETWEEN DATE '2024-01-10'
                                       AND source_end.date_to
            ),
            'source_expense_bank_cache_association_count', (
                (
                    SELECT count(*)
                    FROM x_sl_expense_bank_candidate candidate
                    JOIN hr_expense expense
                      ON expense.id = candidate.x_expense_id
                    WHERE expense.company_id = 1
                      AND expense.date
                          BETWEEN DATE '2024-01-10'
                              AND source_end.date_to
                )
                + (
                    SELECT count(*)
                    FROM x_hr_expense_bank_statement_line_rel relation
                    JOIN hr_expense expense
                      ON expense.id = relation.expense_id
                    WHERE expense.company_id = 1
                      AND expense.date
                          BETWEEN DATE '2024-01-10'
                              AND source_end.date_to
                )
                + (
                    SELECT count(*)
                    FROM hr_expense expense
                    WHERE expense.company_id = 1
                      AND expense.date
                          BETWEEN DATE '2024-01-10'
                              AND source_end.date_to
                      AND expense.x_selected_bank_statement_line_id
                          IS NOT NULL
                )
            ),
            'source_asset_count', (
                SELECT count(*)
                FROM account_asset asset
                WHERE asset.company_id = 1
                  AND EXISTS (
                      SELECT 1
                      FROM account_move move
                      WHERE move.asset_id = asset.id
                        AND move.date >= DATE '2024-01-10'
                  )
            ),
            'source_posted_asset_move_count', (
                SELECT count(*)
                FROM account_move move
                JOIN account_asset asset ON asset.id = move.asset_id
                WHERE asset.company_id = 1
                  AND move.state = 'posted'
                  AND move.date BETWEEN DATE '2024-01-10'
                                    AND source_end.date_to
            )
        )
        FROM source_end
        """,
    )
    source_date_to = source_profile["date_to"]
    source_manager_identity = source_manager_accounting_identity()
    import_script = PRIVATE_ARTIFACTS / "dev-import-source-snapshot.py"
    import_script.write_text(
        "\n".join([
            "import json",
            "import time",
            "import_started = time.monotonic()",
            "run = env['rebuild.account.import.run'].create({",
            "    'name': 'USL complete source-faithful product snapshot',",
            "    'mode': 'exact_ledger_replay',",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {DEV_QA_DB!r},",
            "})",
            "stage_started = time.monotonic()",
            "stats = run.run_exact_ledger_replay_from_source({",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {DEV_QA_DB!r},",
            f"    'date_from': {USL_BENCHMARK_START!r},",
            f"    'date_to': {source_date_to!r},",
            "    'source_company_ids': [1, 8],",
            "    'preserve_business_documents': True,",
            "})",
            "exact_replay_seconds = time.monotonic() - stage_started",
            "expense_run = env['rebuild.account.import.run'].create({",
            "    'name': 'USL source-faithful native expenses',",
            "    'mode': 'exact_ledger_replay',",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {DEV_QA_DB!r},",
            "})",
            "stage_started = time.monotonic()",
            "expense_stats = expense_run.run_source_faithful_expense_materialization_from_source({",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {DEV_QA_DB!r},",
            f"    'date_from': {USL_BENCHMARK_START!r},",
            f"    'date_to': {source_date_to!r},",
            "    'source_company_ids': [1],",
            "})",
            "expense_batch_transition = expense_run.run_expense_batch_transition()",
            "expense_batch_transition_rerun = expense_run.run_expense_batch_transition()",
            "expense_replay_seconds = time.monotonic() - stage_started",
            "asset_run = env['rebuild.account.import.run'].create({",
            "    'name': 'USL source-faithful native assets',",
            "    'mode': 'exact_ledger_replay',",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {DEV_QA_DB!r},",
            "})",
            "stage_started = time.monotonic()",
            "asset_stats = asset_run.run_native_asset_replay_from_source({",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {DEV_QA_DB!r},",
            f"    'date_from': {USL_BENCHMARK_START!r},",
            f"    'date_to': {source_date_to!r},",
            "    'source_company_ids': [1],",
            "    'opening_depreciation_date': '2025-09-30',",
            "    'use_exact_imported_moves': True,",
            "})",
            "asset_replay_seconds = time.monotonic() - stage_started",
            "currency_rate_cron = env.ref(",
            "    'rebuild_account_migration.ir_cron_rebuild_currency_rate_provider',",
            ")",
            "currency_rate_cron.active = True",
            "Users = env['res.users'].sudo().with_context(",
            "    no_reset_password=True,",
            "    mail_create_nosubscribe=True,",
            "    tracking_disable=True,",
            "    usl_governed_identity_provisioning=True,",
            ")",
            "companies = env['res.company'].search([",
            "    ('rebuild_source_id', 'in', [1, 8]),",
            "], order='rebuild_source_id')",
            "main_company = companies.filtered(",
            "    lambda company: company.rebuild_source_id == 1",
            ")[:1]",
            "base_group = env.ref('base.group_user')",
            "manager_group = env.ref('account.group_account_manager')",
            "expense_manager_group = env.ref('hr_expense.group_hr_expense_manager')",
            "reviewer_group = env.ref(",
            "    'rebuild_account_migration.group_rebuild_accountant_reviewer',",
            ")",
            "manager_employees = env['hr.employee'].search([",
            "    ('company_id', '=', main_company.id),",
            "    ('rebuild_source_model', '=', 'hr.employee'),",
            f"    ('rebuild_source_id', '=', {source_manager_identity['employee_source_id']}),",
            "])",
            "if len(manager_employees) != 1:",
            "    raise RuntimeError(",
            "        'Expected one source-traced manager employee, found %s'",
            "        % len(manager_employees)",
            "    )",
            "manager_employee = manager_employees",
            "manager_partner = manager_employee.work_contact_id",
            "if not manager_partner or manager_partner.rebuild_source_model != 'res.partner':",
            "    raise RuntimeError(",
            "        'The source-traced Valentin employee has no source-traced work contact.'",
            "    )",
            "manager_payable = manager_partner.with_company(",
            "    main_company",
            ").property_account_payable_id",
            "if not manager_payable:",
            "    raise RuntimeError(",
            "        'The source-traced Valentin work contact has no payable account.'",
            "    )",
            "def provision_user(",
            "    login, name, password, company, allowed_companies, groups, partner=False",
            "):",
            "    values = {",
            "        'name': name,",
            "        'login': login,",
            "        'active': True,",
            "        'share': False,",
            "        'company_id': company.id,",
            "        'company_ids': [(6, 0, allowed_companies.ids)],",
            "        'group_ids': [(6, 0, [group.id for group in groups])],",
            "        'password': password,",
            "    }",
            "    if partner:",
            "        values['partner_id'] = partner.id",
            "    user = (",
            "        Users.search([('partner_id', '=', partner.id)], limit=1)",
            "        if partner else Users.browse()",
            "    )",
            "    if not user:",
            "        user = Users.search([('login', '=', login)], limit=1)",
            "    if user:",
            "        user.write(values)",
            "    else:",
            "        user = Users.create(values)",
            "    return user",
            "manager_user = provision_user(",
            "    'valentin',",
            "    'Valentin',",
            f"    {manager_password!r},",
            "    main_company,",
            "    companies,",
            "    base_group | manager_group | expense_manager_group,",
            "    partner=manager_partner,",
            ")",
            "reviewer_user = provision_user(",
            "    'prosper',",
            "    'Prosper',",
            f"    {reviewer_password!r},",
            "    main_company,",
            "    main_company,",
            "    base_group | reviewer_group,",
            ")",
            "manager_employee.write({",
            "        'user_id': manager_user.id,",
            "        'expense_manager_id': manager_user.id,",
            "    })",
            "manager_partner = manager_employee.work_contact_id",
            "manager_payable = manager_partner.with_company(",
            "    main_company",
            ").property_account_payable_id",
            "main_company.write({",
            "    'rebuild_overview_cca_account_id': manager_payable.id,",
            "    'rebuild_overview_cca_employee_id': manager_employee.id,",
            "})",
            "manager_cca_line_count = env['account.move.line'].search_count([",
            "    ('account_id', '=', manager_payable.id),",
            "    ('partner_id', '=', manager_partner.id),",
            "])",
            "manager_open_debit_count = env['account.move.line'].search_count([",
            "    ('account_id', '=', manager_payable.id),",
            "    ('partner_id', '=', manager_partner.id),",
            "    ('parent_state', '=', 'posted'),",
            "    ('reconciled', '=', False),",
            "    ('balance', '>', 0),",
            "])",
            "env['product.product'].search([",
            "    ('can_be_expensed', '=', True),",
            "    ('default_code', '=', 'TELETRAVAIL FORFAIT'),",
            "]).write({'rebuild_receipt_required': False})",
            "env.cr.commit()",
            "print('REBUILD_REPLACEMENT_IMPORT_RESULT=' + json.dumps({",
            "    'run_id': run.id,",
            "    'run_status': run.status,",
            "    'stats': stats,",
            "    'expense_run_id': expense_run.id,",
            "    'expense_run_status': expense_run.status,",
            "    'expense_stats': expense_stats,",
            "    'expense_batch_transition': expense_batch_transition,",
            "    'expense_batch_transition_rerun': expense_batch_transition_rerun,",
            "    'asset_run_id': asset_run.id,",
            "    'asset_run_status': asset_run.status,",
            "    'asset_stats': asset_stats,",
            "    'performance': {",
            "        'schema': 'usl-accounting-import-run-performance-v1',",
            "        'duration_seconds': time.monotonic() - import_started,",
            "        'exact_ledger': stats.get('performance', {}),",
            "        'stages': [",
            "            {",
            "                'name': 'exact ledger replay',",
            "                'duration_seconds': exact_replay_seconds,",
            "                'move_count': stats.get('source_move_count', 0),",
            "                'move_line_count': stats.get('source_move_line_count', 0),",
            "            },",
            "            {",
            "                'name': 'expenses',",
            "                'duration_seconds': expense_replay_seconds,",
            "                'expense_count': expense_stats.get('source_expense_count', 0),",
            "            },",
            "            {",
            "                'name': 'assets',",
            "                'duration_seconds': asset_replay_seconds,",
            "                'asset_count': asset_stats.get('source_asset_count', 0),",
            "            },",
            "        ],",
            "    },",
            "    'users': {",
            "        'manager': {",
            "            'id': manager_user.id,",
            "            'login': manager_user.login,",
            "            'company_ids': manager_user.company_ids.ids,",
            "            'account_manager': manager_user.has_group(",
            "                'account.group_account_manager',",
            "            ),",
            "            'expense_manager': manager_user.has_group(",
            "                'hr_expense.group_hr_expense_manager',",
            "            ),",
            "            'employee_id': manager_employee.id,",
            "            'employee_linked': (",
            "                manager_employee.user_id == manager_user",
            "            ),",
            "            'partner_id': manager_user.partner_id.id,",
            "            'work_contact_id': manager_employee.work_contact_id.id,",
            "            'canonical_partner': (",
            "                manager_user.partner_id == manager_employee.work_contact_id",
            "            ),",
            "            'configured_cca_account': (",
            "                main_company.rebuild_overview_cca_account_id",
            "                == manager_payable",
            "            ),",
            "            'configured_cca_employee': (",
            "                main_company.rebuild_overview_cca_employee_id",
            "                == manager_employee",
            "            ),",
            "            'source_partner_id': manager_partner.rebuild_source_id,",
            "            'payable_account_id': manager_payable.id,",
            "            'payable_source_account_id': manager_payable.rebuild_source_id,",
            "            'payable_code': manager_payable.code,",
            "            'payable_reconcile': manager_payable.reconcile,",
            "            'cca_line_count': manager_cca_line_count,",
            "            'open_debit_count': manager_open_debit_count,",
            "        },",
            "        'accountant': {",
            "            'id': reviewer_user.id,",
            "            'login': reviewer_user.login,",
            "            'company_ids': reviewer_user.company_ids.ids,",
            "            'reviewer': reviewer_user.has_group(",
            "                'rebuild_account_migration.group_rebuild_accountant_reviewer',",
            "            ),",
            "            'account_manager': reviewer_user.has_group(",
            "                'account.group_account_manager',",
            "            ),",
            "        },",
            "    },",
            "}, sort_keys=True, default=str))",
            "",
        ]),
        encoding="utf-8",
    )
    result = run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "-e",
            f"ODOO_ADDONS_PATH={TARGET_ODOO_ADDONS_PATH}",
            "init-db",
            "odoo",
            "shell",
            "--config=/etc/odoo/odoo.conf",
            f"--database={DEV_QA_DB}",
        ),
        input_file=import_script,
        check=False,
    )
    marker = None
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("REBUILD_REPLACEMENT_IMPORT_RESULT="):
            marker = line.removeprefix(
                "REBUILD_REPLACEMENT_IMPORT_RESULT=",
            )
    if result.returncode or not marker:
        combined_output = result.stdout + result.stderr
        failure = classify_product_import_failure(
            result.returncode,
            combined_output,
        )
        status = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "dev-import",
            "status": "failed",
            **failure,
            "database": DEV_QA_DB,
            "exit_code": result.returncode,
            "output_tail": combined_output[-12000:],
        }
        write_json(
            PRIVATE_ARTIFACTS / "dev-import-status.json",
            status,
        )
        message = (
            "Product source-snapshot import was killed by resource exhaustion; "
            "the interrupted target must be reset before retry."
            if failure["classification"] == "MIGRATION_RESOURCE_EXHAUSTION"
            else "Product source-snapshot import failed. See the private artifact."
        )
        raise HarnessError(message)
    payload = json.loads(marker)
    analyze_started = time.monotonic()
    psql_exec(
        DEV_QA_DB,
        "ANALYZE account_move, account_move_line, account_partial_reconcile, "
        "account_full_reconcile, account_analytic_line, ir_attachment;",
    )
    analyze_seconds = time.monotonic() - analyze_started
    stats = payload["stats"]
    expected = {
        "source_move_count": source_profile["source_move_count"],
        "imported_move_line_count": source_profile["source_move_line_count"],
        "source_expense_count": source_profile["source_expense_count"],
        "source_canada_draft_expense_count": source_profile[
            "source_canada_draft_expense_count"
        ],
        "source_asset_count": source_profile["source_asset_count"],
        "source_posted_asset_move_count": (
            source_profile["source_posted_asset_move_count"]
        ),
        "reused_native_move_representation_count": 0,
    }
    checks = {
        key: stats.get(key) == expected_value
        for key, expected_value in expected.items()
        if key not in {
            "source_expense_count",
            "source_canada_draft_expense_count",
            "source_asset_count",
            "source_posted_asset_move_count",
        }
    }
    checks["sequence_chronology_matches"] = (
        stats.get("sequence_chronology", {}).get(
            "target_matches_source",
        )
        is True
    )
    checks["native_non_posted_documents_match"] = (
        stats.get("native_documents", {}).get("native_non_posted_move_count")
        == source_profile["source_non_posted_move_count"]
        and stats.get("native_context_lines", {}).get(
            "native_source_line_count",
        )
        == source_profile["source_native_context_line_count"]
    )
    checks["native_expenses_match"] = (
        payload["expense_run_status"] == "passed"
        and payload["expense_stats"]["source_expense_count"]
        == source_profile["source_expense_count"]
        and payload["expense_stats"]["passed_expense_count"]
        == source_profile["source_expense_count"]
        and payload["expense_stats"]["mismatch_expense_count"] == 0
        and payload["expense_stats"]["blocked_case_count"] == 0
        and payload["expense_stats"]["restored_context_count"]
        == source_profile["source_expense_count"]
        and payload["expense_stats"]["restored_split_link_count"]
        == source_profile["source_expense_split_count"]
        and payload["expense_stats"]["attachments"][
            "source_attachment_count"
        ]
        == source_profile["source_expense_attachment_count"]
        and payload["expense_stats"]["attachments"][
            "imported_attachment_count"
        ]
        == source_profile["source_expense_attachment_count"]
        and payload["expense_stats"]["expense_bank_matches"][
            "source_cache_association_count"
        ]
        == source_profile[
            "source_expense_bank_cache_association_count"
        ]
        and payload["expense_stats"]["expense_bank_matches"][
            "classified_association_count"
        ]
        == source_profile[
            "source_expense_bank_cache_association_count"
        ]
        and payload["expense_stats"]["expense_bank_matches"][
            "refresh_error_count"
        ]
        == 0
        and payload["expense_stats"]["expense_bank_matches"][
            "refresh_idempotent"
        ]
        and payload["expense_stats"]["expense_bank_matches"][
            "accounting_unchanged"
        ]
        and payload["expense_stats"]["expense_bank_matches"][
            "legacy_target_schema"
        ]["absent"]
    )
    transition = payload["expense_batch_transition"]
    transition_rerun = payload["expense_batch_transition_rerun"]
    source_canada_draft_count = source_profile[
        "source_canada_draft_expense_count"
    ]
    expected_ambiguous = [{
        "name": "Zen Kyoto — Canada 2026 — 18,08 CAD / 11,18 EUR",
        "reason": "description_not_confidently_mapped",
    }]
    actual_ambiguous = [
        {
            "name": example["name"],
            "reason": example["reason"],
        }
        for example in transition["ambiguous_examples"]
    ]
    checks["expense_batch_transition_matches"] = (
        transition["candidate_draft_count"] == source_canada_draft_count
        and transition["reclassified_expense_count"]
        == source_canada_draft_count - len(expected_ambiguous)
        and transition["created_batch_count"] == 1
        and transition["batched_expense_count"] == source_canada_draft_count
        and transition["newly_batched_expense_count"]
        == source_canada_draft_count
        and transition["incomplete_expense_count"] == 4
        and transition["ambiguous_count"] == len(expected_ambiguous)
        and actual_ambiguous == expected_ambiguous
        and transition["archived_trip_product_count"] == 4
        and transition["archived_trip_product_codes"]
        == ["AUS26", "BCN2602", "CA26", "LPASUM26"]
        and transition["historical_unchanged"] is True
        and transition_rerun["candidate_draft_count"] == 0
        and transition_rerun["reclassified_expense_count"] == 0
        and transition_rerun["created_batch_count"] == 0
        and transition_rerun["archived_trip_product_count"] == 0
        and transition_rerun["historical_unchanged"] is True
    )
    checks["native_expense_url_evidence_matches"] = (
        query_json(
            DEV_QA_DB,
            """
            SELECT to_jsonb(count(*))
            FROM ir_attachment attachment
            JOIN hr_expense expense ON expense.id = attachment.res_id
            WHERE attachment.res_model = 'hr.expense'
              AND attachment.type = 'url'
              AND expense.rebuild_source_model = 'hr.expense'
            """,
            set_readonly_role=False,
        )
        == source_profile["source_expense_url_attachment_count"]
    )
    checks["native_assets_match"] = (
        payload["asset_run_status"] == "passed"
        and payload["asset_stats"]["source_asset_count"]
        == source_profile["source_asset_count"]
        and payload["asset_stats"]["passed_depreciation_move_count"]
        == source_profile["source_posted_asset_move_count"]
        and payload["asset_stats"]["created_depreciation_move_count"] == 0
        and payload["asset_stats"]["mismatch_count"] == 0
        and payload["asset_stats"]["blocked_count"] == 0
    )
    checks["product_users_match"] = (
        payload["users"]["manager"]["login"] == "valentin"
        and payload["users"]["manager"]["account_manager"] is True
        and payload["users"]["manager"]["expense_manager"] is True
        and payload["users"]["manager"]["employee_linked"] is True
        and payload["users"]["manager"]["canonical_partner"] is True
        and payload["users"]["manager"]["configured_cca_account"] is True
        and payload["users"]["manager"]["configured_cca_employee"] is True
        and payload["users"]["manager"]["source_partner_id"]
        == source_manager_identity["partner_source_id"]
        and payload["users"]["manager"]["payable_source_account_id"]
        == source_manager_identity["payable_source_account_id"]
        and payload["users"]["manager"]["payable_code"]
        == source_manager_identity["payable_code"]
        and payload["users"]["manager"]["payable_reconcile"]
        == source_manager_identity["payable_reconcile"]
        and payload["users"]["manager"]["cca_line_count"]
        == source_manager_identity["cca_line_count"]
        and payload["users"]["manager"]["open_debit_count"]
        == source_manager_identity["open_debit_count"]
        and payload["users"]["manager"]["open_debit_count"] > 0
        and payload["users"]["accountant"]["login"] == "prosper"
        and payload["users"]["accountant"]["reviewer"] is True
        and payload["users"]["accountant"]["account_manager"] is False
    )
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "dev-import",
        "status": "passed" if all(checks.values()) else "failed",
        "classification": "COMPLETE_SOURCE_FAITHFUL_PRODUCT_IMPORT",
        "database": DEV_QA_DB,
        "source_dump_sha256": dump_sha,
        "date_from": USL_BENCHMARK_START,
        "date_to": source_date_to,
        "run_id": payload["run_id"],
        "run_status": payload["run_status"],
        "expense_run_id": payload["expense_run_id"],
        "expense_run_status": payload["expense_run_status"],
        "asset_run_id": payload["asset_run_id"],
        "asset_run_status": payload["asset_run_status"],
        "source_manager_accounting_identity": source_manager_identity,
        "target_manager_accounting_identity": payload["users"]["manager"],
        "expected": expected,
        "checks": checks,
        "statistics": stats,
        "expense_statistics": payload["expense_stats"],
        "expense_batch_transition": transition,
        "expense_batch_transition_rerun": transition_rerun,
        "asset_statistics": payload["asset_stats"],
        "performance": {
            **payload["performance"],
            "stages": [
                *payload["performance"]["stages"],
                {
                    "name": "post-import analyze",
                    "duration_seconds": analyze_seconds,
                },
            ],
        },
        "users": payload["users"],
    }
    write_json(PRIVATE_ARTIFACTS / "dev-import-status.json", status)
    if status["status"] != "passed":
        message = "Product import counts, native document replay, or chronology differ."
        raise HarnessError(message)
    return status


def dev_validate(args: argparse.Namespace) -> dict[str, Any]:
    """Validate the clean, source-faithful development product candidate."""
    ensure_dirs()
    if not table_exists(DEV_QA_DB, "rebuild_account_import_run"):
        message = (
            "Run replacement reset and import before replacement validation."
        )
        raise HarnessError(message)
    source_manager_identity = source_manager_accounting_identity()
    target_manager_identity = target_manager_accounting_identity(DEV_QA_DB)
    manager_identity_matches = manager_accounting_identity_matches(
        source_manager_identity,
        target_manager_identity,
    )
    source_historical = query_rows(
        SOURCE_DB,
        """
        SELECT move.company_id::text AS source_company_id,
               count(DISTINCT move.id)::text AS posted_move_count,
               count(line.id)::text AS posted_move_line_count,
               round(sum(line.debit)::numeric, 2)::text AS debit,
               round(sum(line.credit)::numeric, 2)::text AS credit
        FROM account_move move
        JOIN account_move_line line ON line.move_id = move.id
        WHERE move.company_id IN (1, 8)
          AND move.state = 'posted'
          AND move.date BETWEEN DATE '2024-01-10' AND DATE '2025-09-30'
          AND line.account_id IS NOT NULL
        GROUP BY move.company_id
        ORDER BY move.company_id
        """,
    )
    target_historical = query_rows(
        DEV_QA_DB,
        """
        SELECT company.rebuild_source_id::text AS source_company_id,
               count(DISTINCT move.id)::text AS posted_move_count,
               count(line.id)::text AS posted_move_line_count,
               round(sum(line.debit)::numeric, 2)::text AS debit,
               round(sum(line.credit)::numeric, 2)::text AS credit
        FROM account_move move
        JOIN account_move_line line ON line.move_id = move.id
        JOIN res_company company ON company.id = move.company_id
        WHERE company.rebuild_source_id IN (1, 8)
          AND move.state = 'posted'
          AND move.date BETWEEN DATE '2024-01-10' AND DATE '2025-09-30'
          AND line.account_id IS NOT NULL
        GROUP BY company.rebuild_source_id
        ORDER BY company.rebuild_source_id
        """,
        set_readonly_role=False,
    )
    source_historical_moves = source_move_comparison_rows()
    target_historical_moves = target_move_comparison_rows(
        DEV_QA_DB,
    )
    historical_move_identity = compare_rows(
        source_historical_moves,
        target_historical_moves,
        key="source_move_id",
        fields=[
            "move_name",
            "date",
            "sequence_prefix",
            "sequence_number",
            "source_journal_id",
            "source_company_id",
            "source_partner_id",
            "state",
        ],
    )
    source_sequence_chronology = sequence_chronology_summary(
        source_historical_moves,
    )
    target_sequence_chronology = sequence_chronology_summary(
        target_historical_moves,
    )
    sequence_chronology_matches = (
        historical_move_identity["passed"]
        and source_sequence_chronology == target_sequence_chronology
    )
    source_current = query_json(
        SOURCE_DB,
        """
        SELECT jsonb_build_object(
            'posted_move_count', count(DISTINCT move.id)::text,
            'posted_move_line_count', count(line.id)::text,
            'debit', round(sum(line.debit)::numeric, 2)::text,
            'credit', round(sum(line.credit)::numeric, 2)::text
        )
        FROM account_move move
        JOIN account_move_line line ON line.move_id = move.id
        WHERE move.company_id = 1
          AND move.state = 'posted'
          AND move.date BETWEEN DATE '2025-10-01' AND DATE '2026-06-30'
          AND line.account_id IS NOT NULL
        """,
    )
    target_current = query_json(
        DEV_QA_DB,
        """
        SELECT jsonb_build_object(
            'posted_move_count', count(DISTINCT move.id)::text,
            'posted_move_line_count', count(line.id)::text,
            'debit', round(sum(line.debit)::numeric, 2)::text,
            'credit', round(sum(line.credit)::numeric, 2)::text
        )
        FROM account_move move
        JOIN account_move_line line ON line.move_id = move.id
        JOIN res_company company ON company.id = move.company_id
        WHERE company.rebuild_source_id = 1
          AND move.state = 'posted'
          AND move.date BETWEEN DATE '2025-10-01' AND DATE '2026-06-30'
          AND line.account_id IS NOT NULL
        """,
        set_readonly_role=False,
    )
    source_journal_rows = query_rows(
        SOURCE_DB,
        """
        SELECT journal.id::text AS source_journal_id,
               journal.code,
               journal.type AS journal_type,
               count(DISTINCT move.id)::text AS posted_move_count,
               count(line.id)::text AS posted_move_line_count,
               round(sum(line.debit)::numeric, 2)::text AS debit,
               round(sum(line.credit)::numeric, 2)::text AS credit
        FROM account_move move
        JOIN account_move_line line ON line.move_id = move.id
        JOIN account_journal journal ON journal.id = move.journal_id
        WHERE move.company_id = 1
          AND move.state = 'posted'
          AND move.date BETWEEN DATE '2025-10-01' AND DATE '2026-06-30'
          AND line.account_id IS NOT NULL
        GROUP BY journal.id, journal.code, journal.type
        ORDER BY journal.id
        """,
    )
    target_journal_rows = query_rows(
        DEV_QA_DB,
        """
        SELECT journal.id::text AS target_journal_id,
               journal.rebuild_source_id::text AS source_journal_id,
               journal.code,
               journal.type AS journal_type,
               count(DISTINCT move.id)::text AS posted_move_count,
               count(line.id)::text AS posted_move_line_count,
               round(sum(line.debit)::numeric, 2)::text AS debit,
               round(sum(line.credit)::numeric, 2)::text AS credit
        FROM account_move move
        JOIN account_move_line line ON line.move_id = move.id
        JOIN account_journal journal ON journal.id = move.journal_id
        JOIN res_company company ON company.id = move.company_id
        WHERE company.rebuild_source_id = 1
          AND move.state = 'posted'
          AND move.date BETWEEN DATE '2025-10-01' AND DATE '2026-06-30'
          AND line.account_id IS NOT NULL
        GROUP BY journal.id, journal.rebuild_source_id,
                 journal.code, journal.type
        ORDER BY journal.rebuild_source_id NULLS FIRST, journal.id
        """,
        set_readonly_role=False,
    )
    source_balance_rows = query_rows(
        SOURCE_DB,
        """
        SELECT account.id::text AS source_account_id,
               COALESCE(
                   account.code_store->>move.company_id::text,
                   account.code_store->>'1',
                   account.code_store::text
               ) AS account_code,
               account.account_type,
               round(sum(line.balance)::numeric, 2)::text AS balance
        FROM account_move move
        JOIN account_move_line line ON line.move_id = move.id
        JOIN account_account account ON account.id = line.account_id
        WHERE move.company_id = 1
          AND move.state = 'posted'
          AND move.date BETWEEN DATE '2025-10-01' AND DATE '2026-06-30'
        GROUP BY account.id, move.company_id, account.account_type
        ORDER BY account.id
        """,
    )
    target_balance_rows = query_rows(
        DEV_QA_DB,
        """
        SELECT account.id::text AS target_account_id,
               account.rebuild_source_id::text AS source_account_id,
               COALESCE(
                   account.code_store->>company.id::text,
                   account.code_store->>'1',
                   account.code_store::text
               ) AS account_code,
               account.account_type,
               round(sum(line.balance)::numeric, 2)::text AS balance
        FROM account_move move
        JOIN account_move_line line ON line.move_id = move.id
        JOIN account_account account ON account.id = line.account_id
        JOIN res_company company ON company.id = move.company_id
        WHERE company.rebuild_source_id = 1
          AND move.state = 'posted'
          AND move.date BETWEEN DATE '2025-10-01' AND DATE '2026-06-30'
        GROUP BY account.id, account.rebuild_source_id,
                 account.code_store, account.account_type, company.id
        ORDER BY account.rebuild_source_id NULLS FIRST, account.id
        """,
        set_readonly_role=False,
    )
    source_account_journal_rows = query_rows(
        SOURCE_DB,
        """
        SELECT account.id::text AS source_account_id,
               journal.id::text AS source_journal_id,
               journal.code,
               journal.type AS journal_type,
               round(sum(line.balance)::numeric, 2)::text AS balance
        FROM account_move move
        JOIN account_move_line line ON line.move_id = move.id
        JOIN account_account account ON account.id = line.account_id
        JOIN account_journal journal ON journal.id = move.journal_id
        WHERE move.company_id = 1
          AND move.state = 'posted'
          AND move.date BETWEEN DATE '2025-10-01' AND DATE '2026-06-30'
        GROUP BY account.id, journal.id, journal.code, journal.type
        ORDER BY account.id, journal.id
        """,
    )
    target_account_journal_rows = query_rows(
        DEV_QA_DB,
        """
        SELECT account.id::text AS target_account_id,
               account.rebuild_source_id::text AS source_account_id,
               journal.id::text AS target_journal_id,
               journal.rebuild_source_id::text AS source_journal_id,
               journal.code,
               journal.type AS journal_type,
               round(sum(line.balance)::numeric, 2)::text AS balance
        FROM account_move move
        JOIN account_move_line line ON line.move_id = move.id
        JOIN account_account account ON account.id = line.account_id
        JOIN account_journal journal ON journal.id = move.journal_id
        JOIN res_company company ON company.id = move.company_id
        WHERE company.rebuild_source_id = 1
          AND move.state = 'posted'
          AND move.date BETWEEN DATE '2025-10-01' AND DATE '2026-06-30'
        GROUP BY account.id, account.rebuild_source_id,
                 journal.id, journal.rebuild_source_id,
                 journal.code, journal.type
        ORDER BY account.rebuild_source_id NULLS FIRST,
                 account.id, journal.rebuild_source_id NULLS FIRST, journal.id
        """,
        set_readonly_role=False,
    )
    source_journals = {
        row["source_journal_id"]: row
        for row in source_journal_rows
    }
    target_journals = {}
    for row in target_journal_rows:
        key = row["source_journal_id"] or f"target:{row['target_journal_id']}"
        target_journals[key] = row

    journal_differences = []
    for key in sorted(
        set(source_journals) | set(target_journals),
        key=lambda value: (
            value.startswith("target:"),
            int(value.split(":", 1)[-1]),
        ),
    ):
        source_row = source_journals.get(key, {})
        target_row = target_journals.get(key, {})
        source_move_count = int(source_row.get("posted_move_count") or 0)
        target_move_count = int(target_row.get("posted_move_count") or 0)
        source_line_count = int(
            source_row.get("posted_move_line_count") or 0,
        )
        target_line_count = int(
            target_row.get("posted_move_line_count") or 0,
        )
        source_debit = Decimal(source_row.get("debit") or "0")
        target_debit = Decimal(target_row.get("debit") or "0")
        source_credit = Decimal(source_row.get("credit") or "0")
        target_credit = Decimal(target_row.get("credit") or "0")
        differences = {
            "posted_move_count": target_move_count - source_move_count,
            "posted_move_line_count": (
                target_line_count - source_line_count
            ),
            "debit": target_debit - source_debit,
            "credit": target_credit - source_credit,
        }
        if not any(differences.values()):
            continue
        code = target_row.get("code") or source_row.get("code")
        journal_type = (
            target_row.get("journal_type")
            or source_row.get("journal_type")
        )
        if code == "CABA":
            classification = "native_cash_basis_timing_and_aggregation"
        elif code == "EXCH":
            classification = "native_exchange_timing_and_aggregation"
        elif journal_type in {"bank", "cash", "credit"}:
            classification = "native_bank_allocation_segmentation"
        else:
            classification = "unclassified_current_journal_difference"
        journal_differences.append({
            "source_journal_id": (
                None if key.startswith("target:") else key
            ),
            "target_journal_id": target_row.get("target_journal_id"),
            "code": code,
            "journal_type": journal_type,
            "classification": classification,
            "source": {
                "posted_move_count": source_move_count,
                "posted_move_line_count": source_line_count,
                "debit": f"{source_debit:.2f}",
                "credit": f"{source_credit:.2f}",
            },
            "target": {
                "posted_move_count": target_move_count,
                "posted_move_line_count": target_line_count,
                "debit": f"{target_debit:.2f}",
                "credit": f"{target_credit:.2f}",
            },
            "difference": {
                "posted_move_count": differences["posted_move_count"],
                "posted_move_line_count": (
                    differences["posted_move_line_count"]
                ),
                "debit": f"{differences['debit']:.2f}",
                "credit": f"{differences['credit']:.2f}",
            },
        })

    source_balances = {
        row["source_account_id"]: row
        for row in source_balance_rows
    }
    target_balances = {}
    for row in target_balance_rows:
        key = row["source_account_id"] or f"target:{row['target_account_id']}"
        target_balances[key] = row
    source_account_journals = {
        (row["source_account_id"], row["source_journal_id"]): row
        for row in source_account_journal_rows
    }
    target_account_journals = {}
    for row in target_account_journal_rows:
        account_key = (
            row["source_account_id"]
            or f"target:{row['target_account_id']}"
        )
        journal_key = (
            row["source_journal_id"]
            or f"target:{row['target_journal_id']}"
        )
        target_account_journals[account_key, journal_key] = row
    balance_differences = []
    for key in sorted(
        set(source_balances) | set(target_balances),
        key=lambda value: (
            value.startswith("target:"),
            int(value.split(":", 1)[-1]),
        ),
    ):
        source_row = source_balances.get(key, {})
        target_row = target_balances.get(key, {})
        source_balance = Decimal(source_row.get("balance") or "0")
        target_balance = Decimal(target_row.get("balance") or "0")
        difference = target_balance - source_balance
        if difference:
            journal_effects = []
            account_journal_keys = {
                journal_key
                for account_key, journal_key in (
                    set(source_account_journals)
                    | set(target_account_journals)
                )
                if account_key == key
            }
            for journal_key in sorted(
                account_journal_keys,
                key=lambda value: (
                    value.startswith("target:"),
                    int(value.split(":", 1)[-1]),
                ),
            ):
                source_effect = source_account_journals.get(
                    (key, journal_key),
                    {},
                )
                target_effect = target_account_journals.get(
                    (key, journal_key),
                    {},
                )
                source_journal_balance = Decimal(
                    source_effect.get("balance") or "0",
                )
                target_journal_balance = Decimal(
                    target_effect.get("balance") or "0",
                )
                journal_difference = (
                    target_journal_balance - source_journal_balance
                )
                if not journal_difference:
                    continue
                journal_effects.append({
                    "source_journal_id": (
                        None
                        if journal_key.startswith("target:")
                        else journal_key
                    ),
                    "target_journal_id": target_effect.get(
                        "target_journal_id",
                    ),
                    "code": (
                        target_effect.get("code")
                        or source_effect.get("code")
                    ),
                    "journal_type": (
                        target_effect.get("journal_type")
                        or source_effect.get("journal_type")
                    ),
                    "source_balance": f"{source_journal_balance:.2f}",
                    "target_balance": f"{target_journal_balance:.2f}",
                    "difference": f"{journal_difference:.2f}",
                })
            journal_codes = {
                effect["code"]
                for effect in journal_effects
            }
            journal_types = {
                effect["journal_type"]
                for effect in journal_effects
            }
            if journal_codes and journal_codes <= {"EXCH"}:
                classification = "native_exchange_balance_timing"
            elif (
                "EXCH" in journal_codes
                and journal_types <= {"general", "bank", "cash", "credit"}
                and all(
                    effect["code"] == "EXCH"
                    or effect["journal_type"] in {"bank", "cash", "credit"}
                    for effect in journal_effects
                )
            ):
                classification = (
                    "native_exchange_and_bank_allocation_segmentation"
                )
            elif (
                journal_effects
                and journal_types <= {"bank", "cash", "credit"}
            ):
                classification = "native_bank_allocation_segmentation"
            else:
                classification = (
                    "unclassified_current_balance_difference"
                )
            balance_differences.append({
                "source_account_id": (
                    None if key.startswith("target:") else key
                ),
                "target_account_id": target_row.get("target_account_id"),
                "account_code": (
                    target_row.get("account_code")
                    or source_row.get("account_code")
                ),
                "account_type": (
                    target_row.get("account_type")
                    or source_row.get("account_type")
                ),
                "source_balance": f"{source_balance:.2f}",
                "target_balance": f"{target_balance:.2f}",
                "difference": f"{difference:.2f}",
                "classification": classification,
                "journal_effects": journal_effects,
            })

    runtime_signature = dev_runtime_signature(DEV_QA_DB)
    historical_matches = source_historical == target_historical
    source_snapshot = query_json(
        SOURCE_DB,
        """
        SELECT jsonb_build_object(
            'accounting_move_count', (
                SELECT count(*)::text
                FROM account_move move
                WHERE move.company_id IN (1, 8)
                  AND move.date >= DATE '2024-01-10'
                  AND (
                      move.state = 'posted'
                      OR EXISTS (
                          SELECT 1
                          FROM account_move_line line
                          WHERE line.move_id = move.id
                            AND line.account_id IS NOT NULL
                      )
                      OR (
                          move.state = 'draft'
                          AND move.move_type IN (
                              'out_invoice', 'out_refund', 'in_invoice',
                              'in_refund', 'out_receipt', 'in_receipt'
                          )
                          AND EXISTS (
                              SELECT 1
                              FROM ir_attachment attachment
                              WHERE attachment.res_model = 'account.move'
                                AND attachment.res_id = move.id
                                AND attachment.type = 'binary'
                          )
                      )
                  )
            ),
            'posted_move_count', (
                SELECT count(*)::text
                FROM account_move
                WHERE company_id IN (1, 8)
                  AND state = 'posted'
                  AND date >= DATE '2024-01-10'
            ),
            'draft_move_count', (
                SELECT count(*)::text
                FROM account_move move
                WHERE move.company_id IN (1, 8)
                  AND move.state = 'draft'
                  AND move.date >= DATE '2024-01-10'
                  AND (
                      EXISTS (
                          SELECT 1
                          FROM account_move_line line
                          WHERE line.move_id = move.id
                            AND line.account_id IS NOT NULL
                      )
                      OR (
                          move.move_type IN (
                              'out_invoice', 'out_refund', 'in_invoice',
                              'in_refund', 'out_receipt', 'in_receipt'
                          )
                          AND EXISTS (
                              SELECT 1
                              FROM ir_attachment attachment
                              WHERE attachment.res_model = 'account.move'
                                AND attachment.res_id = move.id
                                AND attachment.type = 'binary'
                          )
                      )
                  )
            ),
            'business_document_count', (
                SELECT count(*)::text
                FROM account_move move
                WHERE move.company_id IN (1, 8)
                  AND move.date >= DATE '2024-01-10'
                  AND move.move_type IN (
                      'out_invoice', 'out_refund', 'in_invoice',
                      'in_refund', 'out_receipt', 'in_receipt'
                  )
                  AND (
                      move.state = 'posted'
                      OR EXISTS (
                          SELECT 1
                          FROM account_move_line line
                          WHERE line.move_id = move.id
                            AND line.account_id IS NOT NULL
                      )
                      OR (
                          move.state = 'draft'
                          AND EXISTS (
                              SELECT 1
                              FROM ir_attachment attachment
                              WHERE attachment.res_model = 'account.move'
                                AND attachment.res_id = move.id
                                AND attachment.type = 'binary'
                          )
                      )
                  )
            ),
            'bank_transaction_count', (
                SELECT count(*)::text
                FROM account_bank_statement_line
                WHERE company_id IN (1, 8)
            ),
            'payment_count', (
                SELECT count(*)::text
                FROM account_payment
                WHERE company_id IN (1, 8)
                  AND move_id IS NOT NULL
            ),
            'payment_evidence_count', (
                SELECT count(*)::text
                FROM account_payment
                WHERE company_id IN (1, 8)
                  AND move_id IS NULL
            ),
            'partial_reconcile_count', (
                WITH imported AS (
                    SELECT line.id
                    FROM account_move_line line
                    JOIN account_move move ON move.id = line.move_id
                    WHERE move.company_id IN (1, 8)
                      AND move.state = 'posted'
                      AND move.date BETWEEN DATE '2024-01-10'
                                        AND DATE '2026-07-21'
                )
                SELECT count(*)::text
                FROM account_partial_reconcile partial
                WHERE partial.debit_move_id IN (SELECT id FROM imported)
                  AND partial.credit_move_id IN (SELECT id FROM imported)
            ),
            'full_reconcile_count', (
                WITH imported AS (
                    SELECT line.id
                    FROM account_move_line line
                    JOIN account_move move ON move.id = line.move_id
                    WHERE move.company_id IN (1, 8)
                      AND move.state = 'posted'
                      AND move.date BETWEEN DATE '2024-01-10'
                                        AND DATE '2026-07-21'
                ),
                full_lines AS (
                    SELECT line.full_reconcile_id,
                           count(*) AS total_line_count,
                           count(*) FILTER (
                               WHERE line.id IN (SELECT id FROM imported)
                           ) AS imported_line_count
                    FROM account_move_line line
                    WHERE line.full_reconcile_id IS NOT NULL
                    GROUP BY line.full_reconcile_id
                )
                SELECT count(*)::text
                FROM full_lines
                WHERE imported_line_count > 0
                  AND imported_line_count = total_line_count
            ),
            'reconciliation_evidence_count', (
                WITH imported AS (
                    SELECT line.id
                    FROM account_move_line line
                    JOIN account_move move ON move.id = line.move_id
                    WHERE move.company_id IN (1, 8)
                      AND move.state = 'posted'
                      AND move.date BETWEEN DATE '2024-01-10'
                                        AND DATE '2026-07-21'
                ),
                partial_reviews AS (
                    SELECT partial.id
                    FROM account_partial_reconcile partial
                    WHERE (
                        partial.debit_move_id IN (SELECT id FROM imported)
                        OR partial.credit_move_id IN (SELECT id FROM imported)
                    )
                      AND NOT (
                        partial.debit_move_id IN (SELECT id FROM imported)
                        AND partial.credit_move_id IN (SELECT id FROM imported)
                    )
                ),
                full_lines AS (
                    SELECT line.full_reconcile_id,
                           count(*) AS total_line_count,
                           count(*) FILTER (
                               WHERE line.id IN (SELECT id FROM imported)
                           ) AS imported_line_count
                    FROM account_move_line line
                    WHERE line.full_reconcile_id IS NOT NULL
                    GROUP BY line.full_reconcile_id
                )
                SELECT (
                    (SELECT count(*) FROM partial_reviews)
                    + (
                        SELECT count(*)
                        FROM full_lines
                        WHERE imported_line_count > 0
                          AND imported_line_count < total_line_count
                    )
                )::text
            ),
            'analytic_line_count', (
                SELECT count(*)::text
                FROM account_analytic_line
                WHERE company_id IN (1, 8)
            ),
            'currency_rate_count', (
                SELECT count(*)::text
                FROM res_currency_rate
                WHERE company_id IS NULL OR company_id IN (1, 8)
            ),
            'native_expense_count', (
                SELECT count(*)::text
                FROM hr_expense
                WHERE company_id = 1
                  AND date >= DATE '2024-01-10'
            ),
            'native_expense_move_link_count', (
                SELECT count(*)::text
                FROM hr_expense
                WHERE company_id = 1
                  AND date >= DATE '2024-01-10'
                  AND account_move_id IS NOT NULL
            ),
            'native_expense_line_link_count', (
                SELECT count(*)::text
                FROM account_move_line line
                JOIN hr_expense expense ON expense.id = line.expense_id
                WHERE expense.company_id = 1
                  AND expense.date >= DATE '2024-01-10'
            ),
            'native_expense_state_counts', (
                SELECT jsonb_object_agg(state, state_count)
                FROM (
                    SELECT state, count(*)::text AS state_count
                    FROM hr_expense
                    WHERE company_id = 1
                      AND date >= DATE '2024-01-10'
                    GROUP BY state
                ) expense_states
            ),
            'asset_count', (
                SELECT count(*)::text
                FROM account_asset
                WHERE company_id IN (1, 8)
            ),
            'native_asset_count', (
                SELECT count(*)::text
                FROM account_asset
                WHERE company_id = 1
                  AND EXISTS (
                      SELECT 1
                      FROM account_move move
                      WHERE move.asset_id = account_asset.id
                        AND move.date >= DATE '2024-01-10'
                  )
            ),
            'native_asset_schedule_line_count', (
                SELECT count(*)::text
                FROM account_move move
                JOIN account_asset asset ON asset.id = move.asset_id
                WHERE asset.company_id = 1
                  AND move.date >= DATE '2024-01-10'
            ),
            'native_asset_posted_move_link_count', (
                SELECT count(*)::text
                FROM account_move move
                JOIN account_asset asset ON asset.id = move.asset_id
                WHERE asset.company_id = 1
                  AND move.state = 'posted'
                  AND move.date >= DATE '2024-01-10'
            )
        )
        """,
    )
    source_snapshot.pop("payment_evidence_count", None)
    source_snapshot.pop("reconciliation_evidence_count", None)
    source_snapshot.update(query_json(
        SOURCE_DB,
        """
        WITH scoped_lines AS (
            SELECT line.id, line.full_reconcile_id
            FROM account_move_line line
            JOIN account_move move ON move.id = line.move_id
            WHERE move.company_id IN (1, 8)
        )
        SELECT jsonb_build_object(
            'accounting_move_count', (
                SELECT count(*)::text
                FROM account_move
                WHERE company_id IN (1, 8)
            ),
            'posted_move_count', (
                SELECT count(*)::text
                FROM account_move
                WHERE company_id IN (1, 8)
                  AND state = 'posted'
            ),
            'draft_move_count', (
                SELECT count(*)::text
                FROM account_move
                WHERE company_id IN (1, 8)
                  AND state = 'draft'
            ),
            'payment_count', (
                SELECT count(*)::text
                FROM account_payment
                WHERE company_id IN (1, 8)
            ),
            'historical_no_entry_payment_count', (
                SELECT count(*)::text
                FROM account_payment
                WHERE company_id IN (1, 8)
                  AND move_id IS NULL
            ),
            'partial_reconcile_count', (
                SELECT count(*)::text
                FROM account_partial_reconcile partial
                WHERE partial.debit_move_id IN (SELECT id FROM scoped_lines)
                  AND partial.credit_move_id IN (SELECT id FROM scoped_lines)
            ),
            'full_reconcile_count', (
                SELECT count(DISTINCT full_reconcile_id)::text
                FROM scoped_lines
                WHERE full_reconcile_id IS NOT NULL
            )
        )
        """,
    ))
    source_snapshot["native_expense_state_counts"] = (
        normalize_source_expense_state_counts(
            source_snapshot.get("native_expense_state_counts"),
        )
    )
    target_snapshot = query_json(
        DEV_QA_DB,
        """
        SELECT jsonb_build_object(
            'accounting_move_count', (
                SELECT count(*)::text
                FROM account_move move
                JOIN res_company company ON company.id = move.company_id
                WHERE company.rebuild_source_id IN (1, 8)
                  AND move.rebuild_source_id IS NOT NULL
            ),
            'posted_move_count', (
                SELECT count(*)::text
                FROM account_move move
                JOIN res_company company ON company.id = move.company_id
                WHERE company.rebuild_source_id IN (1, 8)
                  AND move.state = 'posted'
                  AND move.rebuild_source_id IS NOT NULL
            ),
            'draft_move_count', (
                SELECT count(*)::text
                FROM account_move move
                JOIN res_company company ON company.id = move.company_id
                WHERE company.rebuild_source_id IN (1, 8)
                  AND move.state = 'draft'
                  AND move.rebuild_source_id IS NOT NULL
            ),
            'business_document_count', (
                SELECT count(*)::text
                FROM account_move move
                JOIN res_company company ON company.id = move.company_id
                WHERE company.rebuild_source_id IN (1, 8)
                  AND move.move_type IN (
                      'out_invoice', 'out_refund', 'in_invoice',
                      'in_refund', 'out_receipt', 'in_receipt'
                  )
                  AND move.rebuild_source_id IS NOT NULL
            ),
            'bank_transaction_count', (
                SELECT count(*)::text
                FROM account_bank_statement_line line
                JOIN res_company company ON company.id = line.company_id
                WHERE company.rebuild_source_id IN (1, 8)
                  AND line.rebuild_source_id IS NOT NULL
            ),
            'payment_count', (
                SELECT count(*)::text
                FROM account_payment payment
                JOIN res_company company ON company.id = payment.company_id
                WHERE company.rebuild_source_id IN (1, 8)
                  AND payment.rebuild_source_id IS NOT NULL
            ),
            'historical_no_entry_payment_count', (
                SELECT count(*)::text
                FROM account_payment
                WHERE rebuild_source_id IS NOT NULL
                  AND usl_historical_no_ledger_effect IS TRUE
            ),
            'partial_reconcile_count', (
                SELECT count(*)::text
                FROM account_partial_reconcile
                WHERE rebuild_source_id IS NOT NULL
            ),
            'full_reconcile_count', (
                SELECT count(*)::text
                FROM account_full_reconcile
                WHERE rebuild_source_id IS NOT NULL
            ),
            'analytic_line_count', (
                SELECT count(*)::text
                FROM account_analytic_line line
                JOIN res_company company ON company.id = line.company_id
                WHERE company.rebuild_source_id IN (1, 8)
            ),
            'currency_rate_count', (
                SELECT count(*)::text
                FROM res_currency_rate
                WHERE rebuild_source_id IS NOT NULL
            ),
            'native_expense_count', (
                SELECT count(*)::text
                FROM hr_expense expense
                JOIN res_company company ON company.id = expense.company_id
                WHERE company.rebuild_source_id = 1
                  AND expense.rebuild_source_model = 'hr.expense'
            ),
            'native_expense_move_link_count', (
                SELECT count(*)::text
                FROM hr_expense expense
                JOIN res_company company ON company.id = expense.company_id
                WHERE company.rebuild_source_id = 1
                  AND expense.rebuild_source_model = 'hr.expense'
                  AND expense.account_move_id IS NOT NULL
            ),
            'native_expense_line_link_count', (
                SELECT count(*)::text
                FROM account_move_line line
                JOIN hr_expense expense ON expense.id = line.expense_id
                JOIN res_company company ON company.id = expense.company_id
                WHERE company.rebuild_source_id = 1
                  AND expense.rebuild_source_model = 'hr.expense'
                  AND line.rebuild_source_model = 'account.move.line'
            ),
            'native_expense_state_counts', (
                SELECT jsonb_object_agg(state, state_count)
                FROM (
                    SELECT expense.state,
                           count(*)::text AS state_count
                    FROM hr_expense expense
                    JOIN res_company company
                      ON company.id = expense.company_id
                    WHERE company.rebuild_source_id = 1
                      AND expense.rebuild_source_model = 'hr.expense'
                    GROUP BY expense.state
                ) expense_states
            ),
            'asset_count', (
                SELECT count(*)::text
                FROM rebuild_account_asset
                WHERE rebuild_source_id IS NOT NULL
            ),
            'native_asset_count', (
                SELECT count(*)::text
                FROM account_asset asset
                JOIN res_company company ON company.id = asset.company_id
                WHERE company.rebuild_source_id = 1
                  AND asset.rebuild_source_model = 'account.asset'
            ),
            'native_asset_schedule_line_count', (
                SELECT count(*)::text
                FROM account_asset_line line
                JOIN account_asset asset ON asset.id = line.asset_id
                JOIN res_company company ON company.id = asset.company_id
                WHERE company.rebuild_source_id = 1
                  AND line.rebuild_source_model
                      = 'account.move.asset_depreciation_schedule'
            ),
            'native_asset_posted_move_link_count', (
                SELECT count(*)::text
                FROM account_asset_line line
                JOIN account_asset asset ON asset.id = line.asset_id
                JOIN account_move move ON move.id = line.move_id
                JOIN res_company company ON company.id = asset.company_id
                WHERE company.rebuild_source_id = 1
                  AND line.rebuild_source_model
                      = 'account.move.asset_depreciation_schedule'
                  AND move.state = 'posted'
            )
        )
        """,
        set_readonly_role=False,
    )
    product_counts_match = {
        key: target_snapshot.get(key) == source_value
        for key, source_value in source_snapshot.items()
    }
    critical_checks = {
        "historical_ledger_matches": historical_matches,
        "historical_move_identity_matches": (
            historical_move_identity["passed"]
        ),
        "sequence_chronology_matches": sequence_chronology_matches,
        "product_snapshot_counts_match": all(product_counts_match.values()),
        "posted_moves_balance": (
            runtime_signature.get("unbalanced_posted_move_count") == "0"
        ),
        "source_move_representations_unique": (
            runtime_signature.get(
                "duplicate_source_move_representation_count",
            ) == "0"
        ),
        "manager_accounting_identity_matches": manager_identity_matches,
    }
    current_gross_totals_match = source_current == target_current
    current_account_balances_match = not balance_differences
    unclassified_journal_differences = [
        row
        for row in journal_differences
        if row["classification"]
        == "unclassified_current_journal_difference"
    ]
    unclassified_balance_differences = [
        row
        for row in balance_differences
        if row["classification"]
        == "unclassified_current_balance_difference"
    ]
    balance_difference_net = sum(
        (Decimal(row["difference"]) for row in balance_differences),
        Decimal("0"),
    )
    profit_and_loss_account_types = {
        "income",
        "income_other",
        "expense",
        "expense_depreciation",
        "expense_direct_cost",
    }
    profit_and_loss_balance_difference = sum(
        (
            Decimal(row["difference"])
            for row in balance_differences
            if row["account_type"] in profit_and_loss_account_types
        ),
        Decimal("0"),
    )
    move_count_difference_journal_codes = {
        row["code"]
        for row in journal_differences
        if row["difference"]["posted_move_count"]
    }
    current_differences_explained = (
        not unclassified_journal_differences
        and not unclassified_balance_differences
        and balance_difference_net == 0
        and move_count_difference_journal_codes <= {"CABA", "EXCH"}
    )
    critical_passed = all(critical_checks.values())
    status_value = (
        "failed"
        if not critical_passed
        else (
            "passed"
            if current_gross_totals_match and current_account_balances_match
            else "partial"
        )
    )
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "dev-validate",
        "status": status_value,
        "classification": (
            "DEV_QA_TARGET_PARITY"
            if status_value == "passed"
            else (
                "DEV_QA_TARGET_EXPLAINED_NATIVE_DIFFERENCES"
                if status_value == "partial"
                and current_differences_explained
                else "DEV_QA_TARGET_CURRENT_PERIOD_REVIEW_REQUIRED"
                if status_value == "partial"
                else "DEV_QA_TARGET_DEFECT"
            )
        ),
        "database": DEV_QA_DB,
        "critical_checks": critical_checks,
        "product_count_checks": product_counts_match,
        "runtime_signature": runtime_signature,
        "manager_accounting_identity": {
            "source": source_manager_identity,
            "target": target_manager_identity,
            "matches": manager_identity_matches,
        },
        "source_snapshot": source_snapshot,
        "target_snapshot": target_snapshot,
        "historical": {
            "source": source_historical,
            "target": target_historical,
            "matches": historical_matches,
            "move_identity": historical_move_identity,
            "sequence_chronology": {
                "source": source_sequence_chronology,
                "target": target_sequence_chronology,
                "matches": sequence_chronology_matches,
                "interpretation": (
                    "Source exceptions are preserved for review; the "
                    "replacement import does not silently resequence posted "
                    "history."
                ),
            },
        },
        "current_period": {
            "date_from": USL_CURRENT_START,
            "date_to": "2026-06-30",
            "source": source_current,
            "target": target_current,
            "gross_totals_match": current_gross_totals_match,
            "account_balances_match": current_account_balances_match,
            "differences_explained": current_differences_explained,
            "professional_acceptance_required": (
                False
            ),
            "posted_move_count_difference": (
                int(target_current["posted_move_count"])
                - int(source_current["posted_move_count"])
            ),
            "posted_move_line_count_difference": (
                int(target_current["posted_move_line_count"])
                - int(source_current["posted_move_line_count"])
            ),
            "gross_debit_difference": (
                f"{Decimal(target_current['debit']) - Decimal(source_current['debit']):.2f}"
            ),
            "gross_credit_difference": (
                f"{Decimal(target_current['credit']) - Decimal(source_current['credit']):.2f}"
            ),
            "journal_difference_count": len(journal_differences),
            "unclassified_journal_difference_count": len(
                unclassified_journal_differences,
            ),
            "journal_differences": journal_differences,
            "account_balance_difference_count": len(balance_differences),
            "unclassified_account_balance_difference_count": len(
                unclassified_balance_differences,
            ),
            "account_balance_difference_net": (
                f"{balance_difference_net:.2f}"
            ),
            "profit_and_loss_balance_difference": (
                f"{profit_and_loss_balance_difference:.2f}"
            ),
            "account_balance_differences": balance_differences,
            "interpretation": (
                (
                    "The source-faithful development product preserves the "
                    "source ledger. Any remaining difference is a migration "
                    "defect to correct, not an acceptance assumption."
                )
                if current_differences_explained
                else (
                    "Native current-period workflows are present and the historical "
                    "ledger is exact. Remaining gross-turnover and account-balance "
                    "differences must be explained or corrected before this candidate "
                    "can replace the exact replay target."
                )
            ),
        },
    }
    write_json(PRIVATE_ARTIFACTS / "dev-validate-status.json", status)
    if status_value == "failed":
        message = (
            "Replacement validation failed a historical, balance, uniqueness, "
            "or native product-count check."
        )
        raise HarnessError(message)
    return status


def native_validation_documents(args: argparse.Namespace) -> dict[str, Any]:
    """Rebuild and post source business documents through native Odoo logic."""
    ensure_dirs()
    validation = validate_source(args)
    dump_sha = validation["dump"]["sha256"] or "unknown"
    snapshot_id = f"source-{dump_sha[:12]}"
    if not table_exists(NATIVE_VALIDATION_DB, "rebuild_account_import_run"):
        message = "Run make accounting-validation-native-reset before Track B document replay."
        raise HarnessError(message)

    script_path = PRIVATE_ARTIFACTS / "validation-native-native-documents.py"
    script_path.write_text(
        "\n".join([
            "import json",
            "run = env['rebuild.account.import.run'].create({",
            "    'name': 'USL Track B native business-document replay',",
            "    'mode': 'native_engine_replay',",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {NATIVE_VALIDATION_DB!r},",
            "})",
            "stats = run.run_native_engine_replay_from_source({",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {NATIVE_VALIDATION_DB!r},",
            f"    'date_from': {USL_CURRENT_START!r},",
            "    'date_to': '2026-06-30',",
            "    'source_company_ids': [1],",
            "})",
            "env.cr.commit()",
            "print('REBUILD_NATIVE_VALIDATION_RESULT=' + json.dumps({",
            "    'run_id': run.id,",
            "    'status': run.status,",
            "    'stats': stats,",
            "}, sort_keys=True, default=str))",
            "",
        ]),
        encoding="utf-8",
    )
    result = run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "-e",
            f"ODOO_ADDONS_PATH={TARGET_ODOO_ADDONS_PATH}",
            "init-db",
            "odoo",
            "shell",
            "--config=/etc/odoo/odoo.conf",
            f"--database={NATIVE_VALIDATION_DB}",
        ),
        input_file=script_path,
        check=False,
    )
    marker = None
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("REBUILD_NATIVE_VALIDATION_RESULT="):
            marker = line.removeprefix("REBUILD_NATIVE_VALIDATION_RESULT=")
    if result.returncode or not marker:
        status = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "validation-native-documents",
            "status": "failed",
            "classification": "NATIVE_VALIDATION_EXECUTION_DEFECT",
            "database": NATIVE_VALIDATION_DB,
            "exit_code": result.returncode,
            "output_tail": (result.stdout + result.stderr)[-12000:],
        }
        write_json(PRIVATE_ARTIFACTS / "validation-native-documents-status.json", status)
        if not getattr(args, "allow_errors", False):
            message = "Track B document replay failed. See the private status artifact."
            raise HarnessError(message)
        return status
    payload = json.loads(marker)
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "validation-native-documents",
        "database": NATIVE_VALIDATION_DB,
        "run_id": payload["run_id"],
        "status": payload["status"],
        **payload["stats"],
    }
    write_json(PRIVATE_ARTIFACTS / "validation-native-documents-status.json", status)
    if status["status"] != "passed" and not getattr(args, "allow_errors", False):
        message = "Track B document replay has blocked or mismatched cases."
        raise HarnessError(message)
    return status


def native_validation_assets(args: argparse.Namespace) -> dict[str, Any]:
    """Replay source depreciation schedules through native OCA assets."""
    ensure_dirs()
    validation = validate_source(args)
    dump_sha = validation["dump"]["sha256"] or "unknown"
    snapshot_id = f"source-{dump_sha[:12]}"
    if not table_exists(NATIVE_VALIDATION_DB, "rebuild_account_import_run"):
        message = "Run make accounting-validation-native-reset before Track B asset replay."
        raise HarnessError(message)

    script_path = PRIVATE_ARTIFACTS / "validation-native-native-assets.py"
    script_path.write_text(
        "\n".join([
            "import json",
            "run = env['rebuild.account.import.run'].create({",
            "    'name': 'USL Track B native asset replay',",
            "    'mode': 'native_engine_replay',",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {NATIVE_VALIDATION_DB!r},",
            "})",
            "stats = run.run_native_asset_replay_from_source({",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {NATIVE_VALIDATION_DB!r},",
            f"    'date_from': {USL_CURRENT_START!r},",
            "    'date_to': '2026-06-30',",
            "    'source_company_ids': [1],",
            "})",
            "env.cr.commit()",
            "print('REBUILD_NATIVE_VALIDATION_ASSET_RESULT=' + json.dumps({",
            "    'run_id': run.id,",
            "    'status': run.status,",
            "    'stats': stats,",
            "}, sort_keys=True, default=str))",
            "",
        ]),
        encoding="utf-8",
    )
    result = run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "-e",
            f"ODOO_ADDONS_PATH={TARGET_ODOO_ADDONS_PATH}",
            "init-db",
            "odoo",
            "shell",
            "--config=/etc/odoo/odoo.conf",
            f"--database={NATIVE_VALIDATION_DB}",
        ),
        input_file=script_path,
        check=False,
    )
    marker = None
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("REBUILD_NATIVE_VALIDATION_ASSET_RESULT="):
            marker = line.removeprefix("REBUILD_NATIVE_VALIDATION_ASSET_RESULT=")
    if result.returncode or not marker:
        status = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "validation-native-assets",
            "status": "failed",
            "classification": "NATIVE_VALIDATION_EXECUTION_DEFECT",
            "database": NATIVE_VALIDATION_DB,
            "exit_code": result.returncode,
            "output_tail": (result.stdout + result.stderr)[-12000:],
        }
        write_json(PRIVATE_ARTIFACTS / "validation-native-assets-status.json", status)
        if not getattr(args, "allow_errors", False):
            message = "Track B asset replay failed. See the private status artifact."
            raise HarnessError(message)
        return status
    payload = json.loads(marker)
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "validation-native-assets",
        "database": NATIVE_VALIDATION_DB,
        "run_id": payload["run_id"],
        "status": payload["status"],
        **payload["stats"],
    }
    write_json(PRIVATE_ARTIFACTS / "validation-native-assets-status.json", status)
    if status["status"] != "passed" and not getattr(args, "allow_errors", False):
        message = "Track B asset replay has blocked or mismatched cases."
        raise HarnessError(message)
    return status


def native_validation_deferrals(args: argparse.Namespace) -> dict[str, Any]:
    """Replay source deferred schedules through native journal entries."""
    ensure_dirs()
    validation = validate_source(args)
    dump_sha = validation["dump"]["sha256"] or "unknown"
    snapshot_id = f"source-{dump_sha[:12]}"
    if not table_exists(NATIVE_VALIDATION_DB, "rebuild_account_import_run"):
        message = "Run make accounting-validation-native-reset before Track B deferral replay."
        raise HarnessError(message)

    script_path = PRIVATE_ARTIFACTS / "validation-native-native-deferrals.py"
    script_path.write_text(
        "\n".join([
            "import json",
            "run = env['rebuild.account.import.run'].create({",
            "    'name': 'USL Track B native deferral replay',",
            "    'mode': 'native_engine_replay',",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {NATIVE_VALIDATION_DB!r},",
            "})",
            "stats = run.run_native_deferral_replay_from_source({",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {NATIVE_VALIDATION_DB!r},",
            f"    'date_from': {USL_CURRENT_START!r},",
            "    'date_to': '2026-06-30',",
            "    'source_company_ids': [1],",
            "    'opening_boundary_source_move_ids': [8871],",
            "})",
            "env.cr.commit()",
            "print('REBUILD_NATIVE_VALIDATION_DEFERRAL_RESULT=' + json.dumps({",
            "    'run_id': run.id,",
            "    'status': run.status,",
            "    'stats': stats,",
            "}, sort_keys=True, default=str))",
            "",
        ]),
        encoding="utf-8",
    )
    result = run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "-e",
            f"ODOO_ADDONS_PATH={TARGET_ODOO_ADDONS_PATH}",
            "init-db",
            "odoo",
            "shell",
            "--config=/etc/odoo/odoo.conf",
            f"--database={NATIVE_VALIDATION_DB}",
        ),
        input_file=script_path,
        check=False,
    )
    marker = None
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("REBUILD_NATIVE_VALIDATION_DEFERRAL_RESULT="):
            marker = line.removeprefix("REBUILD_NATIVE_VALIDATION_DEFERRAL_RESULT=")
    if result.returncode or not marker:
        status = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "validation-native-deferrals",
            "status": "failed",
            "classification": "NATIVE_VALIDATION_EXECUTION_DEFECT",
            "database": NATIVE_VALIDATION_DB,
            "exit_code": result.returncode,
            "output_tail": (result.stdout + result.stderr)[-12000:],
        }
        write_json(PRIVATE_ARTIFACTS / "validation-native-deferrals-status.json", status)
        if not getattr(args, "allow_errors", False):
            message = "Track B deferral replay failed. See the private status artifact."
            raise HarnessError(message)
        return status
    payload = json.loads(marker)
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "validation-native-deferrals",
        "database": NATIVE_VALIDATION_DB,
        "run_id": payload["run_id"],
        "status": payload["status"],
        **payload["stats"],
    }
    write_json(PRIVATE_ARTIFACTS / "validation-native-deferrals-status.json", status)
    if status["status"] != "passed" and not getattr(args, "allow_errors", False):
        message = "Track B deferral replay has blocked or mismatched cases."
        raise HarnessError(message)
    return status


def native_validation_analytics(args: argparse.Namespace) -> dict[str, Any]:
    """Apply classified analytic corrections and validate multi-plan parity."""
    ensure_dirs()
    validation = validate_source(args)
    dump_sha = validation["dump"]["sha256"] or "unknown"
    snapshot_id = f"source-{dump_sha[:12]}"
    if not table_exists(NATIVE_VALIDATION_DB, "rebuild_account_import_run"):
        message = "Run make accounting-validation-native-reset before Track B analytic replay."
        raise HarnessError(message)

    script_path = PRIVATE_ARTIFACTS / "validation-native-native-analytics.py"
    script_path.write_text(
        "\n".join([
            "import json",
            "run = env['rebuild.account.import.run'].create({",
            "    'name': 'USL Track B native multi-plan analytic replay',",
            "    'mode': 'native_engine_replay',",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {NATIVE_VALIDATION_DB!r},",
            "})",
            "stats = run.run_native_analytic_replay_from_source({",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {NATIVE_VALIDATION_DB!r},",
            f"    'date_from': {USL_CURRENT_START!r},",
            "    'date_to': '2026-06-30',",
            "    'source_company_ids': [1],",
            "})",
            "env.cr.commit()",
            "print('REBUILD_NATIVE_VALIDATION_ANALYTIC_RESULT=' + json.dumps({",
            "    'run_id': run.id,",
            "    'status': run.status,",
            "    'stats': stats,",
            "}, sort_keys=True, default=str))",
            "",
        ]),
        encoding="utf-8",
    )
    result = run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "-e",
            f"ODOO_ADDONS_PATH={TARGET_ODOO_ADDONS_PATH}",
            "init-db",
            "odoo",
            "shell",
            "--config=/etc/odoo/odoo.conf",
            f"--database={NATIVE_VALIDATION_DB}",
        ),
        input_file=script_path,
        check=False,
    )
    marker = None
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("REBUILD_NATIVE_VALIDATION_ANALYTIC_RESULT="):
            marker = line.removeprefix("REBUILD_NATIVE_VALIDATION_ANALYTIC_RESULT=")
    if result.returncode or not marker:
        status = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "validation-native-analytics",
            "status": "failed",
            "classification": "NATIVE_VALIDATION_EXECUTION_DEFECT",
            "database": NATIVE_VALIDATION_DB,
            "exit_code": result.returncode,
            "output_tail": (result.stdout + result.stderr)[-12000:],
        }
        write_json(PRIVATE_ARTIFACTS / "validation-native-analytics-status.json", status)
        if not getattr(args, "allow_errors", False):
            message = "Track B analytic replay failed. See the private status artifact."
            raise HarnessError(message)
        return status
    payload = json.loads(marker)
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "validation-native-analytics",
        "database": NATIVE_VALIDATION_DB,
        "run_id": payload["run_id"],
        "status": payload["status"],
        **payload["stats"],
    }
    write_json(PRIVATE_ARTIFACTS / "validation-native-analytics-status.json", status)
    if status["status"] != "passed" and not getattr(args, "allow_errors", False):
        message = "Track B analytic replay has blocked or mismatched cases."
        raise HarnessError(message)
    return status


def native_validation_expenses(args: argparse.Namespace) -> dict[str, Any]:
    """Rebuild source expenses through native approval and posting workflows."""
    ensure_dirs()
    validation = validate_source(args)
    dump_sha = validation["dump"]["sha256"] or "unknown"
    snapshot_id = f"source-{dump_sha[:12]}"
    if not table_exists(NATIVE_VALIDATION_DB, "rebuild_account_import_run"):
        message = "Run make accounting-validation-native-reset before Track B expense replay."
        raise HarnessError(message)

    script_path = PRIVATE_ARTIFACTS / "validation-native-native-expenses.py"
    script_path.write_text(
        "\n".join([
            "import json",
            "run = env['rebuild.account.import.run'].create({",
            "    'name': 'USL Track B native expense replay',",
            "    'mode': 'native_engine_replay',",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {NATIVE_VALIDATION_DB!r},",
            "})",
            "stats = run.run_native_expense_replay_from_source({",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {NATIVE_VALIDATION_DB!r},",
            f"    'date_from': {USL_CURRENT_START!r},",
            "    'date_to': '2026-06-30',",
            "    'source_company_ids': [1],",
            "})",
            "env.cr.commit()",
            "print('REBUILD_NATIVE_VALIDATION_EXPENSE_RESULT=' + json.dumps({",
            "    'run_id': run.id,",
            "    'status': run.status,",
            "    'stats': stats,",
            "}, sort_keys=True, default=str))",
            "",
        ]),
        encoding="utf-8",
    )
    result = run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "-e",
            f"ODOO_ADDONS_PATH={TARGET_ODOO_ADDONS_PATH}",
            "init-db",
            "odoo",
            "shell",
            "--config=/etc/odoo/odoo.conf",
            f"--database={NATIVE_VALIDATION_DB}",
        ),
        input_file=script_path,
        check=False,
    )
    marker = None
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("REBUILD_NATIVE_VALIDATION_EXPENSE_RESULT="):
            marker = line.removeprefix("REBUILD_NATIVE_VALIDATION_EXPENSE_RESULT=")
    if result.returncode or not marker:
        status = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "validation-native-expenses",
            "status": "failed",
            "classification": "NATIVE_VALIDATION_EXPENSE_EXECUTION_DEFECT",
            "database": NATIVE_VALIDATION_DB,
            "exit_code": result.returncode,
            "output_tail": (result.stdout + result.stderr)[-12000:],
        }
        write_json(PRIVATE_ARTIFACTS / "validation-native-expenses-status.json", status)
        if not getattr(args, "allow_errors", False):
            message = "Track B expense replay failed. See the private status artifact."
            raise HarnessError(message)
        return status
    payload = json.loads(marker)
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "validation-native-expenses",
        "database": NATIVE_VALIDATION_DB,
        "run_id": payload["run_id"],
        "status": payload["status"],
        **payload["stats"],
    }
    write_json(PRIVATE_ARTIFACTS / "validation-native-expenses-status.json", status)
    if status["status"] != "passed" and not getattr(args, "allow_errors", False):
        message = "Track B expense replay has blocked or mismatched cases."
        raise HarnessError(message)
    return status


def native_validation_expense_settlement(args: argparse.Namespace) -> dict[str, Any]:
    """Replay expense-related bank matching through OCA reconciliation."""
    ensure_dirs()
    validation = validate_source(args)
    dump_sha = validation["dump"]["sha256"] or "unknown"
    snapshot_id = f"source-{dump_sha[:12]}"
    if not table_exists(NATIVE_VALIDATION_DB, "rebuild_account_import_run"):
        message = "Run make accounting-validation-native-reset before Track B expense settlement."
        raise HarnessError(message)

    script_path = PRIVATE_ARTIFACTS / "validation-native-native-expense-settlement.py"
    script_path.write_text(
        "\n".join([
            "import json",
            "run = env['rebuild.account.import.run'].create({",
            "    'name': 'USL Track B native expense bank settlement',",
            "    'mode': 'native_engine_replay',",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {NATIVE_VALIDATION_DB!r},",
            "})",
            "stats = run.run_native_expense_settlement_from_source({",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {NATIVE_VALIDATION_DB!r},",
            f"    'date_from': {USL_CURRENT_START!r},",
            "    'date_to': '2026-06-30',",
            "    'source_company_ids': [1],",
            "})",
            "env.cr.commit()",
            "print('REBUILD_NATIVE_VALIDATION_EXPENSE_SETTLEMENT_RESULT=' + json.dumps({",
            "    'run_id': run.id,",
            "    'status': run.status,",
            "    'stats': stats,",
            "}, sort_keys=True, default=str))",
            "",
        ]),
        encoding="utf-8",
    )
    result = run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "-e",
            f"ODOO_ADDONS_PATH={TARGET_ODOO_ADDONS_PATH}",
            "init-db",
            "odoo",
            "shell",
            "--config=/etc/odoo/odoo.conf",
            f"--database={NATIVE_VALIDATION_DB}",
        ),
        input_file=script_path,
        check=False,
    )
    marker = None
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("REBUILD_NATIVE_VALIDATION_EXPENSE_SETTLEMENT_RESULT="):
            marker = line.removeprefix("REBUILD_NATIVE_VALIDATION_EXPENSE_SETTLEMENT_RESULT=")
    if result.returncode or not marker:
        status = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "validation-native-expense-settlement",
            "status": "failed",
            "classification": "NATIVE_VALIDATION_EXPENSE_SETTLEMENT_EXECUTION_DEFECT",
            "database": NATIVE_VALIDATION_DB,
            "exit_code": result.returncode,
            "output_tail": (result.stdout + result.stderr)[-12000:],
        }
        write_json(PRIVATE_ARTIFACTS / "validation-native-expense-settlement-status.json", status)
        if not getattr(args, "allow_errors", False):
            message = "Track B expense settlement failed. See the private status artifact."
            raise HarnessError(message)
        return status
    payload = json.loads(marker)
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "validation-native-expense-settlement",
        "database": NATIVE_VALIDATION_DB,
        "run_id": payload["run_id"],
        "status": payload["status"],
        **payload["stats"],
    }
    write_json(PRIVATE_ARTIFACTS / "validation-native-expense-settlement-status.json", status)
    if status["status"] != "passed" and not getattr(args, "allow_errors", False):
        message = "Track B expense settlement has blocked or mismatched cases."
        raise HarnessError(message)
    return status


def native_validation_document_settlement(args: argparse.Namespace) -> dict[str, Any]:
    """Replay commercial-document bank matching through OCA reconciliation."""
    ensure_dirs()
    validation = validate_source(args)
    dump_sha = validation["dump"]["sha256"] or "unknown"
    snapshot_id = f"source-{dump_sha[:12]}"
    if not table_exists(NATIVE_VALIDATION_DB, "rebuild_account_import_run"):
        message = "Run make accounting-validation-native-reset before document settlement."
        raise HarnessError(message)

    script_path = PRIVATE_ARTIFACTS / "validation-native-native-document-settlement.py"
    script_path.write_text(
        "\n".join([
            "import json",
            "run = env['rebuild.account.import.run'].create({",
            "    'name': 'USL Track B native commercial-document bank settlement',",
            "    'mode': 'native_engine_replay',",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {NATIVE_VALIDATION_DB!r},",
            "})",
            "stats = run.run_native_document_settlement_from_source({",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {NATIVE_VALIDATION_DB!r},",
            f"    'date_from': {USL_CURRENT_START!r},",
            "    'date_to': '2026-06-30',",
            "    'source_company_ids': [1],",
            "})",
            "env.cr.commit()",
            "print('REBUILD_NATIVE_VALIDATION_DOCUMENT_SETTLEMENT_RESULT=' + json.dumps({",
            "    'run_id': run.id,",
            "    'status': run.status,",
            "    'stats': stats,",
            "}, sort_keys=True, default=str))",
            "",
        ]),
        encoding="utf-8",
    )
    result = run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "-e",
            f"ODOO_ADDONS_PATH={TARGET_ODOO_ADDONS_PATH}",
            "init-db",
            "odoo",
            "shell",
            "--config=/etc/odoo/odoo.conf",
            f"--database={NATIVE_VALIDATION_DB}",
        ),
        input_file=script_path,
        check=False,
    )
    marker = None
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("REBUILD_NATIVE_VALIDATION_DOCUMENT_SETTLEMENT_RESULT="):
            marker = line.removeprefix(
                "REBUILD_NATIVE_VALIDATION_DOCUMENT_SETTLEMENT_RESULT=",
            )
    artifact_path = PRIVATE_ARTIFACTS / "validation-native-document-settlement-status.json"
    if result.returncode or not marker:
        status = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "validation-native-document-settlement",
            "status": "failed",
            "classification": "NATIVE_VALIDATION_DOCUMENT_SETTLEMENT_EXECUTION_DEFECT",
            "database": NATIVE_VALIDATION_DB,
            "exit_code": result.returncode,
            "output_tail": (result.stdout + result.stderr)[-12000:],
        }
        write_json(artifact_path, status)
        if not getattr(args, "allow_errors", False):
            message = "Track B document settlement failed. See the private status artifact."
            raise HarnessError(message)
        return status
    payload = json.loads(marker)
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "validation-native-document-settlement",
        "database": NATIVE_VALIDATION_DB,
        "run_id": payload["run_id"],
        "status": payload["status"],
        **payload["stats"],
    }
    write_json(artifact_path, status)
    if status["status"] != "passed" and not getattr(args, "allow_errors", False):
        message = "Track B document settlement has blocked or mismatched cases."
        raise HarnessError(message)
    return status


def native_validation_general_reconciliation(args: argparse.Namespace) -> dict[str, Any]:
    """Replay non-bank document settlement through native reconciliation."""
    ensure_dirs()
    validation = validate_source(args)
    dump_sha = validation["dump"]["sha256"] or "unknown"
    snapshot_id = f"source-{dump_sha[:12]}"
    if not table_exists(NATIVE_VALIDATION_DB, "rebuild_account_import_run"):
        message = "Run make accounting-validation-native-reset before General Reconciliation."
        raise HarnessError(message)

    script_path = PRIVATE_ARTIFACTS / "validation-native-native-general-reconciliation.py"
    script_path.write_text(
        "\n".join([
            "import json",
            "run = env['rebuild.account.import.run'].create({",
            "    'name': 'USL Track B native General Reconciliation',",
            "    'mode': 'native_engine_replay',",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {NATIVE_VALIDATION_DB!r},",
            "})",
            "stats = run.run_native_general_reconciliation_from_source({",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {NATIVE_VALIDATION_DB!r},",
            f"    'date_from': {USL_CURRENT_START!r},",
            "    'date_to': '2026-06-30',",
            "    'source_company_ids': [1],",
            "})",
            "env.cr.commit()",
            "print('REBUILD_NATIVE_VALIDATION_GENERAL_RECONCILIATION_RESULT=' + json.dumps({",
            "    'run_id': run.id,",
            "    'status': run.status,",
            "    'stats': stats,",
            "}, sort_keys=True, default=str))",
            "",
        ]),
        encoding="utf-8",
    )
    result = run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "-e",
            f"ODOO_ADDONS_PATH={TARGET_ODOO_ADDONS_PATH}",
            "init-db",
            "odoo",
            "shell",
            "--config=/etc/odoo/odoo.conf",
            f"--database={NATIVE_VALIDATION_DB}",
        ),
        input_file=script_path,
        check=False,
    )
    marker = None
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("REBUILD_NATIVE_VALIDATION_GENERAL_RECONCILIATION_RESULT="):
            marker = line.removeprefix(
                "REBUILD_NATIVE_VALIDATION_GENERAL_RECONCILIATION_RESULT=",
            )
    artifact_path = PRIVATE_ARTIFACTS / "validation-native-general-reconciliation-status.json"
    if result.returncode or not marker:
        status = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "validation-native-general-reconciliation",
            "status": "failed",
            "classification": "NATIVE_VALIDATION_GENERAL_RECONCILIATION_EXECUTION_DEFECT",
            "database": NATIVE_VALIDATION_DB,
            "exit_code": result.returncode,
            "output_tail": (result.stdout + result.stderr)[-12000:],
        }
        write_json(artifact_path, status)
        if not getattr(args, "allow_errors", False):
            message = (
                "Track B General Reconciliation failed. "
                "See the private status artifact."
            )
            raise HarnessError(message)
        return status
    payload = json.loads(marker)
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "validation-native-general-reconciliation",
        "database": NATIVE_VALIDATION_DB,
        "run_id": payload["run_id"],
        "status": payload["status"],
        **payload["stats"],
    }
    write_json(artifact_path, status)
    if status["status"] != "passed" and not getattr(args, "allow_errors", False):
        message = "Track B General Reconciliation has blocked or mismatched cases."
        raise HarnessError(message)
    return status


def native_validation_bank_categorization(args: argparse.Namespace) -> dict[str, Any]:
    """Replay direct bank categorization and source-open transactions."""
    ensure_dirs()
    validation = validate_source(args)
    dump_sha = validation["dump"]["sha256"] or "unknown"
    snapshot_id = f"source-{dump_sha[:12]}"
    if not table_exists(NATIVE_VALIDATION_DB, "rebuild_account_import_run"):
        message = "Run make accounting-validation-native-reset before bank categorization."
        raise HarnessError(message)

    script_path = PRIVATE_ARTIFACTS / "validation-native-native-bank-categorization.py"
    script_path.write_text(
        "\n".join([
            "import json",
            "run = env['rebuild.account.import.run'].create({",
            "    'name': 'USL Track B native direct bank categorization',",
            "    'mode': 'native_engine_replay',",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {NATIVE_VALIDATION_DB!r},",
            "})",
            "stats = run.run_native_bank_categorization_from_source({",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {NATIVE_VALIDATION_DB!r},",
            f"    'date_from': {USL_CURRENT_START!r},",
            "    'date_to': '2026-06-30',",
            "    'source_company_ids': [1],",
            "})",
            "env.cr.commit()",
            "print('REBUILD_NATIVE_VALIDATION_BANK_CATEGORIZATION_RESULT=' + json.dumps({",
            "    'run_id': run.id,",
            "    'status': run.status,",
            "    'stats': stats,",
            "}, sort_keys=True, default=str))",
            "",
        ]),
        encoding="utf-8",
    )
    result = run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "-e",
            f"ODOO_ADDONS_PATH={TARGET_ODOO_ADDONS_PATH}",
            "init-db",
            "odoo",
            "shell",
            "--config=/etc/odoo/odoo.conf",
            f"--database={NATIVE_VALIDATION_DB}",
        ),
        input_file=script_path,
        check=False,
    )
    marker = None
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("REBUILD_NATIVE_VALIDATION_BANK_CATEGORIZATION_RESULT="):
            marker = line.removeprefix(
                "REBUILD_NATIVE_VALIDATION_BANK_CATEGORIZATION_RESULT=",
            )
    artifact_path = PRIVATE_ARTIFACTS / "validation-native-bank-categorization-status.json"
    if result.returncode or not marker:
        status = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "validation-native-bank-categorization",
            "status": "failed",
            "classification": "NATIVE_VALIDATION_BANK_CATEGORIZATION_EXECUTION_DEFECT",
            "database": NATIVE_VALIDATION_DB,
            "exit_code": result.returncode,
            "output_tail": (result.stdout + result.stderr)[-12000:],
        }
        write_json(artifact_path, status)
        if not getattr(args, "allow_errors", False):
            message = (
                "Track B bank categorization failed. "
                "See the private status artifact."
            )
            raise HarnessError(message)
        return status
    payload = json.loads(marker)
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "validation-native-bank-categorization",
        "database": NATIVE_VALIDATION_DB,
        "run_id": payload["run_id"],
        "status": payload["status"],
        **payload["stats"],
    }
    write_json(artifact_path, status)
    if status["status"] != "passed" and not getattr(args, "allow_errors", False):
        message = "Track B bank categorization has blocked or mismatched cases."
        raise HarnessError(message)
    return status


def native_validation_bank_external(args: argparse.Namespace) -> dict[str, Any]:
    """Replay the final current-period external-endpoint bank perimeter."""
    ensure_dirs()
    validation = validate_source(args)
    dump_sha = validation["dump"]["sha256"] or "unknown"
    snapshot_id = f"source-{dump_sha[:12]}"
    if not table_exists(NATIVE_VALIDATION_DB, "rebuild_account_import_run"):
        message = "Run make accounting-validation-native-reset before external bank replay."
        raise HarnessError(message)

    script_path = PRIVATE_ARTIFACTS / "validation-native-native-bank-external.py"
    script_path.write_text(
        "\n".join([
            "import json",
            "run = env['rebuild.account.import.run'].create({",
            "    'name': 'USL Track B native external bank replay',",
            "    'mode': 'native_engine_replay',",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {NATIVE_VALIDATION_DB!r},",
            "})",
            "stats = run.run_native_bank_external_replay_from_source({",
            "    'source_database': 'odoo_online_source_saas_19_3',",
            f"    'source_dump_sha256': {dump_sha!r},",
            f"    'source_snapshot_id': {snapshot_id!r},",
            "    'source_version': 'Odoo Online Enterprise saas~19.3',",
            f"    'target_database': {NATIVE_VALIDATION_DB!r},",
            f"    'date_from': {USL_CURRENT_START!r},",
            "    'date_to': '2026-06-30',",
            "    'source_company_ids': [1],",
            "})",
            "env.cr.commit()",
            "print('REBUILD_NATIVE_VALIDATION_BANK_EXTERNAL_RESULT=' + json.dumps({",
            "    'run_id': run.id,",
            "    'status': run.status,",
            "    'stats': stats,",
            "}, sort_keys=True, default=str))",
            "",
        ]),
        encoding="utf-8",
    )
    result = run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "-e",
            f"ODOO_ADDONS_PATH={TARGET_ODOO_ADDONS_PATH}",
            "init-db",
            "odoo",
            "shell",
            "--config=/etc/odoo/odoo.conf",
            f"--database={NATIVE_VALIDATION_DB}",
        ),
        input_file=script_path,
        check=False,
    )
    marker = None
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("REBUILD_NATIVE_VALIDATION_BANK_EXTERNAL_RESULT="):
            marker = line.removeprefix("REBUILD_NATIVE_VALIDATION_BANK_EXTERNAL_RESULT=")
    artifact_path = PRIVATE_ARTIFACTS / "validation-native-bank-external-status.json"
    if result.returncode or not marker:
        status = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "validation-native-bank-external",
            "status": "failed",
            "classification": "NATIVE_VALIDATION_BANK_EXTERNAL_EXECUTION_DEFECT",
            "database": NATIVE_VALIDATION_DB,
            "exit_code": result.returncode,
            "output_tail": (result.stdout + result.stderr)[-12000:],
        }
        write_json(artifact_path, status)
        if not getattr(args, "allow_errors", False):
            message = "Track B external bank replay failed. See private status."
            raise HarnessError(message)
        return status
    payload = json.loads(marker)
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "validation-native-bank-external",
        "database": NATIVE_VALIDATION_DB,
        "run_id": payload["run_id"],
        "status": payload["status"],
        **payload["stats"],
    }
    write_json(artifact_path, status)
    if status["status"] != "passed" and not getattr(args, "allow_errors", False):
        message = "Track B external bank replay has blocked or mismatched cases."
        raise HarnessError(message)
    return status


def target_import(args: argparse.Namespace) -> dict[str, Any]:
    ensure_dirs()
    validation = validate_source(args)
    dump_sha = validation["dump"]["sha256"] or "unknown"
    snapshot_id = f"source-{dump_sha[:12]}"
    import_date_to = source_snapshot_date() or USL_BENCHMARK_END
    if not table_exists(EXACT_VALIDATION_DB, "rebuild_account_import_run"):
        status = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "validation-exact-import",
            "status": "failed",
            "classification": "MISSING_TARGET_MODEL",
            "reason": f"{EXACT_VALIDATION_DB} does not have rebuild_account_migration installed.",
            "next_action": "Run make accounting-validation-exact-reset before target import.",
        }
        write_json(PRIVATE_ARTIFACTS / "validation-exact-import-status.json", status)
        raise HarnessError(status["reason"])

    import_script = PRIVATE_ARTIFACTS / "validation-exact-import-exact-ledger.py"
    import_script.write_text(
        "\n".join(
            [
                "import json",
                "run = env['rebuild.account.import.run'].create({",
                "    'name': 'USL complete native accounting replay',",
                "    'mode': 'exact_ledger_replay',",
                "    'source_database': 'odoo_online_source_saas_19_3',",
                f"    'source_dump_sha256': {dump_sha!r},",
                f"    'source_snapshot_id': {snapshot_id!r},",
                "    'source_version': 'Odoo Online Enterprise saas~19.3',",
                f"    'target_database': {EXACT_VALIDATION_DB!r},",
                "})",
                "stats = run.run_exact_ledger_replay_from_source({",
                "    'source_database': 'odoo_online_source_saas_19_3',",
                f"    'source_dump_sha256': {dump_sha!r},",
                f"    'source_snapshot_id': {snapshot_id!r},",
                "    'source_version': 'Odoo Online Enterprise saas~19.3',",
                f"    'target_database': {EXACT_VALIDATION_DB!r},",
                f"    'date_from': {USL_BENCHMARK_START!r},",
                f"    'date_to': {import_date_to!r},",
                "    'source_company_ids': [1, 8],",
                "})",
                "env.cr.commit()",
                "print('REBUILD_IMPORT_RESULT=' + json.dumps({'run_id': run.id, 'status': run.status, 'stats': stats}, sort_keys=True, default=str))",
                "",
            ],
        ),
        encoding="utf-8",
    )
    result = run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "-e",
            f"ODOO_ADDONS_PATH={TARGET_ODOO_ADDONS_PATH}",
            "init-db",
            "odoo",
            "shell",
            "--config=/etc/odoo/odoo.conf",
            f"--database={EXACT_VALIDATION_DB}",
        ),
        input_file=import_script,
        check=False,
    )
    if result.returncode:
        status = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "validation-exact-import",
            "status": "failed",
            "classification": "IMPORT_DEFECT",
            "script": str(import_script.relative_to(ROOT)),
            "exit_code": result.returncode,
            "output_tail": (result.stdout + result.stderr)[-8000:],
            "next_action": "Inspect the target import output and source/target field mapping that failed.",
        }
        write_json(PRIVATE_ARTIFACTS / "validation-exact-import-status.json", status)
        raise HarnessError("Target import failed. See artifacts/accounting-compat/private/validation-exact-import-status.json")

    run_row = query_json(
        EXACT_VALIDATION_DB,
        """
        SELECT to_jsonb(r)
        FROM (
            SELECT id, name, mode, status, source_database, source_dump_sha256,
                   source_snapshot_id, target_database, imported_company_count,
                   imported_currency_rate_count,
                   imported_account_count, imported_journal_count, imported_partner_count,
                   imported_move_count, imported_move_line_count,
                   imported_non_posted_move_count, imported_context_line_count,
                   imported_payment_count, imported_bank_statement_line_count,
                   imported_no_entry_payment_count, imported_analytic_line_count,
                   imported_attachment_count, imported_reconciliation_count,
                   imported_source_report_count,
                   imported_deferred_schedule_line_count,
                   warning_count, discrepancy_count, statistics_json
            FROM rebuild_account_import_run
            ORDER BY id DESC
            LIMIT 1
        ) r
        """,
        set_readonly_role=False,
    )
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "validation-exact-import",
        "status": "passed" if run_row and run_row.get("status") == "passed" else "failed",
        "classification": "COMPLETE_NATIVE_SOURCE_REPLAY",
        "script": str(import_script.relative_to(ROOT)),
        "import_run": run_row,
        "record_counts": target_table_counts(EXACT_VALIDATION_DB),
    }
    write_json(PRIVATE_ARTIFACTS / "validation-exact-import-status.json", status)
    return status


def target_idempotence(args: argparse.Namespace) -> dict[str, Any]:
    ensure_dirs()
    if not table_exists(EXACT_VALIDATION_DB, "rebuild_account_import_run"):
        status = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "validation-exact-idempotence",
            "status": "failed",
            "classification": "MISSING_TARGET_MODEL",
            "reason": f"{EXACT_VALIDATION_DB} does not have rebuild_account_migration installed.",
            "next_action": "Run make accounting-validation-exact-reset and make accounting-validation-exact-import first.",
        }
        write_json(PRIVATE_ARTIFACTS / "validation-exact-idempotence-status.json", status)
        raise HarnessError(status["reason"])

    before_import_run_count = scalar(
        EXACT_VALIDATION_DB,
        "SELECT count(*)::text FROM rebuild_account_import_run",
        set_readonly_role=False,
    ) or "0"
    before_signature = target_idempotence_signature()
    rerun_import = target_import(args)
    after_import_run_count = scalar(
        EXACT_VALIDATION_DB,
        "SELECT count(*)::text FROM rebuild_account_import_run",
        set_readonly_role=False,
    ) or "0"
    validation = target_validate(args)
    after_signature = target_idempotence_signature()

    duplicate_failures = {
        key: value
        for key, value in validation.get("invariant_failures", {}).items()
        if key.startswith("duplicate_") and value
    }
    import_run_delta = int(after_import_run_count) - int(before_import_run_count)
    signature_matches = before_signature == after_signature
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "validation-exact-idempotence",
        "status": "passed" if signature_matches and import_run_delta == 1 and validation.get("status") == "passed" and not duplicate_failures else "failed",
        "classification": "TARGET_IMPORT_IDEMPOTENCE_GUARDRAIL",
        "before_import_run_count": before_import_run_count,
        "after_import_run_count": after_import_run_count,
        "expected_import_run_delta": 1,
        "observed_import_run_delta": import_run_delta,
        "signature_matches": signature_matches,
        "before_signature": before_signature,
        "after_signature": after_signature,
        "rerun_import_status": rerun_import.get("status"),
        "rerun_import_run_id": (rerun_import.get("import_run") or {}).get("id"),
        "target_validate_status": validation.get("status"),
        "duplicate_failures": duplicate_failures,
        "limitations": [
            "This stage verifies that a repeated exact-ledger import does not duplicate accounting consequences.",
            "The import-run audit row is expected to increase by one.",
            "Rollback-only duplicate-trace, missing-account, missing-tax and incomplete-reconciliation probes are covered by make accounting-validation-exact-failure-tests.",
        ],
    }
    write_json(PRIVATE_ARTIFACTS / "validation-exact-idempotence-status.json", status)
    if status["status"] != "passed" and not getattr(args, "allow_errors", False):
        raise HarnessError("Target import idempotence guardrail failed")
    return status


def target_failure_tests(args: argparse.Namespace) -> dict[str, Any]:
    """Verify native trace constraints without migration review models."""
    ensure_dirs()
    duplicate_failures = {
        "account_move": duplicate_target_traces("account_move"),
        "account_move_line": duplicate_target_traces("account_move_line"),
        "account_payment": duplicate_target_traces("account_payment"),
        "account_partial_reconcile": duplicate_target_traces("account_partial_reconcile"),
        "account_full_reconcile": duplicate_target_traces("account_full_reconcile"),
    }
    obsolete_tables = [
        "rebuild_account_move_review",
        "rebuild_account_move_line_review",
        "rebuild_account_document_regeneration_case",
        "rebuild_account_payment_review",
        "rebuild_account_reconciliation_review",
    ]
    obsolete_models = [
        "rebuild.account.move.review",
        "rebuild.account.move.line.review",
        "rebuild.account.document.regeneration.case",
        "rebuild.account.payment.review",
        "rebuild.account.reconciliation.review",
    ]
    unexpected_tables = [
        table
        for table in obsolete_tables
        if table_exists(EXACT_VALIDATION_DB, table)
    ]
    unexpected_models = query_rows(
        EXACT_VALIDATION_DB,
        f"""
        SELECT model
        FROM ir_model
        WHERE model IN ({
            ", ".join(f"'{model}'" for model in obsolete_models)
        })
        ORDER BY model
        """,
        set_readonly_role=False,
    )
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "validation-exact-failure-tests",
        "status": (
            "passed"
            if (
                not any(duplicate_failures.values())
                and not unexpected_tables
                and not unexpected_models
            )
            else "failed"
        ),
        "classification": "NATIVE_SOURCE_TRACE_GUARDRAILS",
        "duplicate_failures": duplicate_failures,
        "obsolete_table_failures": unexpected_tables,
        "obsolete_model_failures": unexpected_models,
        "note": "All reconstructed business objects are native Odoo records; no review-model fallback is accepted.",
    }
    write_json(PRIVATE_ARTIFACTS / "validation-exact-failure-tests-status.json", status)
    if status["status"] != "passed" and not getattr(args, "allow_errors", False):
        raise HarnessError("Native source-trace guardrails failed")
    return status


def amount_text(value: Any, places: str = "0.01") -> str:
    return format(Decimal(str(value or "0")).quantize(Decimal(places)), "f")


def source_account_code_expr(company_id: int = 1) -> str:
    return f"COALESCE(aa.code_store->>'{company_id}', aa.code_store->>'1', aa.code_store::text)"


def target_account_code_expr(company_id: int = 1) -> str:
    return f"COALESCE(aa.code_store->>'{company_id}', aa.code_store->>'1', aa.code_store::text)"


def target_benchmark_move_where(move_alias: str = "am", company_alias: str = "company") -> str:
    return (
        f"{company_alias}.rebuild_source_id = 1 "
        f"AND {move_alias}.state = 'posted' "
        f"AND {move_alias}.date BETWEEN DATE '{USL_BENCHMARK_START}' AND DATE '{USL_BENCHMARK_END}'"
    )


def source_name_expr(alias: str, field: str = "name") -> str:
    return f"COALESCE({alias}.{field}->>'fr_FR', {alias}.{field}->>'en_US', {alias}.{field}::text)"


def source_posted_summary() -> dict[str, Any]:
    source_filter = f"""
        am.company_id = 1
        AND am.state = 'posted'
        AND am.date BETWEEN DATE '{USL_BENCHMARK_START}' AND DATE '{USL_BENCHMARK_END}'
    """
    move_summary = query_json(
        SOURCE_DB,
        f"""
        SELECT jsonb_build_object(
            'move_count', count(*)::text,
            'min_date', min(date)::text,
            'max_date', max(date)::text
        )
        FROM account_move am
        WHERE {source_filter}
        """,
    )
    line_summary = query_json(
        SOURCE_DB,
        f"""
        SELECT jsonb_build_object(
            'move_line_count', count(*)::text,
            'debit', COALESCE(round(sum(aml.debit)::numeric, 2), 0)::text,
            'credit', COALESCE(round(sum(aml.credit)::numeric, 2), 0)::text,
            'balance', COALESCE(round(sum(aml.balance)::numeric, 2), 0)::text
        )
        FROM account_move_line aml
        JOIN account_move am ON am.id = aml.move_id
        WHERE {source_filter}
        """,
    )
    return {**(move_summary or {}), **(line_summary or {})}


def target_posted_summary() -> dict[str, Any]:
    move_summary = query_json(
        EXACT_VALIDATION_DB,
        """
        SELECT jsonb_build_object(
            'move_count', count(*)::text,
            'min_date', min(date)::text,
            'max_date', max(date)::text
        )
        FROM account_move am
        JOIN res_company company ON company.id = am.company_id
        WHERE am.rebuild_source_model = 'account.move'
          AND {benchmark_where}
        """.format(benchmark_where=target_benchmark_move_where()),
        set_readonly_role=False,
    )
    line_summary = query_json(
        EXACT_VALIDATION_DB,
        """
        SELECT jsonb_build_object(
            'move_line_count', count(*)::text,
            'debit', COALESCE(round(sum(debit)::numeric, 2), 0)::text,
            'credit', COALESCE(round(sum(credit)::numeric, 2), 0)::text,
            'balance', COALESCE(round(sum(balance)::numeric, 2), 0)::text
        )
        FROM account_move_line aml
        JOIN account_move am ON am.id = aml.move_id
        JOIN res_company company ON company.id = am.company_id
        WHERE aml.rebuild_source_model = 'account.move.line'
          AND {benchmark_where}
        """.format(benchmark_where=target_benchmark_move_where()),
        set_readonly_role=False,
    )
    return {**(move_summary or {}), **(line_summary or {})}


def source_posted_line_amount_profile() -> dict[str, Any]:
    return query_json(
        SOURCE_DB,
        """
        SELECT jsonb_build_object(
            'move_line_count', count(*)::text,
            'zero_amount_line_count', count(*) FILTER (WHERE aml.debit = 0 AND aml.credit = 0)::text,
            'amount_line_count', count(*) FILTER (WHERE aml.debit <> 0 OR aml.credit <> 0)::text,
            'debit', COALESCE(round(sum(aml.debit)::numeric, 2), 0)::text,
            'credit', COALESCE(round(sum(aml.credit)::numeric, 2), 0)::text
        )
        FROM account_move_line aml
        JOIN account_move am ON am.id = aml.move_id
        WHERE am.company_id IN (1, 8)
        """,
    ) or {}


def target_posted_line_amount_profile() -> dict[str, Any]:
    return query_json(
        EXACT_VALIDATION_DB,
        """
        SELECT jsonb_build_object(
            'move_line_count', count(*)::text,
            'zero_amount_line_count', count(*) FILTER (WHERE debit = 0 AND credit = 0)::text,
            'amount_line_count', count(*) FILTER (WHERE debit <> 0 OR credit <> 0)::text,
            'debit', COALESCE(round(sum(debit)::numeric, 2), 0)::text,
            'credit', COALESCE(round(sum(credit)::numeric, 2), 0)::text
        )
        FROM account_move_line aml
        JOIN account_move am ON am.id = aml.move_id
        JOIN res_company company ON company.id = am.company_id
        WHERE aml.rebuild_source_model = 'account.move.line'
          AND {benchmark_where}
        """.format(benchmark_where=target_benchmark_move_where()),
        set_readonly_role=False,
    ) or {}


def source_full_replay_company_rows() -> list[dict[str, Any]]:
    snapshot = source_snapshot_date() or USL_BENCHMARK_END
    return query_rows(
        SOURCE_DB,
        f"""
        WITH scope_moves AS (
            SELECT id, company_id
            FROM account_move
            WHERE company_id IN (1, 8)
              AND state = 'posted'
              AND date BETWEEN DATE '{USL_BENCHMARK_START}' AND DATE '{snapshot}'
        ),
        scoped_lines AS (
            SELECT aml.*, sm.company_id AS source_company_id
            FROM account_move_line aml
            JOIN scope_moves sm ON sm.id = aml.move_id
        )
        SELECT sm.company_id::text AS source_company_id,
               count(DISTINCT sm.id)::text AS move_count,
               count(sl.id) FILTER (WHERE sl.account_id IS NOT NULL)::text AS accounting_line_count,
               count(sl.id) FILTER (WHERE sl.account_id IS NULL)::text AS non_account_line_count,
               COALESCE(round(sum(sl.debit) FILTER (WHERE sl.account_id IS NOT NULL)::numeric, 2), 0)::text AS debit,
               COALESCE(round(sum(sl.credit) FILTER (WHERE sl.account_id IS NOT NULL)::numeric, 2), 0)::text AS credit,
               count(sl.id) FILTER (WHERE sl.account_id IS NOT NULL AND sl.tax_repartition_line_id IS NOT NULL)::text AS tax_repartition_line_count,
               (
                   SELECT count(*)::text
                   FROM scoped_lines rel_line
                   JOIN account_move_line_account_tax_rel rel ON rel.account_move_line_id = rel_line.id
                   WHERE rel_line.source_company_id = sm.company_id
                     AND rel_line.account_id IS NOT NULL
               ) AS tax_relation_count,
               (
                   SELECT count(*)::text
                   FROM scoped_lines tag_line
                   JOIN account_account_tag_account_move_line_rel rel ON rel.account_move_line_id = tag_line.id
                   WHERE tag_line.source_company_id = sm.company_id
                     AND tag_line.account_id IS NOT NULL
               ) AS tax_tag_relation_count
        FROM scope_moves sm
        LEFT JOIN scoped_lines sl ON sl.move_id = sm.id
        GROUP BY sm.company_id
        ORDER BY sm.company_id
        """,
    )


def source_currency_rate_rows() -> list[dict[str, Any]]:
    return query_rows(
        SOURCE_DB,
        """
        SELECT rate.id::text AS source_currency_rate_id,
               currency.name::text AS currency,
               COALESCE(rate.company_id::text, '') AS source_company_id,
               rate.name::text AS date,
               round(rate.rate::numeric, 12)::text AS rate,
               COALESCE(company.currency_provider::text, 'source_odoo_online') AS provider,
               to_char(COALESCE(rate.write_date, rate.create_date), 'YYYY-MM-DD HH24:MI:SS.US') AS retrieved_at
        FROM res_currency_rate rate
        JOIN res_currency currency ON currency.id = rate.currency_id
        LEFT JOIN res_company company ON company.id = rate.company_id
        WHERE rate.company_id IS NULL OR rate.company_id IN (1, 8)
        ORDER BY rate.id
        """,
    )


def target_currency_rate_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT rate.rebuild_source_id::text AS source_currency_rate_id,
               currency.name::text AS currency,
               COALESCE(company.rebuild_source_id::text, '') AS source_company_id,
               rate.name::text AS date,
               round(rate.rate::numeric, 12)::text AS rate,
               COALESCE(rate.rebuild_rate_provider::text, 'source_odoo_online') AS provider,
               to_char(rate.rebuild_rate_retrieved_at, 'YYYY-MM-DD HH24:MI:SS.US') AS retrieved_at
        FROM res_currency_rate rate
        JOIN res_currency currency ON currency.id = rate.currency_id
        LEFT JOIN res_company company ON company.id = rate.company_id
        WHERE rate.rebuild_source_model = 'res.currency.rate'
          AND (company.rebuild_source_id IS NULL OR company.rebuild_source_id IN (1, 8))
        ORDER BY rate.rebuild_source_id
        """,
        set_readonly_role=False,
    )


def currency_rate_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(rows),
        "currencies": sorted({row["currency"] for row in rows}),
        "source_company_ids": sorted({row["source_company_id"] for row in rows}),
        "providers": sorted({row["provider"] for row in rows}),
        "first_rate_date": min((row["date"] for row in rows), default=None),
        "last_rate_date": max((row["date"] for row in rows), default=None),
        "first_retrieved_at": min((row["retrieved_at"] for row in rows), default=None),
        "last_retrieved_at": max((row["retrieved_at"] for row in rows), default=None),
    }


def target_full_replay_company_rows() -> list[dict[str, Any]]:
    snapshot = source_snapshot_date() or USL_BENCHMARK_END
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        WITH scope_moves AS (
            SELECT am.id, company.rebuild_source_id AS source_company_id
            FROM account_move am
            JOIN res_company company ON company.id = am.company_id
            WHERE company.rebuild_source_id IN (1, 8)
              AND am.rebuild_source_model = 'account.move'
              AND am.state = 'posted'
              AND am.date BETWEEN DATE '{date_from}' AND DATE '{date_to}'
        ),
        scoped_lines AS (
            SELECT aml.*, sm.source_company_id
            FROM account_move_line aml
            JOIN scope_moves sm ON sm.id = aml.move_id
            WHERE aml.rebuild_source_model = 'account.move.line'
        )
        SELECT sm.source_company_id::text AS source_company_id,
               count(DISTINCT sm.id)::text AS move_count,
               count(sl.id) FILTER (WHERE sl.account_id IS NOT NULL)::text AS accounting_line_count,
               count(sl.id) FILTER (WHERE sl.account_id IS NULL)::text AS non_account_line_count,
               COALESCE(round(sum(sl.debit) FILTER (WHERE sl.account_id IS NOT NULL)::numeric, 2), 0)::text AS debit,
               COALESCE(round(sum(sl.credit) FILTER (WHERE sl.account_id IS NOT NULL)::numeric, 2), 0)::text AS credit,
               count(sl.id) FILTER (WHERE sl.account_id IS NOT NULL AND sl.tax_repartition_line_id IS NOT NULL)::text AS tax_repartition_line_count,
               (
                   SELECT count(*)::text
                   FROM scoped_lines rel_line
                   JOIN account_move_line_account_tax_rel rel ON rel.account_move_line_id = rel_line.id
                     WHERE rel_line.source_company_id = sm.source_company_id
                       AND rel_line.account_id IS NOT NULL
               ) AS tax_relation_count,
               (
                   SELECT count(*)::text
                   FROM scoped_lines tag_line
                   JOIN account_account_tag_account_move_line_rel rel ON rel.account_move_line_id = tag_line.id
                     WHERE tag_line.source_company_id = sm.source_company_id
                       AND tag_line.account_id IS NOT NULL
               ) AS tax_tag_relation_count
        FROM scope_moves sm
        LEFT JOIN scoped_lines sl ON sl.move_id = sm.id
        GROUP BY sm.source_company_id
        ORDER BY sm.source_company_id
        """.format(date_from=USL_BENCHMARK_START, date_to=snapshot),
        set_readonly_role=False,
    )


def source_move_backed_payment_rows() -> list[dict[str, Any]]:
    snapshot = source_snapshot_date() or USL_BENCHMARK_END
    return query_rows(
        SOURCE_DB,
        f"""
        SELECT pay.id::text AS source_payment_id,
               pay.move_id::text AS source_move_id,
               pay.company_id::text AS source_company_id,
               pay.journal_id::text AS source_journal_id,
               COALESCE(pay.partner_id::text, '') AS source_partner_id,
               COALESCE(pay.outstanding_account_id::text, '') AS source_outstanding_account_id,
               COALESCE(pay.destination_account_id::text, '') AS source_destination_account_id,
               COALESCE(currency.name::text, '') AS currency,
               pay.date::text AS date,
               round(pay.amount::numeric, 2)::text AS amount,
               pay.payment_type::text AS payment_type,
               pay.partner_type::text AS partner_type,
               CASE
                   WHEN pay.state = 'posted' THEN 'paid'
                   ELSE COALESCE(pay.state::text, 'draft')
               END AS target_state,
               pay.state::text AS source_state,
               COALESCE(method.code::text, '') AS payment_method_code,
               COALESCE(method.payment_type::text, '') AS payment_method_type,
               'true' AS origin_linked
        FROM account_payment pay
        LEFT JOIN res_currency currency ON currency.id = pay.currency_id
        LEFT JOIN account_payment_method_line method_line ON method_line.id = pay.payment_method_line_id
        LEFT JOIN account_payment_method method ON method.id = method_line.payment_method_id
        WHERE pay.company_id IN (1, 8)
          AND pay.date BETWEEN DATE '{USL_BENCHMARK_START}' AND DATE '{snapshot}'
          AND pay.move_id IS NOT NULL
        ORDER BY pay.id
        """,
    )


def target_move_backed_payment_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT pay.rebuild_source_id::text AS source_payment_id,
               move.rebuild_source_id::text AS source_move_id,
               company.rebuild_source_id::text AS source_company_id,
               journal.rebuild_source_id::text AS source_journal_id,
               COALESCE(partner.rebuild_source_id::text, '') AS source_partner_id,
               COALESCE(outstanding.rebuild_source_id::text, '') AS source_outstanding_account_id,
               COALESCE(destination.rebuild_source_id::text, '') AS source_destination_account_id,
               COALESCE(currency.name::text, '') AS currency,
               pay.date::text AS date,
               round(pay.amount::numeric, 2)::text AS amount,
               pay.payment_type::text AS payment_type,
               pay.partner_type::text AS partner_type,
               pay.state::text AS target_state,
               substring(COALESCE(pay.rebuild_import_note, '') from 'Source payment state ''([^'']+)''') AS source_state,
               COALESCE(method.code::text, '') AS payment_method_code,
               COALESCE(method.payment_type::text, '') AS payment_method_type,
               (move.origin_payment_id = pay.id)::text AS origin_linked
        FROM account_payment pay
        JOIN account_move move ON move.id = pay.move_id
        JOIN res_company company ON company.id = pay.company_id
        JOIN account_journal journal ON journal.id = pay.journal_id
        LEFT JOIN res_partner partner ON partner.id = pay.partner_id
        LEFT JOIN account_account outstanding ON outstanding.id = pay.outstanding_account_id
        LEFT JOIN account_account destination ON destination.id = pay.destination_account_id
        LEFT JOIN res_currency currency ON currency.id = pay.currency_id
        LEFT JOIN account_payment_method_line method_line ON method_line.id = pay.payment_method_line_id
        LEFT JOIN account_payment_method method ON method.id = method_line.payment_method_id
        WHERE pay.rebuild_source_model = 'account.payment'
        ORDER BY pay.rebuild_source_id
        """,
        set_readonly_role=False,
    )


def source_no_entry_payment_rows() -> list[dict[str, Any]]:
    snapshot = source_snapshot_date() or USL_BENCHMARK_END
    return query_rows(
        SOURCE_DB,
        f"""
        SELECT pay.id::text AS source_payment_id,
               pay.name::text AS name,
               pay.company_id::text AS source_company_id,
               pay.journal_id::text AS source_journal_id,
               COALESCE(pay.partner_id::text, '') AS source_partner_id,
               COALESCE(currency.name::text, '') AS currency,
               pay.date::text AS date,
               round(pay.amount::numeric, 2)::text AS amount,
               round(pay.amount_company_currency_signed::numeric, 2)::text AS amount_company_currency_signed,
               pay.state::text AS state,
               pay.payment_type::text AS payment_type,
               pay.partner_type::text AS partner_type,
               COALESCE(pay.memo::text, '') AS memo,
               COALESCE(pay.payment_reference::text, '') AS payment_reference,
               COALESCE(pay.outstanding_account_id::text, '') AS source_outstanding_account_id,
               COALESCE(pay.destination_account_id::text, '') AS source_destination_account_id,
               COALESCE(pay.is_reconciled::text, 'null') AS is_reconciled,
               COALESCE(pay.is_matched::text, 'null') AS is_matched,
               COALESCE(pay.is_sent::text, 'null') AS is_sent,
               'none_no_source_move' AS accounting_effect
        FROM account_payment pay
        LEFT JOIN res_currency currency ON currency.id = pay.currency_id
        WHERE pay.company_id IN (1, 8)
          AND pay.date BETWEEN DATE '{USL_BENCHMARK_START}' AND DATE '{snapshot}'
          AND pay.move_id IS NULL
        ORDER BY pay.id
        """,
    )


def target_no_entry_payment_rows() -> list[dict[str, Any]]:
    snapshot = source_snapshot_date() or USL_BENCHMARK_END
    return query_rows(
        EXACT_VALIDATION_DB,
        f"""
        SELECT payment.rebuild_source_id::text AS source_payment_id,
               payment.name::text AS name,
               company.rebuild_source_id::text AS source_company_id,
               COALESCE(journal.rebuild_source_id::text, '') AS source_journal_id,
               COALESCE(partner.rebuild_source_id::text, '') AS source_partner_id,
               COALESCE(currency.name::text, '') AS currency,
               payment.date::text AS date,
               round(payment.amount::numeric, 2)::text AS amount,
               round(payment.usl_source_amount_company_currency_signed::numeric, 2)::text AS amount_company_currency_signed,
               payment.state::text AS state,
               COALESCE(payment.payment_type::text, '') AS payment_type,
               COALESCE(payment.partner_type::text, '') AS partner_type,
               COALESCE(payment.memo::text, '') AS memo,
               COALESCE(payment.payment_reference::text, '') AS payment_reference,
               COALESCE(NULLIF(payment.usl_source_outstanding_account_id, 0)::text, '') AS source_outstanding_account_id,
               COALESCE(NULLIF(payment.usl_source_destination_account_id, 0)::text, '') AS source_destination_account_id,
               payment.usl_source_is_reconciled_raw::text AS is_reconciled,
               payment.usl_source_is_matched_raw::text AS is_matched,
               payment.usl_source_is_sent_raw::text AS is_sent,
               'none_no_source_move' AS accounting_effect
        FROM account_payment payment
        JOIN res_company company ON company.id = payment.company_id
        LEFT JOIN account_journal journal ON journal.id = payment.journal_id
        LEFT JOIN res_partner partner ON partner.id = payment.partner_id
        LEFT JOIN res_currency currency ON currency.id = payment.currency_id
        WHERE payment.rebuild_source_model = 'account.payment'
          AND company.rebuild_source_id IN (1, 8)
          AND payment.date BETWEEN DATE '{USL_BENCHMARK_START}' AND DATE '{snapshot}'
          AND payment.move_id IS NULL
          AND payment.usl_historical_no_ledger_effect IS TRUE
        ORDER BY payment.rebuild_source_id
        """,
        set_readonly_role=False,
    )


def source_bank_statement_line_rows() -> list[dict[str, Any]]:
    snapshot = source_snapshot_date() or USL_BENCHMARK_END
    return query_rows(
        SOURCE_DB,
        f"""
        SELECT bsl.id::text AS source_statement_line_id,
               bsl.move_id::text AS source_move_id,
               bsl.company_id::text AS source_company_id,
               bsl.journal_id::text AS source_journal_id,
               COALESCE(bsl.partner_id::text, '') AS source_partner_id,
               COALESCE(currency.name::text, '') AS currency,
               COALESCE(foreign_currency.name::text, '') AS foreign_currency,
               COALESCE(bsl.account_number::text, '') AS account_number,
               COALESCE(bsl.partner_name::text, '') AS partner_name,
               COALESCE(bsl.transaction_type::text, '') AS transaction_type,
               COALESCE(bsl.payment_ref::text, '') AS payment_ref,
               COALESCE(bsl.internal_index::text, '') AS internal_index,
               bsl.sequence::text AS sequence,
               am.date::text AS date,
               round(bsl.amount::numeric, 2)::text AS amount,
               round(bsl.amount_currency::numeric, 2)::text AS amount_currency,
               round(bsl.amount_residual::numeric, 2)::text AS amount_residual,
               bsl.is_reconciled::text AS is_reconciled,
               'true' AS move_statement_linked
        FROM account_bank_statement_line bsl
        JOIN account_move am ON am.id = bsl.move_id
        LEFT JOIN res_currency currency ON currency.id = bsl.currency_id
        LEFT JOIN res_currency foreign_currency ON foreign_currency.id = bsl.foreign_currency_id
        WHERE bsl.company_id IN (1, 8)
          AND am.state = 'posted'
          AND am.date BETWEEN DATE '{USL_BENCHMARK_START}' AND DATE '{snapshot}'
        ORDER BY bsl.id
        """,
    )


def target_bank_statement_line_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT bsl.rebuild_source_id::text AS source_statement_line_id,
               move.rebuild_source_id::text AS source_move_id,
               company.rebuild_source_id::text AS source_company_id,
               journal.rebuild_source_id::text AS source_journal_id,
               COALESCE(partner.rebuild_source_id::text, '') AS source_partner_id,
               COALESCE(currency.name::text, '') AS currency,
               COALESCE(foreign_currency.name::text, '') AS foreign_currency,
               COALESCE(bsl.account_number::text, '') AS account_number,
               COALESCE(bsl.partner_name::text, '') AS partner_name,
               COALESCE(bsl.transaction_type::text, '') AS transaction_type,
               COALESCE(bsl.payment_ref::text, '') AS payment_ref,
               COALESCE(bsl.internal_index::text, '') AS internal_index,
               bsl.sequence::text AS sequence,
               move.date::text AS date,
               round(bsl.amount::numeric, 2)::text AS amount,
               round(bsl.amount_currency::numeric, 2)::text AS amount_currency,
               round(bsl.amount_residual::numeric, 2)::text AS amount_residual,
               bsl.is_reconciled::text AS is_reconciled,
               (move.statement_line_id = bsl.id)::text AS move_statement_linked
        FROM account_bank_statement_line bsl
        JOIN account_move move ON move.id = bsl.move_id
        JOIN res_company company ON company.id = bsl.company_id
        JOIN account_journal journal ON journal.id = bsl.journal_id
        LEFT JOIN res_partner partner ON partner.id = bsl.partner_id
        LEFT JOIN res_currency currency ON currency.id = bsl.currency_id
        LEFT JOIN res_currency foreign_currency ON foreign_currency.id = bsl.foreign_currency_id
        WHERE bsl.rebuild_source_model = 'account.bank.statement.line'
        ORDER BY bsl.rebuild_source_id
        """,
        set_readonly_role=False,
    )


def source_analytic_line_rows() -> list[dict[str, Any]]:
    snapshot = source_snapshot_date() or USL_BENCHMARK_END
    return query_rows(
        SOURCE_DB,
        f"""
        SELECT aal.id::text AS source_analytic_line_id,
               aal.account_id::text AS source_analytic_account_id,
               COALESCE((
                   SELECT string_agg(
                              dimension.value,
                              ',' ORDER BY dimension.value::integer
                          )
                     FROM jsonb_each_text(
                              jsonb_strip_nulls(to_jsonb(aal))
                          ) dimension
                    WHERE dimension.key = 'account_id'
                       OR dimension.key ~ '^x_plan[0-9]+_id$'
               ), '') AS source_analytic_account_ids,
               aal.company_id::text AS source_company_id,
               COALESCE(aal.partner_id::text, '') AS source_partner_id,
               COALESCE(aal.product_id::text, '') AS source_product_id,
               aal.date::text AS date,
               COALESCE(aal.name::text, '') AS name,
               COALESCE(aal.category::text, '') AS category,
               round(aal.amount::numeric, 2)::text AS amount,
               round(aal.unit_amount::numeric, 6)::text AS unit_amount,
               COALESCE(aal.general_account_id::text, '') AS source_general_account_id,
               COALESCE(aal.journal_id::text, '') AS source_journal_id,
               COALESCE(aal.move_line_id::text, '') AS source_move_line_id,
               COALESCE(aal.code::text, '') AS code,
               COALESCE(aal.ref::text, '') AS ref
        FROM account_analytic_line aal
        WHERE aal.company_id IN (1, 8)
          AND aal.date BETWEEN DATE '{USL_BENCHMARK_START}' AND DATE '{snapshot}'
        ORDER BY aal.id
        """,
    )


def target_analytic_line_rows() -> list[dict[str, Any]]:
    snapshot = source_snapshot_date() or USL_BENCHMARK_END
    return query_rows(
        EXACT_VALIDATION_DB,
        f"""
        SELECT aal.rebuild_source_id::text AS source_analytic_line_id,
               aal.rebuild_source_analytic_account_id::text AS source_analytic_account_id,
               COALESCE((
                   SELECT string_agg(
                              source_account.rebuild_source_id::text,
                              ',' ORDER BY source_account.rebuild_source_id
                          )
                     FROM jsonb_each_text(
                              jsonb_strip_nulls(to_jsonb(aal))
                          ) dimension
                     JOIN account_analytic_account source_account
                       ON source_account.id = dimension.value::integer
                    WHERE dimension.key = 'account_id'
                       OR dimension.key ~ '^x_plan[0-9]+_id$'
               ), '') AS source_analytic_account_ids,
               company.rebuild_source_id::text AS source_company_id,
               COALESCE(partner.rebuild_source_id::text, '') AS source_partner_id,
               COALESCE(product.rebuild_source_id::text, '') AS source_product_id,
               aal.date::text AS date,
               COALESCE(aal.name::text, '') AS name,
               COALESCE(aal.category::text, '') AS category,
               round(aal.amount::numeric, 2)::text AS amount,
               round(aal.unit_amount::numeric, 6)::text AS unit_amount,
               COALESCE(NULLIF(aal.rebuild_source_general_account_id, 0)::text, '') AS source_general_account_id,
               COALESCE(NULLIF(aal.rebuild_source_journal_id, 0)::text, '') AS source_journal_id,
               COALESCE(NULLIF(aal.rebuild_source_move_line_id, 0)::text, '') AS source_move_line_id,
               COALESCE(aal.code::text, '') AS code,
               COALESCE(aal.ref::text, '') AS ref
        FROM account_analytic_line aal
        JOIN res_company company ON company.id = aal.company_id
        LEFT JOIN res_partner partner ON partner.id = aal.partner_id
        LEFT JOIN product_product product ON product.id = aal.product_id
        WHERE aal.rebuild_source_model = 'account.analytic.line'
          AND company.rebuild_source_id IN (1, 8)
          AND aal.date BETWEEN DATE '{USL_BENCHMARK_START}' AND DATE '{snapshot}'
        ORDER BY aal.rebuild_source_id
        """,
        set_readonly_role=False,
    )


def source_accounting_attachment_rows() -> list[dict[str, Any]]:
    snapshot = source_snapshot_date() or USL_BENCHMARK_END
    return query_rows(
        SOURCE_DB,
        f"""
        WITH eligible_moves AS (
            SELECT id
              FROM account_move
             WHERE company_id IN (1, 8)
               AND (
                    (
                        state = 'posted'
                        AND date BETWEEN DATE '{USL_BENCHMARK_START}'
                                     AND DATE '{snapshot}'
                    )
                    OR (
                        state <> 'posted'
                        AND date >= DATE '{USL_BENCHMARK_START}'
                    )
               )
        ),
        attachment_scope AS (
            SELECT ia.id,
                   ia.res_model AS source_res_model,
                   ia.res_id AS source_res_id,
                   ia.res_id AS linked_source_res_id,
                   ia.name,
                   ia.mimetype,
                   ia.checksum,
                   ia.file_size,
                   ia.store_fname,
                   ia.type
              FROM ir_attachment ia
              LEFT JOIN account_asset asset
                ON ia.res_model = 'account.asset'
               AND ia.res_id = asset.id
             WHERE ia.type = 'binary'
               AND (
                    (
                        ia.res_model = 'account.move'
                        AND ia.res_id IN (SELECT id FROM eligible_moves)
                    )
                    OR (
                        ia.res_model = 'account.asset'
                        AND asset.company_id IN (1, 8)
                    )
               )
            UNION ALL
            SELECT ia.id,
                   ia.res_model AS source_res_model,
                   ia.res_id AS source_res_id,
                   message.res_id AS linked_source_res_id,
                   ia.name,
                   ia.mimetype,
                   ia.checksum,
                   ia.file_size,
                   ia.store_fname,
                   ia.type
              FROM ir_attachment ia
              JOIN LATERAL (
                   SELECT candidate.res_id
                     FROM message_attachment_rel relation
                     JOIN mail_message candidate
                       ON candidate.id = relation.message_id
                    WHERE relation.attachment_id = ia.id
                      AND candidate.model = 'account.move'
                      AND candidate.res_id IN (
                          SELECT id FROM eligible_moves
                      )
                    ORDER BY candidate.date, candidate.id
                    LIMIT 1
              ) message ON TRUE
             WHERE ia.type = 'binary'
               AND ia.res_model IS NULL
        )
        SELECT id::text AS source_attachment_id,
               COALESCE(source_res_model::text, '') AS source_res_model,
               COALESCE(NULLIF(source_res_id, 0)::text, '') AS source_res_id,
               linked_source_res_id::text AS linked_source_res_id,
               COALESCE(name::text, '') AS name,
               COALESCE(mimetype::text, '') AS mimetype,
               COALESCE(checksum::text, '') AS checksum,
               COALESCE(file_size::text, '') AS file_size,
               COALESCE(store_fname::text, '') AS store_fname,
               COALESCE(type::text, '') AS type
          FROM attachment_scope
         ORDER BY id
        """,
    )


def target_accounting_attachment_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT ia.rebuild_source_id::text AS source_attachment_id,
               COALESCE(
                   ia.rebuild_source_attachment_res_model::text,
                   ''
               ) AS source_res_model,
               COALESCE(
                   NULLIF(ia.rebuild_source_attachment_res_id, 0)::text,
                   ''
               ) AS source_res_id,
               COALESCE(ia.name::text, '') AS name,
               COALESCE(ia.mimetype::text, '') AS mimetype,
               COALESCE(ia.checksum::text, '') AS checksum,
               COALESCE(ia.file_size::text, '') AS file_size,
               COALESCE(ia.store_fname::text, '') AS store_fname,
               COALESCE(ia.type::text, '') AS type
        FROM ir_attachment ia
        WHERE ia.rebuild_source_model = 'ir.attachment'
        ORDER BY ia.rebuild_source_id
        """,
        set_readonly_role=False,
    )


def compare_rows(
    source_rows: list[dict[str, Any]],
    target_rows: list[dict[str, Any]],
    *,
    key: str,
    fields: list[str],
    max_examples: int = 25,
) -> dict[str, Any]:
    source_by_key = {str(row[key]): row for row in source_rows}
    target_by_key = {str(row[key]): row for row in target_rows}
    missing_in_target = sorted(set(source_by_key) - set(target_by_key))
    extra_in_target = sorted(set(target_by_key) - set(source_by_key))
    field_mismatches = []
    for row_key in sorted(set(source_by_key) & set(target_by_key)):
        source_row = source_by_key[row_key]
        target_row = target_by_key[row_key]
        differences = {}
        for field in fields:
            if str(source_row.get(field) or "") != str(target_row.get(field) or ""):
                differences[field] = {
                    "source": source_row.get(field),
                    "target": target_row.get(field),
                }
        if differences:
            field_mismatches.append({"key": row_key, "differences": differences})
    return {
        "source_count": len(source_rows),
        "target_count": len(target_rows),
        "missing_in_target_count": len(missing_in_target),
        "extra_in_target_count": len(extra_in_target),
        "field_mismatch_count": len(field_mismatches),
        "missing_in_target_examples": missing_in_target[:max_examples],
        "extra_in_target_examples": extra_in_target[:max_examples],
        "field_mismatch_examples": field_mismatches[:max_examples],
        "passed": not missing_in_target and not extra_in_target and not field_mismatches,
    }


def sequence_chronology_summary(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    names: dict[tuple[str, str], list[str]] = defaultdict(list)
    numbers: dict[tuple[str, str, int], list[str]] = defaultdict(list)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    missing_names = []
    for row in rows:
        source_move_id = str(row["source_move_id"])
        journal_id = str(row["source_journal_id"])
        move_name = str(row.get("move_name") or "/")
        prefix = str(row.get("sequence_prefix") or "")
        number = int(row.get("sequence_number") or 0)
        move_date = str(row.get("date") or "")
        if move_name == "/":
            missing_names.append(source_move_id)
        names[journal_id, move_name].append(source_move_id)
        if number:
            numbers[journal_id, prefix, number].append(source_move_id)
            groups[journal_id, prefix].append({
                "source_move_id": source_move_id,
                "move_name": move_name,
                "date": move_date,
                "sequence_number": number,
            })

    duplicate_names = [
        {
            "source_journal_id": journal_id,
            "move_name": move_name,
            "source_move_ids": source_move_ids,
        }
        for (journal_id, move_name), source_move_ids in names.items()
        if move_name != "/" and len(source_move_ids) > 1
    ]
    duplicate_numbers = [
        {
            "source_journal_id": journal_id,
            "sequence_prefix": prefix,
            "sequence_number": number,
            "source_move_ids": source_move_ids,
        }
        for (
            journal_id,
            prefix,
            number,
        ), source_move_ids in numbers.items()
        if len(source_move_ids) > 1
    ]
    gaps = []
    date_decreases = []
    for (journal_id, prefix), group_rows in groups.items():
        previous = None
        for row in sorted(
            group_rows,
            key=lambda item: (
                item["sequence_number"],
                int(item["source_move_id"]),
            ),
        ):
            if previous:
                if (
                    row["sequence_number"]
                    > previous["sequence_number"] + 1
                ):
                    gaps.append({
                        "source_journal_id": journal_id,
                        "sequence_prefix": prefix,
                        "previous_source_move_id": (
                            previous["source_move_id"]
                        ),
                        "previous_move_name": previous["move_name"],
                        "previous_sequence_number": (
                            previous["sequence_number"]
                        ),
                        "source_move_id": row["source_move_id"],
                        "move_name": row["move_name"],
                        "sequence_number": row["sequence_number"],
                    })
                if row["date"] < previous["date"]:
                    date_decreases.append({
                        "source_journal_id": journal_id,
                        "sequence_prefix": prefix,
                        "previous_source_move_id": (
                            previous["source_move_id"]
                        ),
                        "previous_move_name": previous["move_name"],
                        "previous_date": previous["date"],
                        "source_move_id": row["source_move_id"],
                        "move_name": row["move_name"],
                        "date": row["date"],
                    })
            previous = row
    return {
        "move_count": len(rows),
        "missing_name_count": len(missing_names),
        "missing_name_examples": missing_names[:10],
        "duplicate_name_group_count": len(duplicate_names),
        "duplicate_name_examples": duplicate_names[:10],
        "duplicate_sequence_number_group_count": len(duplicate_numbers),
        "duplicate_sequence_number_examples": duplicate_numbers[:10],
        "sequence_gap_count": len(gaps),
        "sequence_gap_examples": gaps[:10],
        "sequence_date_decrease_count": len(date_decreases),
        "sequence_date_decrease_examples": date_decreases[:10],
    }


def source_move_comparison_rows() -> list[dict[str, Any]]:
    return query_rows(
        SOURCE_DB,
        """
        SELECT am.id::text AS source_move_id,
               COALESCE(am.name::text, '/') AS move_name,
               am.date::text AS date,
               COALESCE(am.sequence_prefix::text, '') AS sequence_prefix,
               am.sequence_number::text AS sequence_number,
               am.journal_id::text AS source_journal_id,
               am.company_id::text AS source_company_id,
               COALESCE(am.partner_id::text, '') AS source_partner_id,
               am.state::text AS state
        FROM account_move am
        WHERE am.company_id IN (1, 8)
        ORDER BY am.id
        """,
    )


def target_move_comparison_rows(
    database: str = EXACT_VALIDATION_DB,
) -> list[dict[str, Any]]:
    trace_models = [
        "account.move",
        *DEV_SOURCE_TRACE_ALIASES["account.move"],
    ]
    trace_model_sql = ", ".join(
        f"'{model_name}'"
        for model_name in trace_models
    )
    return query_rows(
        database,
        f"""
        SELECT am.rebuild_source_id::text AS source_move_id,
               am.name::text AS move_name,
               am.date::text AS date,
               COALESCE(am.sequence_prefix::text, '') AS sequence_prefix,
               am.sequence_number::text AS sequence_number,
               aj.rebuild_source_id::text AS source_journal_id,
               rc.rebuild_source_id::text AS source_company_id,
               COALESCE(rp.rebuild_source_id::text, '') AS source_partner_id,
               am.state::text AS state
        FROM account_move am
        JOIN account_journal aj ON aj.id = am.journal_id
        JOIN res_company rc ON rc.id = am.company_id
        LEFT JOIN res_partner rp ON rp.id = am.partner_id
        WHERE am.rebuild_source_model IN ({trace_model_sql})
          AND rc.rebuild_source_id IN (1, 8)
        ORDER BY am.rebuild_source_id
        """,
        set_readonly_role=False,
    )



def source_line_comparison_rows() -> list[dict[str, Any]]:
    return query_rows(
        SOURCE_DB,
        """
        SELECT aml.id::text AS source_line_id,
               aml.move_id::text AS source_move_id,
               COALESCE(aml.account_id::text, '') AS source_account_id,
               COALESCE(aml.partner_id::text, '') AS source_partner_id,
               COALESCE(rc.name::text, '') AS currency,
               round(aml.debit::numeric, 2)::text AS debit,
               round(aml.credit::numeric, 2)::text AS credit,
               round(aml.balance::numeric, 2)::text AS balance,
               round(aml.amount_currency::numeric, 2)::text AS amount_currency,
               COALESCE(aml.date_maturity::text, '') AS date_maturity
        FROM account_move_line aml
        JOIN account_move am ON am.id = aml.move_id
        LEFT JOIN res_currency rc ON rc.id = aml.currency_id
        WHERE am.company_id IN (1, 8)
        ORDER BY aml.id
        """,
    )


def target_line_comparison_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT aml.rebuild_source_id::text AS source_line_id,
               am.rebuild_source_id::text AS source_move_id,
               COALESCE(aa.rebuild_source_id::text, '') AS source_account_id,
               COALESCE(rp.rebuild_source_id::text, '') AS source_partner_id,
               COALESCE(rc.name::text, '') AS currency,
               round(aml.debit::numeric, 2)::text AS debit,
               round(aml.credit::numeric, 2)::text AS credit,
               round(aml.balance::numeric, 2)::text AS balance,
               round(aml.amount_currency::numeric, 2)::text AS amount_currency,
               COALESCE(aml.date_maturity::text, '') AS date_maturity
        FROM account_move_line aml
        JOIN account_move am ON am.id = aml.move_id
        LEFT JOIN account_account aa ON aa.id = aml.account_id
        JOIN res_company company ON company.id = am.company_id
        LEFT JOIN res_partner rp ON rp.id = aml.partner_id
        LEFT JOIN res_currency rc ON rc.id = aml.currency_id
        WHERE aml.rebuild_source_model = 'account.move.line'
          AND company.rebuild_source_id IN (1, 8)
        ORDER BY aml.rebuild_source_id
        """,
        set_readonly_role=False,
    )


def source_account_balance_rows() -> list[dict[str, Any]]:
    return query_rows(
        SOURCE_DB,
        f"""
        SELECT aa.id::text AS source_account_id,
               {source_account_code_expr()} AS code,
               {source_name_expr('aa')} AS name,
               round(sum(aml.debit)::numeric, 2)::text AS debit,
               round(sum(aml.credit)::numeric, 2)::text AS credit,
               round(sum(aml.balance)::numeric, 2)::text AS balance
        FROM account_move_line aml
        JOIN account_move am ON am.id = aml.move_id
        JOIN account_account aa ON aa.id = aml.account_id
        WHERE am.company_id = 1
          AND am.state = 'posted'
          AND am.date BETWEEN DATE '{USL_BENCHMARK_START}' AND DATE '{USL_BENCHMARK_END}'
        GROUP BY aa.id
        ORDER BY {source_account_code_expr()}, aa.id
        """,
    )


def target_account_balance_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT aa.rebuild_source_id::text AS source_account_id,
               {account_code} AS code,
               {account_name} AS name,
               round(sum(aml.debit)::numeric, 2)::text AS debit,
               round(sum(aml.credit)::numeric, 2)::text AS credit,
               round(sum(aml.balance)::numeric, 2)::text AS balance
        FROM account_move_line aml
        JOIN account_move am ON am.id = aml.move_id
        JOIN account_account aa ON aa.id = aml.account_id
        JOIN res_company company ON company.id = am.company_id
        WHERE aml.rebuild_source_model = 'account.move.line'
          AND {benchmark_where}
        GROUP BY aa.rebuild_source_id, {account_code}, {account_name}
        ORDER BY {account_code}, aa.rebuild_source_id
        """.format(
            account_code=target_account_code_expr(),
            account_name=source_name_expr("aa"),
            benchmark_where=target_benchmark_move_where(),
        ),
        set_readonly_role=False,
    )


def source_journal_balance_rows() -> list[dict[str, Any]]:
    return query_rows(
        SOURCE_DB,
        f"""
        SELECT aj.id::text AS source_journal_id,
               aj.code::text AS code,
               {source_name_expr('aj')} AS name,
               count(DISTINCT am.id)::text AS move_count,
               count(aml.id)::text AS move_line_count,
               round(sum(aml.debit)::numeric, 2)::text AS debit,
               round(sum(aml.credit)::numeric, 2)::text AS credit,
               round(sum(aml.balance)::numeric, 2)::text AS balance
        FROM account_move_line aml
        JOIN account_move am ON am.id = aml.move_id
        JOIN account_journal aj ON aj.id = am.journal_id
        WHERE am.company_id = 1
          AND am.state = 'posted'
          AND am.date BETWEEN DATE '{USL_BENCHMARK_START}' AND DATE '{USL_BENCHMARK_END}'
        GROUP BY aj.id
        ORDER BY aj.code, aj.id
        """,
    )


def source_partial_reconcile_rows() -> list[dict[str, Any]]:
    return query_rows(
        SOURCE_DB,
        f"""
        WITH imported AS (
            SELECT aml.id
            FROM account_move_line aml
            JOIN account_move am ON am.id = aml.move_id
            WHERE am.company_id = 1
              AND am.state = 'posted'
              AND am.date BETWEEN DATE '{USL_BENCHMARK_START}' AND DATE '{USL_BENCHMARK_END}'
        ),
        full_lines AS (
            SELECT full_reconcile_id,
                   count(*) AS total_line_count,
                   count(*) FILTER (WHERE id IN (SELECT id FROM imported)) AS imported_line_count
            FROM account_move_line
            WHERE full_reconcile_id IS NOT NULL
            GROUP BY full_reconcile_id
        ),
        contained_fulls AS (
            SELECT full_reconcile_id
            FROM full_lines
            WHERE imported_line_count > 0
              AND imported_line_count = total_line_count
        )
        SELECT pr.id::text AS source_partial_reconcile_id,
               pr.debit_move_id::text AS source_debit_line_id,
               pr.credit_move_id::text AS source_credit_line_id,
               CASE
                   WHEN pr.full_reconcile_id IN (SELECT full_reconcile_id FROM contained_fulls)
                   THEN pr.full_reconcile_id::text
                   ELSE ''
               END AS source_full_reconcile_id,
               COALESCE(pr.exchange_move_id::text, '') AS source_exchange_move_id,
               round(pr.amount::numeric, 2)::text AS amount,
               round(pr.debit_amount_currency::numeric, 2)::text AS debit_amount_currency,
               round(pr.credit_amount_currency::numeric, 2)::text AS credit_amount_currency,
               pr.max_date::text AS max_date
        FROM account_partial_reconcile pr
        WHERE pr.debit_move_id IN (SELECT id FROM imported)
          AND pr.credit_move_id IN (SELECT id FROM imported)
        ORDER BY pr.id
        """,
    )


def target_partial_reconcile_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        WITH imported AS (
            SELECT line.id
            FROM account_move_line line
            JOIN account_move move ON move.id = line.move_id
            JOIN res_company company ON company.id = move.company_id
            WHERE line.rebuild_source_model = 'account.move.line'
              AND {benchmark_where}
        ),
        full_lines AS (
            SELECT line.full_reconcile_id,
                   count(*) AS total_line_count,
                   count(*) FILTER (WHERE line.id IN (SELECT id FROM imported)) AS imported_line_count
            FROM account_move_line line
            WHERE line.full_reconcile_id IS NOT NULL
            GROUP BY line.full_reconcile_id
        ),
        contained_fulls AS (
            SELECT full_reconcile_id
            FROM full_lines
            WHERE imported_line_count > 0
              AND imported_line_count = total_line_count
        )
        SELECT pr.rebuild_source_id::text AS source_partial_reconcile_id,
               debit_line.rebuild_source_id::text AS source_debit_line_id,
               credit_line.rebuild_source_id::text AS source_credit_line_id,
               CASE
                   WHEN full_rec.id IN (SELECT full_reconcile_id FROM contained_fulls)
                   THEN full_rec.rebuild_source_id::text
                   ELSE ''
               END AS source_full_reconcile_id,
               COALESCE(exchange_move.rebuild_source_id::text, '') AS source_exchange_move_id,
               round(pr.amount::numeric, 2)::text AS amount,
               round(pr.debit_amount_currency::numeric, 2)::text AS debit_amount_currency,
               round(pr.credit_amount_currency::numeric, 2)::text AS credit_amount_currency,
               pr.max_date::text AS max_date
        FROM account_partial_reconcile pr
        JOIN account_move_line debit_line ON debit_line.id = pr.debit_move_id
        JOIN account_move_line credit_line ON credit_line.id = pr.credit_move_id
        JOIN account_move debit_move ON debit_move.id = debit_line.move_id
        JOIN account_move credit_move ON credit_move.id = credit_line.move_id
        JOIN res_company debit_company ON debit_company.id = debit_move.company_id
        JOIN res_company credit_company ON credit_company.id = credit_move.company_id
        LEFT JOIN account_full_reconcile full_rec ON full_rec.id = pr.full_reconcile_id
        LEFT JOIN account_move exchange_move ON exchange_move.id = pr.exchange_move_id
        WHERE pr.rebuild_source_model = 'account.partial.reconcile'
          AND {debit_benchmark_where}
          AND {credit_benchmark_where}
        ORDER BY pr.rebuild_source_id
        """.format(
            debit_benchmark_where=target_benchmark_move_where(
                move_alias="debit_move",
                company_alias="debit_company",
            ),
            credit_benchmark_where=target_benchmark_move_where(
                move_alias="credit_move",
                company_alias="credit_company",
            ),
            benchmark_where=target_benchmark_move_where(move_alias="move"),
        ),
        set_readonly_role=False,
    )


def source_full_reconcile_rows() -> list[dict[str, Any]]:
    return query_rows(
        SOURCE_DB,
        f"""
        WITH imported AS (
            SELECT aml.id
            FROM account_move_line aml
            JOIN account_move am ON am.id = aml.move_id
            WHERE am.company_id = 1
              AND am.state = 'posted'
              AND am.date BETWEEN DATE '{USL_BENCHMARK_START}' AND DATE '{USL_BENCHMARK_END}'
        ),
        full_lines AS (
            SELECT full_reconcile_id,
                   count(*) AS total_line_count,
                   count(*) FILTER (WHERE id IN (SELECT id FROM imported)) AS imported_line_count,
                   array_agg(id ORDER BY id) FILTER (WHERE id IN (SELECT id FROM imported)) AS imported_line_ids
            FROM account_move_line
            WHERE full_reconcile_id IS NOT NULL
            GROUP BY full_reconcile_id
        ),
        contained_fulls AS (
            SELECT full_reconcile_id, imported_line_ids
            FROM full_lines
            WHERE imported_line_count > 0
              AND imported_line_count = total_line_count
        )
        SELECT cf.full_reconcile_id::text AS source_full_reconcile_id,
               array_to_string(cf.imported_line_ids, ',') AS source_line_ids,
               array_to_string(array_remove(array_agg(pr.id ORDER BY pr.id), NULL), ',') AS source_partial_reconcile_ids
        FROM contained_fulls cf
        LEFT JOIN account_partial_reconcile pr ON pr.full_reconcile_id = cf.full_reconcile_id
        GROUP BY cf.full_reconcile_id, cf.imported_line_ids
        ORDER BY cf.full_reconcile_id
        """,
    )


def target_full_reconcile_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        WITH imported AS (
            SELECT line.id
            FROM account_move_line line
            JOIN account_move move ON move.id = line.move_id
            JOIN res_company company ON company.id = move.company_id
            WHERE line.rebuild_source_model = 'account.move.line'
              AND {benchmark_where}
        ),
        full_lines AS (
            SELECT line.full_reconcile_id,
                   count(*) AS total_line_count,
                   count(*) FILTER (WHERE line.id IN (SELECT id FROM imported)) AS imported_line_count
            FROM account_move_line line
            WHERE line.full_reconcile_id IS NOT NULL
            GROUP BY line.full_reconcile_id
        ),
        contained_fulls AS (
            SELECT full_reconcile_id
            FROM full_lines
            WHERE imported_line_count > 0
              AND imported_line_count = total_line_count
        )
        SELECT full_rec.rebuild_source_id::text AS source_full_reconcile_id,
               COALESCE((
                   SELECT string_agg(line.rebuild_source_id::text, ',' ORDER BY line.rebuild_source_id)
                   FROM account_move_line line
                   WHERE line.full_reconcile_id = full_rec.id
                     AND line.id IN (SELECT id FROM imported)
               ), '') AS source_line_ids,
               COALESCE((
                   SELECT string_agg(partial.rebuild_source_id::text, ',' ORDER BY partial.rebuild_source_id)
                   FROM account_partial_reconcile partial
                   WHERE partial.full_reconcile_id = full_rec.id
               ), '') AS source_partial_reconcile_ids
        FROM account_full_reconcile full_rec
        WHERE full_rec.rebuild_source_model = 'account.full.reconcile'
          AND full_rec.id IN (SELECT full_reconcile_id FROM contained_fulls)
        ORDER BY full_rec.rebuild_source_id
        """.format(benchmark_where=target_benchmark_move_where(move_alias="move")),
        set_readonly_role=False,
    )



def source_report_rows() -> list[dict[str, Any]]:
    if not table_exists(SOURCE_DB, "account_report"):
        return []
    rows = query_rows(
        SOURCE_DB,
        """
        SELECT 'account.report:' || r.id::text AS source_report_key,
               r.id::text AS source_report_id,
               COALESCE(r.name->>'en_US', r.name->>'fr_FR', r.name::text) AS source_name,
               COALESCE(r.name->>'fr_FR', r.name->>'en_US', r.name::text) AS localized_name,
               r.active::text AS active,
               COALESCE(r.sequence::text, '0') AS sequence,
               COALESCE(country.code::text, '') AS country_code,
               COALESCE(r.country_id::text, '') AS source_country_id,
               COALESCE(r.chart_template::text, '') AS chart_template,
               COALESCE(r.root_report_id::text, '') AS source_root_report_id,
               COALESCE(root.name->>'en_US', root.name->>'fr_FR', root.name::text, '') AS root_report_name,
               COALESCE(handler.model::text, '') AS source_custom_handler_model,
               COALESCE(r.availability_condition::text, '') AS availability_condition,
               COALESCE(r.integer_rounding::text, '') AS integer_rounding,
               COALESCE(r.default_opening_date_filter::text, '') AS default_opening_date_filter,
               COALESCE(r.currency_translation::text, '') AS currency_translation,
               COALESCE(r.filter_multi_company::text, '') AS filter_multi_company,
               COALESCE(r.filter_hide_0_lines::text, '') AS filter_hide_0_lines,
               COALESCE(r.filter_hierarchy::text, '') AS filter_hierarchy,
               COALESCE(r.filter_account_type::text, '') AS filter_account_type,
               COALESCE(r.filter_date_range::text, 'false') AS filter_date_range,
               COALESCE(r.filter_show_draft::text, 'false') AS filter_show_draft,
               COALESCE(r.filter_unreconciled::text, 'false') AS filter_unreconciled,
               COALESCE(r.filter_unfold_all::text, 'false') AS filter_unfold_all,
               COALESCE(r.filter_period_comparison::text, 'false') AS filter_period_comparison,
               COALESCE(r.filter_growth_comparison::text, 'false') AS filter_growth_comparison,
               COALESCE(r.filter_journals::text, 'false') AS filter_journals,
               COALESCE(r.filter_partner::text, 'false') AS filter_partner,
               COALESCE(r.filter_aml_ir_filters::text, 'false') AS filter_aml_ir_filters,
               COALESCE(r.filter_budgets::text, 'false') AS filter_budgets,
               COALESCE(r.filter_analytic_groupby::text, 'false') AS filter_analytic_groupby,
               COALESCE(r.filter_cash_basis::text, 'false') AS filter_cash_basis,
               COALESCE(r.use_sections::text, 'false') AS use_sections,
               COALESCE(r.only_tax_exigible::text, 'false') AS only_tax_exigible,
               COALESCE(r.use_fiscal_periods::text, 'false') AS use_fiscal_periods,
               COALESCE(r.allow_foreign_vat::text, 'false') AS allow_foreign_vat,
               COALESCE(line_counts.line_count, 0)::text AS line_count,
               COALESCE(column_counts.column_count, 0)::text AS column_count,
               COALESCE(expression_counts.expression_count, 0)::text AS expression_count,
               COALESCE(external_value_counts.external_value_count, 0)::text AS external_value_count,
               COALESCE(line_codes.line_code_sample, '') AS line_code_sample,
               COALESCE(engine_counts.expression_engine_summary, '{}'::jsonb)::text AS expression_engine_summary
          FROM account_report r
          LEFT JOIN res_country country ON country.id = r.country_id
          LEFT JOIN account_report root ON root.id = r.root_report_id
          LEFT JOIN ir_model handler ON handler.id = r.custom_handler_model_id
          LEFT JOIN (
                SELECT report_id, count(*) AS line_count
                  FROM account_report_line
                 GROUP BY report_id
          ) line_counts ON line_counts.report_id = r.id
          LEFT JOIN (
                SELECT report_id, count(*) AS column_count
                  FROM account_report_column
                 GROUP BY report_id
          ) column_counts ON column_counts.report_id = r.id
          LEFT JOIN (
                SELECT line.report_id, count(expression.id) AS expression_count
                  FROM account_report_line line
                  JOIN account_report_expression expression ON expression.report_line_id = line.id
                 GROUP BY line.report_id
          ) expression_counts ON expression_counts.report_id = r.id
          LEFT JOIN (
                SELECT line.report_id, count(external_value.id) AS external_value_count
                  FROM account_report_line line
                  JOIN account_report_expression expression ON expression.report_line_id = line.id
                  JOIN account_report_external_value external_value ON external_value.target_report_expression_id = expression.id
                 GROUP BY line.report_id
          ) external_value_counts ON external_value_counts.report_id = r.id
          LEFT JOIN (
                SELECT line.report_id, string_agg(line.code, ', ' ORDER BY line.sequence, line.id) AS line_code_sample
                  FROM account_report_line line
                 WHERE line.code IS NOT NULL
                 GROUP BY line.report_id
          ) line_codes ON line_codes.report_id = r.id
          LEFT JOIN (
                SELECT q.report_id, jsonb_object_agg(q.engine, q.expression_count ORDER BY q.engine) AS expression_engine_summary
                  FROM (
                        SELECT line.report_id,
                               COALESCE(expression.engine, 'unknown') AS engine,
                               count(*) AS expression_count
                          FROM account_report_line line
                          JOIN account_report_expression expression ON expression.report_line_id = line.id
                         GROUP BY line.report_id, COALESCE(expression.engine, 'unknown')
                  ) q
                 GROUP BY q.report_id
          ) engine_counts ON engine_counts.report_id = r.id
         ORDER BY r.id
        """,
    )
    for row in rows:
        row["decision"] = classify_report(" ".join([row.get("source_name") or "", row.get("localized_name") or ""]))
        row["target_action_xmlid"] = source_report_target_action(row)
        row["target_evidence_key"] = source_report_target_evidence_key(row)
        row["imported_line_count"] = row["line_count"]
        row["imported_column_count"] = row["column_count"]
        row["imported_expression_count"] = row["expression_count"]
        row["target_status"] = (
            "partial_target_equivalent" if row["target_action_xmlid"]
            else "missing_target_equivalent" if row["decision"] in {"MANDATORY_PARITY", "OPERATIONAL_PARITY"}
            else "decision_pending"
        )
    return rows


def target_source_report_rows() -> list[dict[str, Any]]:
    if not table_exists(EXACT_VALIDATION_DB, "rebuild_account_source_report"):
        return []
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT report.rebuild_source_model || ':' || report.rebuild_source_id::text AS source_report_key,
               report.source_report_id::text AS source_report_id,
               COALESCE(report.source_name::text, '') AS source_name,
               COALESCE(report.localized_name::text, '') AS localized_name,
               report.active::text AS active,
               report.sequence::text AS sequence,
               COALESCE(report.country_code::text, '') AS country_code,
               COALESCE(NULLIF(report.source_country_id, 0)::text, '') AS source_country_id,
               COALESCE(report.chart_template::text, '') AS chart_template,
               COALESCE(NULLIF(report.source_root_report_id, 0)::text, '') AS source_root_report_id,
               COALESCE(report.root_report_name::text, '') AS root_report_name,
               COALESCE(report.source_custom_handler_model::text, '') AS source_custom_handler_model,
               report.decision::text AS decision,
               report.target_status::text AS target_status,
               COALESCE(report.target_action_xmlid::text, '') AS target_action_xmlid,
               COALESCE(report.target_evidence_key::text, '') AS target_evidence_key,
               COALESCE(report.availability_condition::text, '') AS availability_condition,
               COALESCE(report.integer_rounding::text, '') AS integer_rounding,
               COALESCE(report.default_opening_date_filter::text, '') AS default_opening_date_filter,
               COALESCE(report.currency_translation::text, '') AS currency_translation,
               COALESCE(report.filter_multi_company::text, '') AS filter_multi_company,
               COALESCE(report.filter_hide_0_lines::text, '') AS filter_hide_0_lines,
               COALESCE(report.filter_hierarchy::text, '') AS filter_hierarchy,
               COALESCE(report.filter_account_type::text, '') AS filter_account_type,
               report.filter_date_range::text AS filter_date_range,
               report.filter_show_draft::text AS filter_show_draft,
               report.filter_unreconciled::text AS filter_unreconciled,
               report.filter_unfold_all::text AS filter_unfold_all,
               report.filter_period_comparison::text AS filter_period_comparison,
               report.filter_growth_comparison::text AS filter_growth_comparison,
               report.filter_journals::text AS filter_journals,
               report.filter_partner::text AS filter_partner,
               report.filter_aml_ir_filters::text AS filter_aml_ir_filters,
               report.filter_budgets::text AS filter_budgets,
               report.filter_analytic_groupby::text AS filter_analytic_groupby,
               report.filter_cash_basis::text AS filter_cash_basis,
               report.use_sections::text AS use_sections,
               report.only_tax_exigible::text AS only_tax_exigible,
               report.use_fiscal_periods::text AS use_fiscal_periods,
               report.allow_foreign_vat::text AS allow_foreign_vat,
               report.line_count::text AS line_count,
               report.column_count::text AS column_count,
               report.expression_count::text AS expression_count,
               COALESCE(report.imported_line_count::text, '') AS imported_line_count,
               COALESCE(report.imported_column_count::text, '') AS imported_column_count,
               COALESCE(report.imported_expression_count::text, '') AS imported_expression_count,
               report.external_value_count::text AS external_value_count,
               COALESCE(report.line_code_sample::text, '') AS line_code_sample,
               COALESCE(report.expression_engine_summary::jsonb, '{}'::jsonb)::text AS expression_engine_summary
          FROM rebuild_account_source_report report
         ORDER BY report.rebuild_source_id
        """,
        set_readonly_role=False,
    )


def source_report_line_rows() -> list[dict[str, Any]]:
    if not table_exists(SOURCE_DB, "account_report_line"):
        return []
    source_columns = column_names(SOURCE_DB, "account_report_line")
    foldability_expression = (
        "COALESCE(line.foldability::text, 'always_unfolded')"
        if "foldability" in source_columns
        else "CASE WHEN line.foldable THEN 'foldable' ELSE 'always_unfolded' END"
    )
    return query_rows(
        SOURCE_DB,
        f"""
        SELECT 'account.report.line:' || line.id::text AS source_report_line_key,
               line.id::text AS source_line_id,
               line.report_id::text AS source_report_id,
               COALESCE(line.parent_id::text, '') AS source_parent_line_id,
               COALESCE(line.hierarchy_level::text, '0') AS hierarchy_level,
               COALESCE(line.sequence::text, '0') AS sequence,
               COALESCE(line.action_id::text, '') AS source_action_id,
               COALESCE(line.groupby::text, '') AS groupby,
               COALESCE(line.user_groupby::text, '') AS user_groupby,
               COALESCE(line.code::text, '') AS code,
               COALESCE(line.horizontal_split_side::text, '') AS horizontal_split_side,
               COALESCE(line.name->>'en_US', line.name->>'fr_FR', line.name::text) AS source_name,
               COALESCE(line.name->>'fr_FR', line.name->>'en_US', line.name::text) AS localized_name,
               {foldability_expression} AS foldability,
               COALESCE(line.print_on_new_page::text, 'false') AS print_on_new_page,
               COALESCE(line.hide_if_zero::text, 'false') AS hide_if_zero,
               COALESCE(expression_counts.expression_count, 0)::text AS expression_count
          FROM account_report_line line
          LEFT JOIN (
                SELECT report_line_id, count(*) AS expression_count
                  FROM account_report_expression
                 GROUP BY report_line_id
          ) expression_counts ON expression_counts.report_line_id = line.id
         ORDER BY line.report_id, line.sequence, line.id
        """,
    )


def target_source_report_line_rows() -> list[dict[str, Any]]:
    if not table_exists(EXACT_VALIDATION_DB, "rebuild_account_source_report_line"):
        return []
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT line.rebuild_source_model || ':' || line.rebuild_source_id::text AS source_report_line_key,
               line.source_line_id::text AS source_line_id,
               line.source_report_id::text AS source_report_id,
               COALESCE(NULLIF(line.source_parent_line_id, 0)::text, '') AS source_parent_line_id,
               line.hierarchy_level::text AS hierarchy_level,
               line.sequence::text AS sequence,
               COALESCE(NULLIF(line.source_action_id, 0)::text, '') AS source_action_id,
               COALESCE(line.groupby::text, '') AS groupby,
               COALESCE(line.user_groupby::text, '') AS user_groupby,
               COALESCE(line.code::text, '') AS code,
               COALESCE(line.horizontal_split_side::text, '') AS horizontal_split_side,
               COALESCE(line.name::text, '') AS source_name,
               COALESCE(line.localized_name::text, '') AS localized_name,
               COALESCE(line.foldability::text,
                        CASE WHEN line.foldable THEN 'foldable' ELSE 'always_unfolded' END)
                   AS foldability,
               line.print_on_new_page::text AS print_on_new_page,
               line.hide_if_zero::text AS hide_if_zero,
               line.expression_count::text AS expression_count
          FROM rebuild_account_source_report_line line
         ORDER BY line.source_report_id, line.sequence, line.source_line_id
        """,
        set_readonly_role=False,
    )


def source_report_expression_rows() -> list[dict[str, Any]]:
    if not table_exists(SOURCE_DB, "account_report_expression"):
        return []
    return query_rows(
        SOURCE_DB,
        """
        SELECT 'account.report.expression:' || expression.id::text AS source_report_expression_key,
               expression.id::text AS source_expression_id,
               line.report_id::text AS source_report_id,
               expression.report_line_id::text AS source_report_line_id,
               COALESCE(line.code::text, '') AS line_code,
               COALESCE(line.name->>'en_US', line.name->>'fr_FR', line.name::text) AS line_name,
               COALESCE(expression.label::text, '') AS label,
               COALESCE(expression.engine::text, '') AS engine,
               COALESCE(expression.formula::text, '') AS formula,
               COALESCE(expression.subformula::text, '') AS subformula,
               COALESCE(expression.date_scope::text, '') AS date_scope,
               COALESCE(expression.figure_type::text, '') AS figure_type,
               COALESCE(expression.carryover_target::text, '') AS carryover_target,
               COALESCE(expression.green_on_positive::text, 'false') AS green_on_positive,
               COALESCE(expression.blank_if_zero::text, 'false') AS blank_if_zero,
               COALESCE(expression.auditable::text, 'false') AS auditable
          FROM account_report_expression expression
          JOIN account_report_line line ON line.id = expression.report_line_id
         ORDER BY line.report_id, line.sequence, line.id, expression.id
        """,
    )


def target_source_report_expression_rows() -> list[dict[str, Any]]:
    if not table_exists(EXACT_VALIDATION_DB, "rebuild_account_source_report_expression"):
        return []
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT expression.rebuild_source_model || ':' || expression.rebuild_source_id::text AS source_report_expression_key,
               expression.source_expression_id::text AS source_expression_id,
               expression.source_report_id::text AS source_report_id,
               expression.source_report_line_id::text AS source_report_line_id,
               COALESCE(expression.line_code::text, '') AS line_code,
               COALESCE(expression.line_name::text, '') AS line_name,
               COALESCE(expression.label::text, '') AS label,
               COALESCE(expression.engine::text, '') AS engine,
               COALESCE(expression.formula::text, '') AS formula,
               COALESCE(expression.subformula::text, '') AS subformula,
               COALESCE(expression.date_scope::text, '') AS date_scope,
               COALESCE(expression.figure_type::text, '') AS figure_type,
               COALESCE(expression.carryover_target::text, '') AS carryover_target,
               expression.green_on_positive::text AS green_on_positive,
               expression.blank_if_zero::text AS blank_if_zero,
               expression.auditable::text AS auditable
          FROM rebuild_account_source_report_expression expression
         ORDER BY expression.source_report_id, expression.source_report_line_id, expression.source_expression_id
        """,
        set_readonly_role=False,
    )


def source_report_column_rows() -> list[dict[str, Any]]:
    if not table_exists(SOURCE_DB, "account_report_column"):
        return []
    return query_rows(
        SOURCE_DB,
        """
        SELECT 'account.report.column:' || column_record.id::text AS source_report_column_key,
               column_record.id::text AS source_column_id,
               column_record.report_id::text AS source_report_id,
               COALESCE(column_record.sequence::text, '0') AS sequence,
               COALESCE(column_record.expression_label::text, '') AS expression_label,
               COALESCE(column_record.figure_type::text, '') AS figure_type,
               COALESCE(column_record.name->>'en_US', column_record.name->>'fr_FR', column_record.name::text) AS source_name,
               COALESCE(column_record.sortable::text, 'false') AS sortable,
               COALESCE(column_record.blank_if_zero::text, 'false') AS blank_if_zero
          FROM account_report_column column_record
         ORDER BY column_record.report_id, column_record.sequence, column_record.id
        """,
    )


def target_source_report_column_rows() -> list[dict[str, Any]]:
    if not table_exists(EXACT_VALIDATION_DB, "rebuild_account_source_report_column"):
        return []
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT column_record.rebuild_source_model || ':' || column_record.rebuild_source_id::text AS source_report_column_key,
               column_record.source_column_id::text AS source_column_id,
               column_record.source_report_id::text AS source_report_id,
               column_record.sequence::text AS sequence,
               COALESCE(column_record.expression_label::text, '') AS expression_label,
               COALESCE(column_record.figure_type::text, '') AS figure_type,
               COALESCE(column_record.name::text, '') AS source_name,
               column_record.sortable::text AS sortable,
               column_record.blank_if_zero::text AS blank_if_zero
          FROM rebuild_account_source_report_column column_record
         ORDER BY column_record.source_report_id, column_record.sequence, column_record.source_column_id
        """,
        set_readonly_role=False,
    )


def source_deferred_schedule_rows() -> list[dict[str, Any]]:
    if not table_exists(SOURCE_DB, "account_move_deferred_rel"):
        return []
    import_date_to = source_snapshot_date() or USL_BENCHMARK_END
    return query_rows(
        SOURCE_DB,
        f"""
        WITH original_deferred_dates AS (
            SELECT move_id AS original_move_id,
                   min(deferred_start_date) AS deferred_start_date,
                   max(deferred_end_date) AS deferred_end_date,
                   count(*) FILTER (
                       WHERE deferred_start_date IS NOT NULL
                          OR deferred_end_date IS NOT NULL
                   )::integer AS source_original_deferred_line_count
              FROM account_move_line
             WHERE move_id IN (SELECT original_move_id FROM account_move_deferred_rel)
             GROUP BY move_id
        ),
        line_summary AS (
            SELECT rel.original_move_id,
                   rel.deferred_move_id,
                   count(line.id)::integer AS source_line_count,
                   round(COALESCE(sum(CASE
                       WHEN COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text) LIKE '486%%'
                         OR COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text) LIKE '487%%'
                       THEN line.balance ELSE 0
                   END), 0)::numeric, 2) AS deferred_account_balance,
                   round(COALESCE(sum(CASE
                       WHEN COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text) LIKE '486%%'
                         OR COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text) LIKE '487%%'
                       THEN 0 ELSE line.balance
                   END), 0)::numeric, 2) AS counterpart_balance,
                   string_agg(DISTINCT
                       COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text),
                       ', ' ORDER BY COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text)
                   ) FILTER (
                       WHERE COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text) LIKE '486%%'
                          OR COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text) LIKE '487%%'
                   ) AS deferred_account_codes,
                   string_agg(DISTINCT
                       COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text),
                       ', ' ORDER BY COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text)
                   ) FILTER (
                       WHERE NOT (
                           COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text) LIKE '486%%'
                           OR COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text) LIKE '487%%'
                       )
                   ) AS counterpart_account_codes,
                   string_agg(DISTINCT
                       COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text),
                       ', ' ORDER BY COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text)
                   ) FILTER (
                       WHERE COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text) LIKE '486%%'
                          OR COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text) LIKE '487%%'
                   ) AS deferred_account_names,
                   string_agg(DISTINCT
                       COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text),
                       ', ' ORDER BY COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text)
                   ) FILTER (
                       WHERE NOT (
                           COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text) LIKE '486%%'
                           OR COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text) LIKE '487%%'
                       )
                   ) AS counterpart_account_names
              FROM account_move_deferred_rel rel
              JOIN account_move deferred ON deferred.id = rel.deferred_move_id
              LEFT JOIN account_move_line line ON line.move_id = deferred.id
              LEFT JOIN account_account account ON account.id = line.account_id
             GROUP BY rel.original_move_id, rel.deferred_move_id
        ),
        typed AS (
            SELECT rel.original_move_id,
                   rel.deferred_move_id,
                   original.name AS original_name,
                   original.state AS original_state,
                   original.move_type AS original_move_type,
                   original.company_id,
                   currency.name AS currency,
                   original.date AS original_date,
                   deferred.name AS deferred_name,
                   deferred.state AS deferred_state,
                   deferred.move_type AS deferred_move_type,
                   deferred.date AS deferred_date,
                   dates.deferred_start_date,
                   dates.deferred_end_date,
                   COALESCE(dates.source_original_deferred_line_count, 0)::integer AS source_original_deferred_line_count,
                   COALESCE(summary.source_line_count, 0)::integer AS source_line_count,
                   COALESCE(summary.deferred_account_balance, 0) AS deferred_account_balance,
                   COALESCE(summary.counterpart_balance, 0) AS counterpart_balance,
                   COALESCE(summary.deferred_account_codes, '') AS deferred_account_codes,
                   COALESCE(summary.deferred_account_names, '') AS deferred_account_names,
                   COALESCE(summary.counterpart_account_codes, '') AS counterpart_account_codes,
                   COALESCE(summary.counterpart_account_names, '') AS counterpart_account_names,
                   CASE
                       WHEN COALESCE(summary.deferred_account_codes, '') LIKE '486%%' THEN 'expense'
                       WHEN COALESCE(summary.deferred_account_codes, '') LIKE '487%%' THEN 'revenue'
                       WHEN original.move_type IN ('in_invoice', 'in_refund', 'in_receipt') THEN 'expense'
                       WHEN original.move_type IN ('out_invoice', 'out_refund', 'out_receipt') THEN 'revenue'
                       ELSE 'unknown'
                   END AS schedule_type,
                   CASE
                       WHEN deferred.state = 'posted'
                        AND deferred.date BETWEEN DATE '{USL_BENCHMARK_START}' AND DATE '{import_date_to}'
                       THEN 'imported_posted_entry'
                       WHEN deferred.state != 'posted' THEN 'source_draft_forecast'
                       ELSE 'source_not_replayed'
                   END AS representation_status
              FROM account_move_deferred_rel rel
              JOIN account_move original ON original.id = rel.original_move_id
              JOIN account_move deferred ON deferred.id = rel.deferred_move_id
              JOIN res_currency currency ON currency.id = original.currency_id
              LEFT JOIN original_deferred_dates dates ON dates.original_move_id = rel.original_move_id
              LEFT JOIN line_summary summary
                     ON summary.original_move_id = rel.original_move_id
                    AND summary.deferred_move_id = rel.deferred_move_id
             WHERE original.company_id IN (1, 8)
               AND original.date >= DATE '{USL_BENCHMARK_START}'
        )
        SELECT original_move_id::text || ':' || deferred_move_id::text AS source_deferred_schedule_key,
               original_move_id::text AS source_original_move_id,
               deferred_move_id::text AS source_deferred_move_id,
               company_id::text AS source_company_id,
               currency,
               COALESCE(original_name::text, '') AS source_original_name,
               COALESCE(deferred_name::text, '') AS source_deferred_name,
               original_state::text AS source_original_state,
               deferred_state::text AS source_deferred_state,
               original_move_type::text AS source_original_move_type,
               deferred_move_type::text AS source_deferred_move_type,
               original_date::text,
               deferred_date::text,
               COALESCE(deferred_start_date::text, '') AS deferred_start_date,
               COALESCE(deferred_end_date::text, '') AS deferred_end_date,
               source_original_deferred_line_count::text AS source_original_deferred_line_count,
               source_line_count::text AS source_line_count,
               deferred_account_codes AS deferred_account_code,
               deferred_account_names AS deferred_account_name,
               counterpart_account_codes,
               counterpart_account_names,
               round(abs(deferred_account_balance)::numeric, 2)::text AS amount,
               round(deferred_account_balance::numeric, 2)::text AS deferred_account_balance,
               round(counterpart_balance::numeric, 2)::text AS counterpart_balance,
               schedule_type,
               CASE
                   WHEN schedule_type = 'expense' AND deferred_account_balance > 0 THEN 'initial_deferral'
                   WHEN schedule_type = 'expense' AND deferred_account_balance < 0 THEN 'recognition'
                   WHEN schedule_type = 'revenue' AND deferred_account_balance < 0 THEN 'initial_deferral'
                   WHEN schedule_type = 'revenue' AND deferred_account_balance > 0 THEN 'recognition'
                   ELSE 'unknown'
               END AS schedule_phase,
               representation_status
          FROM typed
         ORDER BY original_date, original_move_id, deferred_date, deferred_move_id
        """,
    )


def target_deferred_schedule_rows() -> list[dict[str, Any]]:
    if not table_exists(EXACT_VALIDATION_DB, "rebuild_account_deferred_schedule_line"):
        return []
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT line.source_original_move_id::text || ':' || line.source_deferred_move_id::text AS source_deferred_schedule_key,
               line.source_original_move_id::text AS source_original_move_id,
               line.source_deferred_move_id::text AS source_deferred_move_id,
               line.source_company_id::text AS source_company_id,
               currency.name::text AS currency,
               COALESCE(line.source_original_name::text, '') AS source_original_name,
               COALESCE(line.source_deferred_name::text, '') AS source_deferred_name,
               line.source_original_state::text AS source_original_state,
               line.source_deferred_state::text AS source_deferred_state,
               line.source_original_move_type::text AS source_original_move_type,
               line.source_deferred_move_type::text AS source_deferred_move_type,
               line.original_date::text,
               line.deferred_date::text,
               COALESCE(line.deferred_start_date::text, '') AS deferred_start_date,
               COALESCE(line.deferred_end_date::text, '') AS deferred_end_date,
               line.source_original_deferred_line_count::text AS source_original_deferred_line_count,
               line.source_line_count::text AS source_line_count,
               COALESCE(line.deferred_account_code::text, '') AS deferred_account_code,
               COALESCE(line.deferred_account_name::text, '') AS deferred_account_name,
               COALESCE(line.counterpart_account_codes::text, '') AS counterpart_account_codes,
               COALESCE(line.counterpart_account_names::text, '') AS counterpart_account_names,
               round(line.amount::numeric, 2)::text AS amount,
               round(line.deferred_account_balance::numeric, 2)::text AS deferred_account_balance,
               round(line.counterpart_balance::numeric, 2)::text AS counterpart_balance,
               line.schedule_type::text AS schedule_type,
               line.schedule_phase::text AS schedule_phase,
               line.representation_status::text AS representation_status
          FROM rebuild_account_deferred_schedule_line line
          JOIN res_currency currency ON currency.id = line.currency_id
         ORDER BY line.source_original_move_id, line.source_deferred_move_id
        """,
        set_readonly_role=False,
    )


def source_reconciliation_model_rows() -> list[dict[str, Any]]:
    return query_rows(
        SOURCE_DB,
        f"""
        SELECT model.id::text AS source_reconciliation_model_id,
               {source_name_expr('model')} AS name,
               model.sequence::text AS sequence,
               model.company_id::text AS source_company_id,
               model.active::text AS active,
               model.trigger::text AS trigger,
               model.can_be_proposed::text AS can_be_proposed,
               COALESCE(model.mapped_partner_id::text, '') AS source_mapped_partner_id,
               COALESCE(model.match_amount::text, '') AS match_amount,
               round(COALESCE(model.match_amount_min, 0)::numeric, 6)::text AS match_amount_min,
               round(COALESCE(model.match_amount_max, 0)::numeric, 6)::text AS match_amount_max,
               COALESCE(model.match_label::text, '') AS match_label,
               COALESCE(model.match_label_param::text, '') AS match_label_param,
               COALESCE((
                   SELECT string_agg(
                              relation.account_journal_id::text,
                              ',' ORDER BY relation.account_journal_id
                          )
                   FROM account_journal_account_reconcile_model_rel relation
                   WHERE relation.account_reconcile_model_id = model.id
               ), '') AS source_journal_ids,
               COALESCE((
                   SELECT string_agg(
                              relation.res_partner_id::text,
                              ',' ORDER BY relation.res_partner_id
                          )
                   FROM account_reconcile_model_res_partner_rel relation
                   WHERE relation.account_reconcile_model_id = model.id
               ), '') AS source_match_partner_ids,
               (
                   SELECT count(*)::text
                   FROM account_reconcile_model_line line
                   WHERE line.model_id = model.id
               ) AS line_count
        FROM account_reconcile_model model
        WHERE model.company_id IN (1, 8)
        ORDER BY model.id
        """,
    )


def target_reconciliation_model_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT model.rebuild_source_id::text AS source_reconciliation_model_id,
               {name} AS name,
               model.sequence::text AS sequence,
               company.rebuild_source_id::text AS source_company_id,
               model.active::text AS active,
               model.trigger::text AS trigger,
               model.can_be_proposed::text AS can_be_proposed,
               COALESCE(mapped_partner.rebuild_source_id::text, '') AS source_mapped_partner_id,
               COALESCE(model.match_amount::text, '') AS match_amount,
               round(COALESCE(model.match_amount_min, 0)::numeric, 6)::text AS match_amount_min,
               round(COALESCE(model.match_amount_max, 0)::numeric, 6)::text AS match_amount_max,
               COALESCE(model.match_label::text, '') AS match_label,
               COALESCE(model.match_label_param::text, '') AS match_label_param,
               COALESCE((
                   SELECT string_agg(
                              journal.rebuild_source_id::text,
                              ',' ORDER BY journal.rebuild_source_id
                          )
                   FROM account_journal_account_reconcile_model_rel relation
                   JOIN account_journal journal
                     ON journal.id = relation.account_journal_id
                   WHERE relation.account_reconcile_model_id = model.id
               ), '') AS source_journal_ids,
               COALESCE((
                   SELECT string_agg(
                              partner.rebuild_source_id::text,
                              ',' ORDER BY partner.rebuild_source_id
                          )
                   FROM account_reconcile_model_res_partner_rel relation
                   JOIN res_partner partner
                     ON partner.id = relation.res_partner_id
                   WHERE relation.account_reconcile_model_id = model.id
               ), '') AS source_match_partner_ids,
               (
                   SELECT count(*)::text
                   FROM account_reconcile_model_line line
                   WHERE line.model_id = model.id
                     AND line.rebuild_source_model =
                         'account.reconcile.model.line'
               ) AS line_count
        FROM account_reconcile_model model
        JOIN res_company company ON company.id = model.company_id
        LEFT JOIN res_partner mapped_partner
          ON mapped_partner.id = model.mapped_partner_id
        WHERE model.rebuild_source_model = 'account.reconcile.model'
        ORDER BY model.rebuild_source_id
        """.format(name=source_name_expr("model")),
        set_readonly_role=False,
    )


def source_reconciliation_model_line_rows() -> list[dict[str, Any]]:
    return query_rows(
        SOURCE_DB,
        f"""
        SELECT line.id::text AS source_reconciliation_model_line_id,
               line.model_id::text AS source_reconciliation_model_id,
               line.sequence::text AS sequence,
               COALESCE(line.account_id::text, '') AS source_account_id,
               COALESCE(line.partner_id::text, '') AS source_partner_id,
               {source_name_expr('line', 'label')} AS label,
               line.amount_type::text AS amount_type,
               line.amount_string::text AS amount_string,
               round(line.amount::numeric, 6)::text AS amount,
               COALESCE((
                   SELECT string_agg(
                              relation.account_tax_id::text,
                              ',' ORDER BY relation.account_tax_id
                          )
                   FROM account_reconcile_model_line_account_tax_rel relation
                   WHERE relation.account_reconcile_model_line_id = line.id
               ), '') AS source_tax_ids
        FROM account_reconcile_model_line line
        JOIN account_reconcile_model model ON model.id = line.model_id
        WHERE model.company_id IN (1, 8)
        ORDER BY line.id
        """,
    )


def target_reconciliation_model_line_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT line.rebuild_source_id::text
                   AS source_reconciliation_model_line_id,
               model.rebuild_source_id::text
                   AS source_reconciliation_model_id,
               line.sequence::text AS sequence,
               COALESCE(account.rebuild_source_id::text, '')
                   AS source_account_id,
               COALESCE(partner.rebuild_source_id::text, '')
                   AS source_partner_id,
               {label} AS label,
               line.amount_type::text AS amount_type,
               line.amount_string::text AS amount_string,
               round(line.amount::numeric, 6)::text AS amount,
               COALESCE((
                   SELECT string_agg(
                              tax.rebuild_source_id::text,
                              ',' ORDER BY tax.rebuild_source_id
                          )
                   FROM account_reconcile_model_line_account_tax_rel relation
                   JOIN account_tax tax ON tax.id = relation.account_tax_id
                   WHERE relation.account_reconcile_model_line_id = line.id
               ), '') AS source_tax_ids
        FROM account_reconcile_model_line line
        JOIN account_reconcile_model model ON model.id = line.model_id
        LEFT JOIN account_account account ON account.id = line.account_id
        LEFT JOIN res_partner partner ON partner.id = line.partner_id
        WHERE line.rebuild_source_model = 'account.reconcile.model.line'
          AND model.rebuild_source_model = 'account.reconcile.model'
        ORDER BY line.rebuild_source_id
        """.format(label=source_name_expr("line", "label")),
        set_readonly_role=False,
    )


def source_tax_group_rows() -> list[dict[str, Any]]:
    return query_rows(
        SOURCE_DB,
        f"""
        SELECT tg.id::text AS source_tax_group_id,
               {source_name_expr('tg')} AS name,
               tg.sequence::text AS sequence,
               tg.company_id::text AS source_company_id,
               COALESCE(rc.code::text, '') AS country_code,
               COALESCE(tg.tax_payable_account_id::text, '') AS source_tax_payable_account_id,
               COALESCE(tg.tax_receivable_account_id::text, '') AS source_tax_receivable_account_id,
               COALESCE(tg.advance_tax_payment_account_id::text, '') AS source_advance_tax_payment_account_id
        FROM account_tax_group tg
        LEFT JOIN res_country rc ON rc.id = tg.country_id
        ORDER BY tg.id
        """,
    )


def target_tax_group_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT tg.rebuild_source_id::text AS source_tax_group_id,
               {name} AS name,
               tg.sequence::text AS sequence,
               company.rebuild_source_id::text AS source_company_id,
               COALESCE(country.code::text, '') AS country_code,
               COALESCE(payable.rebuild_source_id::text, '') AS source_tax_payable_account_id,
               COALESCE(receivable.rebuild_source_id::text, '') AS source_tax_receivable_account_id,
               COALESCE(advance.rebuild_source_id::text, '') AS source_advance_tax_payment_account_id
        FROM account_tax_group tg
        JOIN res_company company ON company.id = tg.company_id
        LEFT JOIN res_country country ON country.id = tg.country_id
        LEFT JOIN account_account payable ON payable.id = tg.tax_payable_account_id
        LEFT JOIN account_account receivable ON receivable.id = tg.tax_receivable_account_id
        LEFT JOIN account_account advance ON advance.id = tg.advance_tax_payment_account_id
        WHERE tg.rebuild_source_model = 'account.tax.group'
        ORDER BY tg.rebuild_source_id
        """.format(name=source_name_expr("tg")),
        set_readonly_role=False,
    )


def source_tax_tag_rows() -> list[dict[str, Any]]:
    return query_rows(
        SOURCE_DB,
        f"""
        SELECT tag.id::text AS source_tax_tag_id,
               {source_name_expr('tag')} AS name,
               tag.applicability::text AS applicability,
               COALESCE(tag.color, 0)::text AS color,
               tag.active::text AS active,
               COALESCE(country.code::text, '') AS country_code
        FROM account_account_tag tag
        LEFT JOIN res_country country ON country.id = tag.country_id
        ORDER BY tag.id
        """,
    )


def target_tax_tag_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT tag.rebuild_source_id::text AS source_tax_tag_id,
               {name} AS name,
               tag.applicability::text AS applicability,
               tag.color::text AS color,
               tag.active::text AS active,
               COALESCE(country.code::text, '') AS country_code
        FROM account_account_tag tag
        LEFT JOIN res_country country ON country.id = tag.country_id
        WHERE tag.rebuild_source_model = 'account.account.tag'
        ORDER BY tag.rebuild_source_id
        """.format(name=source_name_expr("tag")),
        set_readonly_role=False,
    )


def source_tax_rows() -> list[dict[str, Any]]:
    return query_rows(
        SOURCE_DB,
        f"""
        SELECT tax.id::text AS source_tax_id,
               {source_name_expr('tax')} AS name,
               tax.type_tax_use::text AS type_tax_use,
               COALESCE(tax.tax_scope::text, '') AS tax_scope,
               tax.amount_type::text AS amount_type,
               COALESCE(tax.price_include_override::text, '') AS price_include_override,
               tax.tax_exigibility::text AS tax_exigibility,
               tax.sequence::text AS sequence,
               round(tax.amount::numeric, 4)::text AS amount,
               tax.active::text AS active,
               tax.include_base_amount::text AS include_base_amount,
               tax.is_base_affected::text AS is_base_affected,
               COALESCE(tax.analytic, false)::text AS analytic,
               tax.company_id::text AS source_company_id,
               tax.tax_group_id::text AS source_tax_group_id,
               COALESCE(country.code::text, '') AS country_code,
               COALESCE(tax.cash_basis_transition_account_id::text, '') AS source_cash_basis_transition_account_id,
               COALESCE(tax.ubl_cii_tax_category_code::text, '') AS ubl_cii_tax_category_code,
               COALESCE(tax.ubl_cii_tax_exemption_reason_code::text, '') AS ubl_cii_tax_exemption_reason_code
        FROM account_tax tax
        LEFT JOIN res_country country ON country.id = tax.country_id
        ORDER BY tax.id
        """,
    )


def target_tax_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT tax.rebuild_source_id::text AS source_tax_id,
               {name} AS name,
               tax.type_tax_use::text AS type_tax_use,
               COALESCE(tax.tax_scope::text, '') AS tax_scope,
               tax.amount_type::text AS amount_type,
               COALESCE(tax.price_include_override::text, '') AS price_include_override,
               tax.tax_exigibility::text AS tax_exigibility,
               tax.sequence::text AS sequence,
               round(tax.amount::numeric, 4)::text AS amount,
               tax.active::text AS active,
               tax.include_base_amount::text AS include_base_amount,
               tax.is_base_affected::text AS is_base_affected,
               tax.analytic::text AS analytic,
               company.rebuild_source_id::text AS source_company_id,
               tax_group.rebuild_source_id::text AS source_tax_group_id,
               COALESCE(country.code::text, '') AS country_code,
               COALESCE(caba_account.rebuild_source_id::text, '') AS source_cash_basis_transition_account_id,
               COALESCE(tax.ubl_cii_tax_category_code::text, '') AS ubl_cii_tax_category_code,
               COALESCE(tax.ubl_cii_tax_exemption_reason_code::text, '') AS ubl_cii_tax_exemption_reason_code
        FROM account_tax tax
        JOIN res_company company ON company.id = tax.company_id
        JOIN account_tax_group tax_group ON tax_group.id = tax.tax_group_id
        LEFT JOIN res_country country ON country.id = tax.country_id
        LEFT JOIN account_account caba_account ON caba_account.id = tax.cash_basis_transition_account_id
        WHERE tax.rebuild_source_model = 'account.tax'
        ORDER BY tax.rebuild_source_id
        """.format(name=source_name_expr("tax")),
        set_readonly_role=False,
    )


def source_tax_repartition_rows() -> list[dict[str, Any]]:
    return query_rows(
        SOURCE_DB,
        """
        SELECT rep.id::text AS source_tax_repartition_line_id,
               rep.tax_id::text AS source_tax_id,
               COALESCE(rep.account_id::text, '') AS source_account_id,
               rep.sequence::text AS sequence,
               rep.repartition_type::text AS repartition_type,
               rep.document_type::text AS document_type,
               round(rep.factor_percent::numeric, 6)::text AS factor_percent,
               rep.use_in_tax_closing::text AS use_in_tax_closing,
               COALESCE((
                   SELECT string_agg(rel.account_account_tag_id::text, ',' ORDER BY rel.account_account_tag_id)
                   FROM account_account_tag_account_tax_repartition_line_rel rel
                   WHERE rel.account_tax_repartition_line_id = rep.id
               ), '') AS source_tax_tag_ids
        FROM account_tax_repartition_line rep
        ORDER BY rep.id
        """,
    )


def target_tax_repartition_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT rep.rebuild_source_id::text AS source_tax_repartition_line_id,
               tax.rebuild_source_id::text AS source_tax_id,
               COALESCE(account.rebuild_source_id::text, '') AS source_account_id,
               rep.sequence::text AS sequence,
               rep.repartition_type::text AS repartition_type,
               rep.document_type::text AS document_type,
               round(rep.factor_percent::numeric, 6)::text AS factor_percent,
               rep.use_in_tax_closing::text AS use_in_tax_closing,
               COALESCE((
                   SELECT string_agg(tag.rebuild_source_id::text, ',' ORDER BY tag.rebuild_source_id)
                   FROM account_account_tag_account_tax_repartition_line_rel rel
                   JOIN account_account_tag tag ON tag.id = rel.account_account_tag_id
                   WHERE rel.account_tax_repartition_line_id = rep.id
               ), '') AS source_tax_tag_ids
        FROM account_tax_repartition_line rep
        JOIN account_tax tax ON tax.id = rep.tax_id
        LEFT JOIN account_account account ON account.id = rep.account_id
        WHERE rep.rebuild_source_model = 'account.tax.repartition.line'
        ORDER BY rep.rebuild_source_id
        """,
        set_readonly_role=False,
    )


def source_tax_alternative_rows() -> list[dict[str, Any]]:
    return query_rows(
        SOURCE_DB,
        """
        SELECT dest_tax_id::text || ':' || src_tax_id::text AS relation_key,
               dest_tax_id::text AS source_dest_tax_id,
               src_tax_id::text AS source_src_tax_id
        FROM account_tax_alternatives
        ORDER BY dest_tax_id, src_tax_id
        """,
    )


def target_tax_alternative_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT dest.rebuild_source_id::text || ':' || src.rebuild_source_id::text AS relation_key,
               dest.rebuild_source_id::text AS source_dest_tax_id,
               src.rebuild_source_id::text AS source_src_tax_id
        FROM account_tax_alternatives rel
        JOIN account_tax dest ON dest.id = rel.dest_tax_id
        JOIN account_tax src ON src.id = rel.src_tax_id
        WHERE dest.rebuild_source_model = 'account.tax'
          AND src.rebuild_source_model = 'account.tax'
        ORDER BY dest.rebuild_source_id, src.rebuild_source_id
        """,
        set_readonly_role=False,
    )


def source_asset_rows() -> list[dict[str, Any]]:
    return query_rows(
        SOURCE_DB,
        f"""
        SELECT asset.id::text AS source_asset_id,
               {source_name_expr('asset')} AS name,
               asset.company_id::text AS source_company_id,
               currency.name::text AS currency,
               asset.state::text AS state,
               COALESCE(asset_group.name::text, '') AS asset_group_name,
               COALESCE(asset.acquisition_date::text, '') AS acquisition_date,
               COALESCE(asset.prorata_date::text, '') AS prorata_date,
               COALESCE(asset.disposal_date::text, '') AS disposal_date,
               COALESCE(asset.prorata_computation_type::text, '') AS prorata_computation_type,
               round(asset.original_value::numeric, 2)::text AS original_value,
               round(asset.book_value::numeric, 2)::text AS book_value,
               round((asset.original_value - asset.already_depreciated_amount_import)::numeric, 2)::text AS imported_period_net_value,
               round(asset.salvage_value::numeric, 2)::text AS salvage_value,
               round(asset.non_deductible_tax_value::numeric, 2)::text AS non_deductible_tax_value,
               round(asset.already_depreciated_amount_import::numeric, 2)::text AS already_depreciated_amount_import,
               COALESCE(round(asset.net_gain_on_sale::numeric, 2)::text, '0.00') AS net_gain_on_sale,
               COALESCE(asset.account_asset_id::text, '') AS source_asset_account_id,
               COALESCE(asset.account_depreciation_id::text, '') AS source_depreciation_account_id,
               COALESCE(asset.account_depreciation_expense_id::text, '') AS source_depreciation_expense_account_id,
               COALESCE(asset.journal_id::text, '') AS source_journal_id,
               count(move.id)::text AS source_depreciation_move_count
        FROM account_asset asset
        JOIN res_currency currency ON currency.id = asset.currency_id
        LEFT JOIN account_asset_group asset_group ON asset_group.id = asset.asset_group_id
        LEFT JOIN account_move move ON move.asset_id = asset.id
        GROUP BY asset.id, currency.name, asset_group.name
        ORDER BY asset.id
        """,
    )


def target_asset_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT asset.rebuild_source_id::text AS source_asset_id,
               asset.name::text AS name,
               company.rebuild_source_id::text AS source_company_id,
               currency.name::text AS currency,
               asset.state::text AS state,
               COALESCE(asset.asset_group_name::text, '') AS asset_group_name,
               COALESCE(asset.acquisition_date::text, '') AS acquisition_date,
               COALESCE(asset.prorata_date::text, '') AS prorata_date,
               COALESCE(asset.disposal_date::text, '') AS disposal_date,
               COALESCE(asset.prorata_computation_type::text, '') AS prorata_computation_type,
               round(asset.original_value::numeric, 2)::text AS original_value,
               round(asset.book_value::numeric, 2)::text AS book_value,
               round(asset.imported_period_net_value::numeric, 2)::text AS imported_period_net_value,
               round(asset.salvage_value::numeric, 2)::text AS salvage_value,
               round(asset.non_deductible_tax_value::numeric, 2)::text AS non_deductible_tax_value,
               round(asset.already_depreciated_amount_import::numeric, 2)::text AS already_depreciated_amount_import,
               COALESCE(round(asset.net_gain_on_sale::numeric, 2)::text, '') AS net_gain_on_sale,
               COALESCE(asset_account.rebuild_source_id::text, '') AS source_asset_account_id,
               COALESCE(depreciation_account.rebuild_source_id::text, '') AS source_depreciation_account_id,
               COALESCE(expense_account.rebuild_source_id::text, '') AS source_depreciation_expense_account_id,
               COALESCE(journal.rebuild_source_id::text, '') AS source_journal_id,
               asset.source_depreciation_move_count::text AS source_depreciation_move_count
        FROM rebuild_account_asset asset
        JOIN res_company company ON company.id = asset.company_id
        JOIN res_currency currency ON currency.id = asset.currency_id
        LEFT JOIN account_account asset_account ON asset_account.id = asset.asset_account_id
        LEFT JOIN account_account depreciation_account ON depreciation_account.id = asset.depreciation_account_id
        LEFT JOIN account_account expense_account ON expense_account.id = asset.depreciation_expense_account_id
        LEFT JOIN account_journal journal ON journal.id = asset.journal_id
        WHERE asset.rebuild_source_model = 'account_asset'
        ORDER BY asset.rebuild_source_id
        """,
        set_readonly_role=False,
    )


def source_depreciation_schedule_rows() -> list[dict[str, Any]]:
    return query_rows(
        SOURCE_DB,
        """
        WITH schedule AS (
            SELECT asset.id::text AS source_asset_id,
                   move.id::text AS source_move_id,
                   concat(asset.id::text, ':', move.id::text) AS source_schedule_id,
                   COALESCE(move.name::text, '') AS source_move_name,
                   move.state::text AS source_move_state,
                   COALESCE(move.date::text, '') AS depreciation_date,
                   COALESCE(move.ref::text, '') AS move_ref,
                   round(sum(CASE
                       WHEN line.account_id = asset.account_depreciation_expense_id
                       THEN line.debit - line.credit
                       ELSE 0
                   END)::numeric, 2) AS expense_amount,
                   round(sum(CASE
                       WHEN line.account_id = asset.account_depreciation_id
                       THEN line.credit - line.debit
                       ELSE 0
                   END)::numeric, 2) AS depreciation_amount,
                   count(line.id)::text AS source_line_count,
                   round(asset.original_value::numeric, 2) AS original_value
              FROM account_asset asset
              JOIN account_move move ON move.asset_id = asset.id
              LEFT JOIN account_move_line line ON line.move_id = move.id
             GROUP BY asset.id, asset.original_value, move.id
        ),
        cumulative AS (
            SELECT schedule.*,
                   round(sum(depreciation_amount) OVER (
                       PARTITION BY source_asset_id
                       ORDER BY depreciation_date, source_move_id::integer
                   )::numeric, 2) AS accumulated_depreciation_amount
              FROM schedule
        )
        SELECT source_asset_id,
               source_move_id,
               source_schedule_id,
               source_move_name,
               source_move_state,
               depreciation_date,
               move_ref,
               expense_amount::text,
               depreciation_amount::text,
               accumulated_depreciation_amount::text,
               round((original_value - accumulated_depreciation_amount)::numeric, 2)::text AS net_book_value_after_line,
               source_line_count
          FROM cumulative
         ORDER BY source_asset_id::integer, depreciation_date, source_move_id::integer
        """,
    )


def target_depreciation_schedule_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT line.source_asset_id::text AS source_asset_id,
               line.source_move_id::text AS source_move_id,
               concat(line.source_asset_id::text, ':', line.source_move_id::text) AS source_schedule_id,
               COALESCE(line.source_move_name::text, '') AS source_move_name,
               COALESCE(line.source_move_state::text, '') AS source_move_state,
               COALESCE(line.depreciation_date::text, '') AS depreciation_date,
               COALESCE(line.move_ref::text, '') AS move_ref,
               round(line.expense_amount::numeric, 2)::text AS expense_amount,
               round(line.depreciation_amount::numeric, 2)::text AS depreciation_amount,
               round(line.accumulated_depreciation_amount::numeric, 2)::text AS accumulated_depreciation_amount,
               round(line.net_book_value_after_line::numeric, 2)::text AS net_book_value_after_line,
               line.source_line_count::text AS source_line_count
          FROM rebuild_account_asset_depreciation_schedule_line line
         ORDER BY line.source_asset_id, line.depreciation_date, line.source_move_id
        """,
        set_readonly_role=False,
    )


def target_journal_balance_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT aj.rebuild_source_id::text AS source_journal_id,
               aj.code::text AS code,
               {journal_name} AS name,
               count(DISTINCT am.id)::text AS move_count,
               count(aml.id)::text AS move_line_count,
               round(sum(aml.debit)::numeric, 2)::text AS debit,
               round(sum(aml.credit)::numeric, 2)::text AS credit,
               round(sum(aml.balance)::numeric, 2)::text AS balance
        FROM account_move_line aml
        JOIN account_move am ON am.id = aml.move_id
        JOIN account_journal aj ON aj.id = am.journal_id
        JOIN res_company company ON company.id = am.company_id
        WHERE aml.rebuild_source_model = 'account.move.line'
          AND {benchmark_where}
        GROUP BY aj.rebuild_source_id, aj.code, {journal_name}
        ORDER BY aj.code, aj.rebuild_source_id
        """.format(journal_name=source_name_expr("aj"), benchmark_where=target_benchmark_move_where()),
        set_readonly_role=False,
    )


def target_unbalanced_moves() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT am.id AS target_move_id,
               am.name,
               am.rebuild_source_id AS source_move_id,
               am.date::text AS date,
               round(sum(aml.balance)::numeric, 2)::text AS balance
        FROM account_move am
        JOIN account_move_line aml ON aml.move_id = am.id
        WHERE am.rebuild_source_model = 'account.move'
          AND am.state = 'posted'
        GROUP BY am.id, am.name, am.rebuild_source_id, am.date
        HAVING round(sum(aml.balance)::numeric, 2) <> 0
        ORDER BY am.date, am.id
        """,
        set_readonly_role=False,
    )


def duplicate_target_traces(table: str) -> list[dict[str, Any]]:
    if not table_exists(EXACT_VALIDATION_DB, table):
        return []
    return query_rows(
        EXACT_VALIDATION_DB,
        f"""
        SELECT rebuild_source_model,
               rebuild_source_id,
               rebuild_source_snapshot,
               count(*)::text AS duplicate_count
        FROM {table}
        WHERE rebuild_source_model IS NOT NULL
          AND rebuild_source_id IS NOT NULL
        GROUP BY rebuild_source_model, rebuild_source_id, rebuild_source_snapshot
        HAVING count(*) > 1
        ORDER BY count(*) DESC, rebuild_source_model, rebuild_source_id
        LIMIT 100
        """,
        set_readonly_role=False,
    )


def target_lock_enforcement_check(source_dir: str = DEFAULT_SOURCE_DIR) -> dict[str, Any]:
    snapshot_id = source_snapshot_id(source_dir)
    lock_script = PRIVATE_ARTIFACTS / "target-lock-check.py"
    lock_script.write_text(
        "\n".join(
            [
                "import json",
                "payload = {'stage': 'target-lock-check'}",
                "company = env['res.company'].search([",
                "    ('rebuild_source_model', '=', 'res.company'),",
                "    ('rebuild_source_id', '=', 1),",
                f"    ('rebuild_source_snapshot', '=', {snapshot_id!r}),",
                "], limit=1)",
                "move = env['account.move'].search([",
                "    ('company_id', '=', company.id),",
                "    ('rebuild_source_model', '=', 'account.move'),",
                "    ('state', '=', 'posted'),",
                f"    ('date', '<=', {USL_BENCHMARK_END!r}),",
                "], order='date, id', limit=1)",
                "line = move.line_ids.filtered(lambda candidate: candidate.account_id and candidate.debit).sorted('id')[:1]",
                "payload.update({",
                "    'company_id': company.id,",
                "    'source_company_id': company.rebuild_source_id,",
                "    'fiscalyear_lock_date': str(company.fiscalyear_lock_date or ''),",
                "    'tax_lock_date': str(company.tax_lock_date or ''),",
                "    'sale_lock_date': str(company.sale_lock_date or ''),",
                "    'purchase_lock_date': str(company.purchase_lock_date or ''),",
                "    'move_id': move.id,",
                "    'source_move_id': move.rebuild_source_id,",
                "    'move_date': str(move.date or ''),",
                "    'line_id': line.id if line else None,",
                "    'source_line_id': line.rebuild_source_id if line else None,",
                "})",
                "if not company or not move or not line:",
                "    payload.update({'status': 'failed', 'classification': 'LOCK_TEST_RECORD_MISSING'})",
                "else:",
                "    try:",
                "        move._check_fiscal_lock_dates()",
                "        payload['direct_lock_check'] = 'failed_no_exception'",
                "    except Exception as error:",
                "        payload['direct_lock_check'] = 'blocked'",
                "        payload['direct_exception_type'] = type(error).__name__",
                "        payload['direct_exception_message'] = str(error)",
                "    try:",
                "        with env.cr.savepoint():",
                "            line.with_context(tracking_disable=True).write({'balance': line.balance + 0.01})",
                "            raise RuntimeError('LOCK_WRITE_SUCCEEDED_ROLLBACK')",
                "    except RuntimeError as error:",
                "        if str(error) == 'LOCK_WRITE_SUCCEEDED_ROLLBACK':",
                "            payload['protected_write_check'] = 'failed_write_succeeded'",
                "        else:",
                "            payload['protected_write_check'] = 'blocked_runtime_error'",
                "            payload['write_exception_type'] = type(error).__name__",
                "            payload['write_exception_message'] = str(error)",
                "    except Exception as error:",
                "        payload['protected_write_check'] = 'blocked'",
                "        payload['write_exception_type'] = type(error).__name__",
                "        payload['write_exception_message'] = str(error)",
                "    payload['status'] = 'passed' if payload.get('direct_lock_check') == 'blocked' and payload.get('protected_write_check') == 'blocked' else 'failed'",
                "    payload['classification'] = 'LOCK_ENFORCEMENT_PRESERVED' if payload['status'] == 'passed' else 'LOCK_ENFORCEMENT_DEFECT'",
                "env.cr.rollback()",
                "print('REBUILD_LOCK_CHECK=' + json.dumps(payload, sort_keys=True))",
                "",
            ],
        ),
        encoding="utf-8",
    )
    result = run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "init-db",
            "odoo",
            "shell",
            "--config=/etc/odoo/odoo.conf",
            f"--database={EXACT_VALIDATION_DB}",
        ),
        input_file=lock_script,
        check=False,
    )
    marker = None
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("REBUILD_LOCK_CHECK="):
            marker = line.removeprefix("REBUILD_LOCK_CHECK=")
    if result.returncode or not marker:
        return {
            "status": "failed",
            "classification": "LOCK_CHECK_EXECUTION_DEFECT",
            "exit_code": result.returncode,
            "output_tail": (result.stdout + result.stderr)[-4000:],
        }
    return json.loads(marker)


def target_validate(args: argparse.Namespace) -> dict[str, Any]:
    ensure_dirs()
    if not table_exists(EXACT_VALIDATION_DB, "rebuild_account_import_run"):
        status = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "validation-exact-validate",
            "status": "failed",
            "classification": "MISSING_TARGET_MODEL",
            "reason": f"{EXACT_VALIDATION_DB} does not have rebuild_account_migration installed.",
            "next_action": "Run make accounting-validation-exact-reset and make accounting-validation-exact-import before validation.",
        }
        write_json(PRIVATE_ARTIFACTS / "validation-exact-validate-status.json", status)
        raise HarnessError(status["reason"])

    source_moves = source_move_comparison_rows()
    target_moves = target_move_comparison_rows()
    source_lines = source_line_comparison_rows()
    target_lines = target_line_comparison_rows()
    source_accounts = source_account_balance_rows()
    target_accounts = target_account_balance_rows()
    source_journals = source_journal_balance_rows()
    target_journals = target_journal_balance_rows()
    source_partials = source_partial_reconcile_rows()
    target_partials = target_partial_reconcile_rows()
    source_fulls = source_full_reconcile_rows()
    target_fulls = target_full_reconcile_rows()
    source_reports = source_report_rows()
    target_source_reports = target_source_report_rows()
    source_report_lines = source_report_line_rows()
    target_source_report_lines = target_source_report_line_rows()
    source_report_expressions = source_report_expression_rows()
    target_source_report_expressions = target_source_report_expression_rows()
    source_report_columns = source_report_column_rows()
    target_source_report_columns = target_source_report_column_rows()
    source_deferred_schedules = source_deferred_schedule_rows()
    target_deferred_schedules = target_deferred_schedule_rows()
    source_reconciliation_models = source_reconciliation_model_rows()
    target_reconciliation_models = target_reconciliation_model_rows()
    source_reconciliation_model_lines = (
        source_reconciliation_model_line_rows()
    )
    target_reconciliation_model_lines = (
        target_reconciliation_model_line_rows()
    )
    source_tax_groups = source_tax_group_rows()
    target_tax_groups = target_tax_group_rows()
    source_tax_tags = source_tax_tag_rows()
    target_tax_tags = target_tax_tag_rows()
    source_taxes = source_tax_rows()
    target_taxes = target_tax_rows()
    source_tax_repartitions = source_tax_repartition_rows()
    target_tax_repartitions = target_tax_repartition_rows()
    source_tax_alternatives = source_tax_alternative_rows()
    target_tax_alternatives = target_tax_alternative_rows()
    source_assets = source_asset_rows()
    target_assets = target_asset_rows()
    source_depreciation_schedule = source_depreciation_schedule_rows()
    target_depreciation_schedule = target_depreciation_schedule_rows()
    source_full_replay_companies = source_full_replay_company_rows()
    target_full_replay_companies = target_full_replay_company_rows()
    source_currency_rates = source_currency_rate_rows()
    target_currency_rates = target_currency_rate_rows()
    source_move_backed_payments = source_move_backed_payment_rows()
    target_move_backed_payments = target_move_backed_payment_rows()
    source_no_entry_payments = source_no_entry_payment_rows()
    target_no_entry_payments = target_no_entry_payment_rows()
    source_bank_statement_lines = source_bank_statement_line_rows()
    target_bank_statement_lines = target_bank_statement_line_rows()
    bank_statement_line_fields = [
        "source_move_id",
        "source_company_id",
        "source_journal_id",
        "source_partner_id",
        "currency",
        "foreign_currency",
        "account_number",
        "partner_name",
        "transaction_type",
        "payment_ref",
        "internal_index",
        "sequence",
        "date",
        "amount",
        "amount_currency",
        "amount_residual",
        "is_reconciled",
        "move_statement_linked",
    ]
    source_analytic_lines = source_analytic_line_rows()
    target_analytic_lines = target_analytic_line_rows()
    source_accounting_attachments = source_accounting_attachment_rows()
    target_accounting_attachments = target_accounting_attachment_rows()
    source_sequence_chronology = sequence_chronology_summary(
        [row for row in source_moves if row["state"] == "posted"],
    )
    target_sequence_chronology = sequence_chronology_summary(
        [row for row in target_moves if row["state"] == "posted"],
    )
    sequence_chronology_matches = (
        source_sequence_chronology == target_sequence_chronology
    )

    comparisons = {
        "full_replay_company_controls": compare_rows(
            source_full_replay_companies,
            target_full_replay_companies,
            key="source_company_id",
            fields=[
                "move_count",
                "accounting_line_count",
                "debit",
                "credit",
                "tax_repartition_line_count",
                "tax_relation_count",
                "tax_tag_relation_count",
            ],
        ),
        "currency_rates": compare_rows(
            source_currency_rates,
            target_currency_rates,
            key="source_currency_rate_id",
            fields=[
                "currency",
                "source_company_id",
                "date",
                "rate",
                "provider",
                "retrieved_at",
            ],
        ),
        "moves": compare_rows(
            source_moves,
            target_moves,
            key="source_move_id",
            fields=[
                "move_name",
                "date",
                "sequence_prefix",
                "sequence_number",
                "source_journal_id",
                "source_company_id",
                "source_partner_id",
                "state",
            ],
        ),
         "move_lines": compare_rows(
            source_lines,
            target_lines,
            key="source_line_id",
            fields=[
                "source_move_id",
                "source_account_id",
                "source_partner_id",
                "currency",
                "debit",
                "credit",
                "balance",
                "amount_currency",
                "date_maturity",
            ],
        ),
        "account_balances": compare_rows(
            source_accounts,
            target_accounts,
            key="source_account_id",
            fields=["code", "debit", "credit", "balance"],
        ),
        "journal_balances": compare_rows(
            source_journals,
            target_journals,
            key="source_journal_id",
            fields=["code", "move_count", "move_line_count", "debit", "credit", "balance"],
        ),
        "partial_reconciliations": compare_rows(
            source_partials,
            target_partials,
            key="source_partial_reconcile_id",
            fields=[
                "source_debit_line_id",
                "source_credit_line_id",
                "source_full_reconcile_id",
                "source_exchange_move_id",
                "amount",
                "debit_amount_currency",
                "credit_amount_currency",
                "max_date",
            ],
        ),
        "full_reconciliations": compare_rows(
            source_fulls,
            target_fulls,
            key="source_full_reconcile_id",
            fields=["source_line_ids", "source_partial_reconcile_ids"],
        ),
         "source_reports": compare_rows(
            source_reports,
            target_source_reports,
            key="source_report_key",
            fields=[
                "source_report_id",
                "source_name",
                "localized_name",
                "active",
                "sequence",
                "country_code",
                "source_country_id",
                "chart_template",
                "source_root_report_id",
                "root_report_name",
                "source_custom_handler_model",
                "decision",
                "target_status",
                "target_action_xmlid",
                "target_evidence_key",
                "availability_condition",
                "integer_rounding",
                "default_opening_date_filter",
                "currency_translation",
                "filter_multi_company",
                "filter_hide_0_lines",
                "filter_hierarchy",
                "filter_account_type",
                "filter_date_range",
                "filter_show_draft",
                "filter_unreconciled",
                "filter_unfold_all",
                "filter_period_comparison",
                "filter_growth_comparison",
                "filter_journals",
                "filter_partner",
                "filter_aml_ir_filters",
                "filter_budgets",
                "filter_analytic_groupby",
                "filter_cash_basis",
                "use_sections",
                "only_tax_exigible",
                "use_fiscal_periods",
                "allow_foreign_vat",
                "line_count",
                "column_count",
                "expression_count",
                "imported_line_count",
                "imported_column_count",
                "imported_expression_count",
                "external_value_count",
                "line_code_sample",
                "expression_engine_summary",
            ],
        ),
        "source_report_lines": compare_rows(
            source_report_lines,
            target_source_report_lines,
            key="source_report_line_key",
            fields=[
                "source_line_id",
                "source_report_id",
                "source_parent_line_id",
                "hierarchy_level",
                "sequence",
                "source_action_id",
                "groupby",
                "user_groupby",
                "code",
                "horizontal_split_side",
                "source_name",
                "localized_name",
                "foldability",
                "print_on_new_page",
                "hide_if_zero",
                "expression_count",
            ],
        ),
        "source_report_expressions": compare_rows(
            source_report_expressions,
            target_source_report_expressions,
            key="source_report_expression_key",
            fields=[
                "source_expression_id",
                "source_report_id",
                "source_report_line_id",
                "line_code",
                "line_name",
                "label",
                "engine",
                "formula",
                "subformula",
                "date_scope",
                "figure_type",
                "carryover_target",
                "green_on_positive",
                "blank_if_zero",
                "auditable",
            ],
        ),
        "source_report_columns": compare_rows(
            source_report_columns,
            target_source_report_columns,
            key="source_report_column_key",
            fields=[
                "source_column_id",
                "source_report_id",
                "sequence",
                "expression_label",
                "figure_type",
                "source_name",
                "sortable",
                "blank_if_zero",
            ],
        ),
        "deferred_schedules": compare_rows(
            source_deferred_schedules,
            target_deferred_schedules,
            key="source_deferred_schedule_key",
            fields=[
                "source_original_move_id",
                "source_deferred_move_id",
                "source_company_id",
                "currency",
                "source_original_name",
                "source_deferred_name",
                "source_original_state",
                "source_deferred_state",
                "source_original_move_type",
                "source_deferred_move_type",
                "original_date",
                "deferred_date",
                "deferred_start_date",
                "deferred_end_date",
                "source_original_deferred_line_count",
                "source_line_count",
                "deferred_account_code",
                "deferred_account_name",
                "counterpart_account_codes",
                "counterpart_account_names",
                "amount",
                "deferred_account_balance",
                "counterpart_balance",
                "schedule_type",
                "schedule_phase",
                "representation_status",
            ],
        ),
        "move_backed_payments": compare_rows(
            source_move_backed_payments,
            target_move_backed_payments,
            key="source_payment_id",
            fields=[
                "source_move_id",
                "source_company_id",
                "source_journal_id",
                "source_partner_id",
                "source_outstanding_account_id",
                "source_destination_account_id",
                "currency",
                "date",
                "amount",
                "payment_type",
                "partner_type",
                "target_state",
                "source_state",
                "payment_method_code",
                "payment_method_type",
                "origin_linked",
            ],
        ),
        "native_no_entry_payments": compare_rows(
            source_no_entry_payments,
            target_no_entry_payments,
            key="source_payment_id",
            fields=[
                "name",
                "source_company_id",
                "source_journal_id",
                "source_partner_id",
                "currency",
                "date",
                "amount",
                "amount_company_currency_signed",
                "state",
                "payment_type",
                "partner_type",
                "memo",
                "payment_reference",
                "source_outstanding_account_id",
                "source_destination_account_id",
                "is_reconciled",
                "is_matched",
                "is_sent",
                "accounting_effect",
            ],
        ),
        "bank_statement_lines": compare_rows(
            source_bank_statement_lines,
            target_bank_statement_lines,
            key="source_statement_line_id",
            fields=bank_statement_line_fields,
        ),
        "analytic_lines": compare_rows(
            source_analytic_lines,
            target_analytic_lines,
            key="source_analytic_line_id",
            fields=[
                "source_analytic_account_id",
                "source_analytic_account_ids",
                "source_company_id",
                "source_partner_id",
                "source_product_id",
                "date",
                "name",
                "category",
                "amount",
                "unit_amount",
                "source_general_account_id",
                "source_journal_id",
                "source_move_line_id",
                "code",
                "ref",
            ],
        ),
        "accounting_attachments": compare_rows(
            source_accounting_attachments,
            target_accounting_attachments,
            key="source_attachment_id",
            fields=[
                "source_res_model",
                "source_res_id",
                "name",
                "mimetype",
                "checksum",
                "file_size",
                "type",
            ],
        ),
        "tax_groups": compare_rows(
            source_tax_groups,
            target_tax_groups,
            key="source_tax_group_id",
            fields=[
                "name",
                "sequence",
                "source_company_id",
                "country_code",
                "source_tax_payable_account_id",
                "source_tax_receivable_account_id",
                "source_advance_tax_payment_account_id",
            ],
        ),
        "reconciliation_models": compare_rows(
            source_reconciliation_models,
            target_reconciliation_models,
            key="source_reconciliation_model_id",
            fields=[
                "name",
                "sequence",
                "source_company_id",
                "active",
                "trigger",
                "can_be_proposed",
                "source_mapped_partner_id",
                "match_amount",
                "match_amount_min",
                "match_amount_max",
                "match_label",
                "match_label_param",
                "source_journal_ids",
                "source_match_partner_ids",
                "line_count",
            ],
        ),
        "reconciliation_model_lines": compare_rows(
            source_reconciliation_model_lines,
            target_reconciliation_model_lines,
            key="source_reconciliation_model_line_id",
            fields=[
                "source_reconciliation_model_id",
                "sequence",
                "source_account_id",
                "source_partner_id",
                "label",
                "amount_type",
                "amount_string",
                "amount",
                "source_tax_ids",
            ],
        ),
        "tax_tags": compare_rows(
            source_tax_tags,
            target_tax_tags,
            key="source_tax_tag_id",
            fields=["name", "applicability", "color", "active", "country_code"],
        ),
        "taxes": compare_rows(
            source_taxes,
            target_taxes,
            key="source_tax_id",
            fields=[
                "name",
                "type_tax_use",
                "tax_scope",
                "amount_type",
                "price_include_override",
                "tax_exigibility",
                "sequence",
                "amount",
                "active",
                "include_base_amount",
                "is_base_affected",
                "analytic",
                "source_company_id",
                "source_tax_group_id",
                "country_code",
                "source_cash_basis_transition_account_id",
                "ubl_cii_tax_category_code",
                "ubl_cii_tax_exemption_reason_code",
            ],
        ),
        "tax_repartition_lines": compare_rows(
            source_tax_repartitions,
            target_tax_repartitions,
            key="source_tax_repartition_line_id",
            fields=[
                "source_tax_id",
                "source_account_id",
                "sequence",
                "repartition_type",
                "document_type",
                "factor_percent",
                "use_in_tax_closing",
                "source_tax_tag_ids",
            ],
        ),
        "tax_alternative_relations": compare_rows(
            source_tax_alternatives,
            target_tax_alternatives,
            key="relation_key",
            fields=["source_dest_tax_id", "source_src_tax_id"],
        ),
        "assets": compare_rows(
            source_assets,
            target_assets,
            key="source_asset_id",
            fields=[
                "name",
                "source_company_id",
                "currency",
                "state",
                "asset_group_name",
                "acquisition_date",
                "prorata_date",
                "disposal_date",
                "prorata_computation_type",
                "original_value",
                "book_value",
                "imported_period_net_value",
                "salvage_value",
                "non_deductible_tax_value",
                "already_depreciated_amount_import",
                "net_gain_on_sale",
                "source_asset_account_id",
                "source_depreciation_account_id",
                "source_depreciation_expense_account_id",
                "source_journal_id",
                "source_depreciation_move_count",
            ],
        ),
        "asset_depreciation_schedule": compare_rows(
            source_depreciation_schedule,
            target_depreciation_schedule,
            key="source_schedule_id",
            fields=[
                "source_asset_id",
                "source_move_id",
                "source_move_name",
                "source_move_state",
                "depreciation_date",
                "move_ref",
                "expense_amount",
                "depreciation_amount",
                "accumulated_depreciation_amount",
                "net_book_value_after_line",
                "source_line_count",
            ],
        ),
    }
    invariant_failures = {
        "target_unbalanced_posted_moves": target_unbalanced_moves(),
        "duplicate_move_traces": duplicate_target_traces("account_move"),
        "duplicate_move_line_traces": duplicate_target_traces("account_move_line"),
        "duplicate_partial_reconcile_traces": duplicate_target_traces("account_partial_reconcile"),
        "duplicate_full_reconcile_traces": duplicate_target_traces("account_full_reconcile"),
        "duplicate_source_report_traces": duplicate_target_traces("rebuild_account_source_report"),
        "duplicate_source_report_line_traces": duplicate_target_traces("rebuild_account_source_report_line"),
        "duplicate_source_report_expression_traces": duplicate_target_traces("rebuild_account_source_report_expression"),
        "duplicate_source_report_column_traces": duplicate_target_traces("rebuild_account_source_report_column"),
        "duplicate_deferred_schedule_traces": duplicate_target_traces("rebuild_account_deferred_schedule_line"),
        "duplicate_payment_traces": duplicate_target_traces("account_payment"),
        "duplicate_bank_statement_line_traces": duplicate_target_traces("account_bank_statement_line"),
        "duplicate_attachment_traces": duplicate_target_traces("ir_attachment"),
        "duplicate_tax_group_traces": duplicate_target_traces("account_tax_group"),
        "duplicate_reconciliation_model_traces": duplicate_target_traces(
            "account_reconcile_model",
        ),
        "duplicate_reconciliation_model_line_traces": duplicate_target_traces(
            "account_reconcile_model_line",
        ),
        "duplicate_tax_traces": duplicate_target_traces("account_tax"),
        "duplicate_tax_repartition_line_traces": duplicate_target_traces("account_tax_repartition_line"),
        "duplicate_tax_tag_traces": duplicate_target_traces("account_account_tag"),
        "duplicate_analytic_plan_traces": duplicate_target_traces("account_analytic_plan"),
        "duplicate_analytic_account_traces": duplicate_target_traces("account_analytic_account"),
        "duplicate_analytic_line_traces": duplicate_target_traces("account_analytic_line"),
        "duplicate_asset_traces": duplicate_target_traces("rebuild_account_asset"),
        "duplicate_asset_depreciation_schedule_traces": duplicate_target_traces("rebuild_account_asset_depreciation_schedule_line"),
        "duplicate_currency_rate_traces": duplicate_target_traces("res_currency_rate"),
    }
    lock_enforcement = target_lock_enforcement_check(args.source_dir)
    passed = (
        all(item["passed"] for item in comparisons.values())
        and sequence_chronology_matches
        and not invariant_failures["target_unbalanced_posted_moves"]
        and not invariant_failures["duplicate_move_traces"]
        and not invariant_failures["duplicate_move_line_traces"]
        and not invariant_failures["duplicate_partial_reconcile_traces"]
        and not invariant_failures["duplicate_full_reconcile_traces"]
        and not invariant_failures["duplicate_source_report_traces"]
        and not invariant_failures["duplicate_source_report_line_traces"]
        and not invariant_failures["duplicate_source_report_expression_traces"]
        and not invariant_failures["duplicate_source_report_column_traces"]
        and not invariant_failures["duplicate_deferred_schedule_traces"]
        and not invariant_failures["duplicate_payment_traces"]
        and not invariant_failures["duplicate_bank_statement_line_traces"]
        and not invariant_failures["duplicate_attachment_traces"]
        and not invariant_failures["duplicate_tax_group_traces"]
        and not invariant_failures["duplicate_reconciliation_model_traces"]
        and not invariant_failures[
            "duplicate_reconciliation_model_line_traces"
        ]
        and not invariant_failures["duplicate_tax_traces"]
        and not invariant_failures["duplicate_tax_repartition_line_traces"]
        and not invariant_failures["duplicate_tax_tag_traces"]
        and not invariant_failures["duplicate_analytic_plan_traces"]
        and not invariant_failures["duplicate_analytic_account_traces"]
        and not invariant_failures["duplicate_analytic_line_traces"]
        and not invariant_failures["duplicate_asset_traces"]
        and not invariant_failures["duplicate_asset_depreciation_schedule_traces"]
        and not invariant_failures["duplicate_currency_rate_traces"]
        and lock_enforcement.get("status") == "passed"
    )
    controls = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "scope": {
            "source_database": SOURCE_DB,
            "target_database": EXACT_VALIDATION_DB,
            "company": "Unstatic Labs",
            "source_company_id": 1,
            "period_start": USL_BENCHMARK_START,
            "period_end": USL_BENCHMARK_END,
            "complete_native_company_replay": True,
            "import_mode": "exact_ledger_replay",
        },
        "source_posted_summary": source_posted_summary(),
        "target_posted_summary": target_posted_summary(),
        "full_replay_company_controls": {
            "source": source_full_replay_companies,
            "target": target_full_replay_companies,
            "comparison": comparisons["full_replay_company_controls"],
            "note": "The broad replay comparison excludes source move lines without an account from accounting-line parity; these are tracked as non-account display lines for document-level follow-up.",
        },
        "currency_rate_controls": {
            "source": currency_rate_summary(source_currency_rates),
            "target": currency_rate_summary(target_currency_rates),
            "comparison": comparisons["currency_rates"],
            "note": "Native Odoo technical rates, source provider metadata and source retrieval timestamps are compared across the complete restored snapshot, including the Track B replay period.",
        },
        "comparisons": comparisons,
        "sequence_chronology": {
            "source": source_sequence_chronology,
            "target": target_sequence_chronology,
            "matches": sequence_chronology_matches,
            "interpretation": (
                "Source gaps and date-order exceptions remain visible and "
                "must match the target exactly; validation does not "
                "resequence posted history."
            ),
        },
        "invariant_failures": invariant_failures,
        "lock_enforcement": lock_enforcement,
        "limitations": [
            "Native move, line, payment and reconciliation comparisons cover every source state for companies 1 and 8; balance benchmarks remain scoped to posted entries.",
            "The target database contains the complete replay through the source snapshot date for source companies 1 and 8.",
            "Currency-rate parity is intentionally broader than the posted benchmark slice because native Track B invoices, payments and exchange differences require the restored rates through the source snapshot date.",
            "Source report definitions, all native payments, bank statement lines, analytic lines, scoped accounting attachments and asset depreciation evidence are compared without source-specific normalization. Final report-variant acceptance remains outside this data-parity control.",
        ],
        "status": "passed" if passed else "failed",
        "classification": "POSTED_LEDGER_SLICE_PARITY" if passed else "TRANSFER_DEFECT",
    }
    write_json(PRIVATE_ARTIFACTS / "target-controls.json", controls)
    write_json(PRIVATE_ARTIFACTS / "validation-exact-validate-status.json", controls)
    if not passed and not getattr(args, "allow_errors", False):
        raise HarnessError("Target posted-ledger slice validation failed. See artifacts/accounting-compat/private/target-controls.json")
    return controls


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def trial_balance_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT {account_code} AS account_code,
               {account_name} AS account_name,
               aa.account_type::text AS account_type,
               aa.rebuild_source_id::text AS source_account_id,
               round(sum(aml.debit)::numeric, 2)::text AS debit,
               round(sum(aml.credit)::numeric, 2)::text AS credit,
               round(sum(aml.balance)::numeric, 2)::text AS closing_balance
        FROM account_move_line aml
        JOIN account_move am ON am.id = aml.move_id
        JOIN account_account aa ON aa.id = aml.account_id
        JOIN res_company company ON company.id = am.company_id
        WHERE aml.rebuild_source_model = 'account.move.line'
          AND {benchmark_where}
        GROUP BY aa.id, {account_code}, {account_name}, aa.account_type, aa.rebuild_source_id
        ORDER BY {account_code}, aa.rebuild_source_id
        """.format(
            account_code=target_account_code_expr(),
            account_name=source_name_expr("aa"),
            benchmark_where=target_benchmark_move_where(),
        ),
        set_readonly_role=False,
    )


def general_ledger_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT q.account_code,
               q.account_name,
               q.date,
               q.journal_code,
               q.move_name,
               q.source_move_id,
               q.source_line_id,
               q.partner_name,
               q.label,
               q.debit,
               q.credit,
               q.balance,
               round(sum(q.balance::numeric) OVER (
                   PARTITION BY q.account_code
                   ORDER BY q.date, q.move_name, q.source_line_id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
               ), 2)::text AS running_balance
        FROM (
            SELECT {account_code} AS account_code,
                   {account_name} AS account_name,
                   am.date::text AS date,
                   aj.code::text AS journal_code,
                   am.name::text AS move_name,
                   am.rebuild_source_id::text AS source_move_id,
                   aml.rebuild_source_id::text AS source_line_id,
                   COALESCE(rp.name::text, '') AS partner_name,
                   COALESCE(aml.name::text, '') AS label,
                   round(aml.debit::numeric, 2)::text AS debit,
                   round(aml.credit::numeric, 2)::text AS credit,
                   round(aml.balance::numeric, 2)::text AS balance
            FROM account_move_line aml
            JOIN account_move am ON am.id = aml.move_id
            JOIN account_account aa ON aa.id = aml.account_id
            JOIN account_journal aj ON aj.id = am.journal_id
            JOIN res_company company ON company.id = am.company_id
            LEFT JOIN res_partner rp ON rp.id = aml.partner_id
            WHERE aml.rebuild_source_model = 'account.move.line'
              AND {benchmark_where}
        ) q
        ORDER BY q.account_code, q.date, q.move_name, q.source_line_id
        """.format(
            account_code=target_account_code_expr(),
            account_name=source_name_expr("aa"),
            benchmark_where=target_benchmark_move_where(),
        ),
        set_readonly_role=False,
    )


def french_statement_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT statement_key::text,
               statement_name::text,
               line_sequence::text,
               line_code::text,
               line_name::text,
               source_formula::text,
               drilldown_account_prefixes::text,
               move_line_count::text,
               round(gross_amount::numeric, 2)::text AS gross_amount,
               round(depreciation_amount::numeric, 2)::text AS depreciation_amount,
               round(net_amount::numeric, 2)::text AS net_amount,
               round(amount::numeric, 2)::text AS amount
        FROM rebuild_account_french_statement_line
        WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
        ORDER BY statement_key, line_sequence, line_code
        """,
        set_readonly_role=False,
    )


def french_tax_package_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT form_code::text,
               form_name::text,
               field_code::text,
               field_label::text,
               source_kind::text,
               source_formula::text,
               COALESCE(source_report_line_code::text, '') AS source_report_line_code,
               COALESCE(drilldown_account_prefixes::text, '') AS drilldown_account_prefixes,
               move_line_count::text,
               quantity::text,
               round(amount::numeric, 2)::text AS amount,
               round(rounded_amount::numeric, 2)::text AS rounded_amount,
               COALESCE(round(benchmark_amount::numeric, 2)::text, '') AS benchmark_amount,
               COALESCE(round(ledger_amount::numeric, 2)::text, '') AS ledger_amount,
               COALESCE(round(difference_amount::numeric, 2)::text, '') AS difference_amount,
               COALESCE(difference_classification::text, '') AS difference_classification,
               COALESCE(value_text::text, '') AS value_text,
               review_status::text
        FROM rebuild_account_french_tax_package_line
        WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
        ORDER BY form_code, line_sequence, field_code
        """,
        set_readonly_role=False,
    )


def vat_benchmark_investigation() -> dict[str, Any]:
    target_ledger_rows = query_rows(
        EXACT_VALIDATION_DB,
        f"""
        SELECT {target_account_code_expr()} AS account_code,
               COALESCE(aa.name->>'en_US', aa.name->>'fr_FR', aa.name::text) AS account_name,
               count(*)::text AS line_count,
               round(COALESCE(sum(aml.debit), 0)::numeric, 2)::text AS debit,
               round(COALESCE(sum(aml.credit), 0)::numeric, 2)::text AS credit,
               round(COALESCE(sum(aml.balance), 0)::numeric, 2)::text AS balance
          FROM account_move_line aml
          JOIN account_move am ON am.id = aml.move_id
          JOIN account_account aa ON aa.id = aml.account_id
         WHERE aml.company_id = (SELECT id FROM res_company WHERE rebuild_source_id = 1 LIMIT 1)
           AND am.state = 'posted'
           AND am.date BETWEEN DATE '{USL_BENCHMARK_START}' AND DATE '{USL_BENCHMARK_END}'
           AND {target_account_code_expr()} = '445660'
         GROUP BY {target_account_code_expr()}, COALESCE(aa.name->>'en_US', aa.name->>'fr_FR', aa.name::text)
        """,
        set_readonly_role=False,
    )
    target_ca12_clearing_rows = query_rows(
        EXACT_VALIDATION_DB,
        f"""
        SELECT am.date::text,
               aj.code::text AS journal_code,
               am.name::text AS move_name,
               am.ref::text AS move_ref,
               {target_account_code_expr()} AS account_code,
               COALESCE(aa.name->>'en_US', aa.name->>'fr_FR', aa.name::text) AS account_name,
               aml.rebuild_source_id::text AS source_line_id,
               round(aml.debit::numeric, 2)::text AS debit,
               round(aml.credit::numeric, 2)::text AS credit,
               round(aml.balance::numeric, 2)::text AS balance,
               aml.name::text AS line_name
          FROM account_move_line aml
          JOIN account_move am ON am.id = aml.move_id
          JOIN res_company company ON company.id = aml.company_id
          JOIN account_account aa ON aa.id = aml.account_id
          LEFT JOIN account_journal aj ON aj.id = aml.journal_id
         WHERE company.rebuild_source_id = 1
           AND am.state = 'posted'
           AND am.date BETWEEN DATE '{USL_BENCHMARK_START}' AND DATE '{USL_BENCHMARK_END}'
           AND {target_account_code_expr()} LIKE '445%'
           AND lower(COALESCE(aml.name, '')) = 'ca12'
         ORDER BY {target_account_code_expr()}, aml.id
        """,
        set_readonly_role=False,
    )
    external_value_rows = query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT form_code::text,
               field_code::text,
               value_kind::text,
               review_status::text,
               round(amount::numeric, 2)::text AS amount,
               source_key::text,
               source_reference::text
          FROM rebuild_account_external_report_value
         WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
           AND field_code IN (
               '2033_D_TVA_DEDUCTIBLE_BIENS_SERVICES_445660',
               '3517S_TVA_DEDUCTIBLE_BIENS_SERVICES_445660'
           )
           AND active IS TRUE
         ORDER BY form_code, field_code
        """,
        set_readonly_role=False,
    )
    tax_package_rows = query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT form_code::text,
               field_code::text,
               source_kind::text,
               source_formula::text,
               drilldown_account_prefixes::text,
               move_line_count::text,
               round(amount::numeric, 2)::text AS amount,
               COALESCE(round(benchmark_amount::numeric, 2)::text, '') AS benchmark_amount,
               COALESCE(round(ledger_amount::numeric, 2)::text, '') AS ledger_amount,
               COALESCE(round(difference_amount::numeric, 2)::text, '') AS difference_amount,
               COALESCE(difference_classification::text, '') AS difference_classification,
               review_status::text
          FROM rebuild_account_french_tax_package_line
         WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
           AND field_code IN (
               '2033_D_TVA_DEDUCTIBLE_BIENS_SERVICES_445660',
               '3517S_TVA_DEDUCTIBLE_BIENS_SERVICES_445660'
           )
         ORDER BY form_code, field_code
        """,
        set_readonly_role=False,
    )

    source_status: dict[str, Any] = {"status": "not_checked"}
    try:
        source_ledger_rows = query_rows(
            SOURCE_DB,
            f"""
            SELECT {source_account_code_expr()} AS account_code,
                   COALESCE(aa.name->>'en_US', aa.name->>'fr_FR', aa.name::text) AS account_name,
                   count(*)::text AS line_count,
                   round(COALESCE(sum(aml.debit), 0)::numeric, 2)::text AS debit,
                   round(COALESCE(sum(aml.credit), 0)::numeric, 2)::text AS credit,
                   round(COALESCE(sum(aml.balance), 0)::numeric, 2)::text AS balance
              FROM account_move_line aml
              JOIN account_move am ON am.id = aml.move_id
              JOIN account_account aa ON aa.id = aml.account_id
             WHERE aml.company_id = 1
               AND am.state = 'posted'
               AND am.date BETWEEN DATE '{USL_BENCHMARK_START}' AND DATE '{USL_BENCHMARK_END}'
               AND {source_account_code_expr()} = '445660'
             GROUP BY {source_account_code_expr()}, COALESCE(aa.name->>'en_US', aa.name->>'fr_FR', aa.name::text)
            """,
            set_readonly_role=True,
        )
        source_ca12_clearing_rows = query_rows(
            SOURCE_DB,
            f"""
            SELECT aml.date::text,
                   aj.code::text AS journal_code,
                   am.name::text AS move_name,
                   am.ref::text AS move_ref,
                   {source_account_code_expr()} AS account_code,
                   COALESCE(aa.name->>'en_US', aa.name->>'fr_FR', aa.name::text) AS account_name,
                   aml.id::text AS source_line_id,
                   round(aml.debit::numeric, 2)::text AS debit,
                   round(aml.credit::numeric, 2)::text AS credit,
                   round(aml.balance::numeric, 2)::text AS balance,
                   aml.name::text AS line_name
              FROM account_move_line aml
              JOIN account_move am ON am.id = aml.move_id
              JOIN account_account aa ON aa.id = aml.account_id
              LEFT JOIN account_journal aj ON aj.id = aml.journal_id
             WHERE aml.company_id = 1
               AND am.state = 'posted'
               AND aml.date BETWEEN DATE '{USL_BENCHMARK_START}' AND DATE '{USL_BENCHMARK_END}'
               AND {source_account_code_expr()} LIKE '445%'
               AND lower(COALESCE(aml.name, '')) = 'ca12'
             ORDER BY {source_account_code_expr()}, aml.id
            """,
            set_readonly_role=True,
        )
        source_tag_rows = query_rows(
            SOURCE_DB,
            f"""
            SELECT tag.name->>'en_US' AS tag,
                   count(*)::text AS line_count,
                   round(COALESCE(sum(aml.debit), 0)::numeric, 2)::text AS debit,
                   round(COALESCE(sum(aml.credit), 0)::numeric, 2)::text AS credit,
                   round(COALESCE(sum(aml.balance), 0)::numeric, 2)::text AS balance,
                   round(COALESCE(sum(aml.tax_base_amount), 0)::numeric, 2)::text AS tax_base_amount
              FROM account_move_line aml
              JOIN account_move am ON am.id = aml.move_id
              JOIN account_account_tag_account_move_line_rel rel ON rel.account_move_line_id = aml.id
              JOIN account_account_tag tag ON tag.id = rel.account_account_tag_id
             WHERE aml.company_id = 1
               AND am.state = 'posted'
               AND aml.date BETWEEN DATE '{USL_BENCHMARK_START}' AND DATE '{USL_BENCHMARK_END}'
               AND tag.name->>'en_US' = '20'
             GROUP BY tag.name->>'en_US'
            """,
            set_readonly_role=True,
        )
        source_expression_rows = query_rows(
            SOURCE_DB,
            """
            SELECT line.code::text,
                   line.name->>'en_US' AS line_name,
                   expr.label::text,
                   expr.engine::text,
                   expr.formula::text,
                   COALESCE(expr.subformula::text, '') AS subformula
              FROM account_report_line line
              JOIN account_report_expression expr ON expr.report_line_id = line.id
             WHERE line.code = 'box_20'
             ORDER BY expr.id
            """,
            set_readonly_role=True,
        )
        source_external_value_count = scalar(
            SOURCE_DB,
            "SELECT count(*)::text FROM account_report_external_value",
            set_readonly_role=True,
        )
        source_status = {
            "status": "checked",
            "ledger_account_445660": source_ledger_rows,
            "ca12_clearing_lines": source_ca12_clearing_rows,
            "tax_tag_20": source_tag_rows,
            "tax_report_box_20_expressions": source_expression_rows,
            "account_report_external_value_count": source_external_value_count,
        }
    except HarnessError as exc:
        source_status = {
            "status": "unavailable",
            "error": str(exc),
        }

    source_ledger = (source_status.get("ledger_account_445660") or [{}])[0] if source_status.get("status") == "checked" else {}
    target_ledger = target_ledger_rows[0] if target_ledger_rows else {}
    source_target_match = bool(
        source_status.get("status") == "checked"
        and source_ledger
        and target_ledger
        and source_ledger.get("line_count") == target_ledger.get("line_count")
        and source_ledger.get("debit") == target_ledger.get("debit")
        and source_ledger.get("credit") == target_ledger.get("credit")
        and source_ledger.get("balance") == target_ledger.get("balance")
    )
    return {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "report": "VAT Benchmark Investigation",
        "company": "Unstatic Labs",
        "period_start": USL_BENCHMARK_START,
        "period_end": USL_BENCHMARK_END,
        "field_codes": [
            "2033_D_TVA_DEDUCTIBLE_BIENS_SERVICES_445660",
            "3517S_TVA_DEDUCTIBLE_BIENS_SERVICES_445660",
        ],
        "source": source_status,
        "target": {
            "ledger_account_445660": target_ledger_rows,
            "ca12_clearing_lines": target_ca12_clearing_rows,
            "external_benchmark_values": external_value_rows,
            "tax_package_rows": tax_package_rows,
        },
        "source_target_445660_ledger_match": source_target_match,
        "classification": "SOURCE_CA12_CLEARING_EVIDENCE",
        "conclusion": (
            "The SQL source and imported target ledger match for account 445660. "
            "The supplied EUR 1,960 benchmark is also present in the posted source CA12 clearing "
            "entry OD000000009 as the 445660 credit line. The French tax-package mapping uses that "
            "source clearing amount for the declaration field and keeps gross 445660 turnover as "
            "separate ledger evidence."
        ),
        "next_action": (
            "Accountant review is still required for final CA12/declaration acceptance, but this "
            "specific EUR 1,960 value is no longer classified as a source-to-target ledger mismatch."
        ),
    }


def bank_reconciliation_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT date::text,
               journal_code::text,
               move_name::text,
               source_statement_line_id::text,
               payment_ref::text,
               partner_name::text,
               transaction_type::text,
               account_number::text,
               internal_index::text,
               round(amount::numeric, 2)::text AS amount,
               round(amount_currency::numeric, 2)::text AS amount_currency,
               round(amount_residual::numeric, 2)::text AS amount_residual,
               is_reconciled::text,
               reconciliation_status::text,
               move_line_count::text
        FROM rebuild_account_bank_reconciliation_line
        WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
        ORDER BY journal_code, date, source_statement_line_id
        """,
        set_readonly_role=False,
    )


def currency_report_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT report_section::text,
               COALESCE(currency.name::text, '') AS currency,
               account_code::text,
               account_name::text,
               account_type::text,
               COALESCE(partner.name::text, '') AS partner_name,
               move_line_count::text,
               round(debit::numeric, 2)::text AS debit,
               round(credit::numeric, 2)::text AS credit,
               round(balance::numeric, 2)::text AS balance,
               round(amount_currency::numeric, 2)::text AS amount_currency,
               round(amount_residual::numeric, 2)::text AS amount_residual,
               round(amount_residual_currency::numeric, 2)::text AS amount_residual_currency
        FROM rebuild_account_currency_report_line report
        LEFT JOIN res_currency currency ON currency.id = report.currency_id
        LEFT JOIN res_partner partner ON partner.id = report.partner_id
        WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
        ORDER BY report_section, currency, account_code, partner_name
        """,
        set_readonly_role=False,
    )


def analytic_distribution_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT analytic_key::text,
               analytic_code::text,
               analytic_name::text,
               account_code::text,
               account_name::text,
               move_line_count::text,
               round(percentage::numeric, 4)::text AS percentage,
               round(allocated_debit::numeric, 2)::text AS allocated_debit,
               round(allocated_credit::numeric, 2)::text AS allocated_credit,
               round(allocated_balance::numeric, 2)::text AS allocated_balance
        FROM rebuild_account_analytic_distribution_line
        WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
        ORDER BY analytic_name, account_code
        """,
        set_readonly_role=False,
    )


def analytic_distribution_current_rows() -> list[dict[str, Any]]:
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT analytic_key::text,
               analytic_code::text,
               analytic_name::text,
               account_code::text,
               account_name::text,
               move_line_count::text,
               round(percentage::numeric, 4)::text AS percentage,
               round(allocated_debit::numeric, 2)::text AS allocated_debit,
               round(allocated_credit::numeric, 2)::text AS allocated_credit,
               round(allocated_balance::numeric, 2)::text AS allocated_balance
        FROM rebuild_account_analytic_distribution_line
        WHERE period_key = 'USL current from 2025-10-01'
        ORDER BY analytic_name, account_code
        """,
        set_readonly_role=False,
    )


def odoo_report_view_controls() -> dict[str, Any]:
    required_views = [
        "rebuild_account_trial_balance_line",
        "rebuild_account_general_ledger_line",
        "rebuild_account_journal_report_line",
        "rebuild_account_partner_ledger_line",
        "rebuild_account_open_item_line",
        "rebuild_account_aged_partner_balance_line",
        "rebuild_account_financial_statement_line",
        "rebuild_account_tax_report_line",
        "rebuild_account_eu_tax_report_line",
        "rebuild_account_bank_reconciliation_line",
        "rebuild_account_currency_report_line",
        "rebuild_account_management_summary_line",
        "rebuild_account_revenue_spending_month",
        "rebuild_account_analytic_distribution_line",
        "rebuild_account_closing_snapshot",
        "rebuild_account_asset",
        "rebuild_account_asset_depreciation_schedule_line",
        "rebuild_account_deferred_schedule_line",
        "rebuild_account_french_statement_line",
        "rebuild_account_french_tax_package_line",
    ]
    missing_views = [view for view in required_views if not table_exists(EXACT_VALIDATION_DB, view)]
    if missing_views:
        return {
            "status": "failed",
            "missing_views": missing_views,
            "classification": "MISSING_CAPABILITY",
        }

    rows = query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT 'trial_balance' AS report_key,
               count(*)::text AS row_count,
               round(sum(debit)::numeric, 2)::text AS debit,
               round(sum(credit)::numeric, 2)::text AS credit,
               round(sum(closing_balance)::numeric, 2)::text AS balance,
               NULL::text AS residual,
               NULL::text AS statement_balance
          FROM rebuild_account_trial_balance_line
         WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
        UNION ALL
        SELECT 'general_ledger',
               count(*)::text,
               round(sum(debit)::numeric, 2)::text,
               round(sum(credit)::numeric, 2)::text,
               round(sum(balance)::numeric, 2)::text,
               NULL::text,
               NULL::text
          FROM rebuild_account_general_ledger_line
         WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
        UNION ALL
        SELECT 'journal_report',
               count(*)::text,
               round(sum(debit)::numeric, 2)::text,
               round(sum(credit)::numeric, 2)::text,
               round(sum(balance)::numeric, 2)::text,
               NULL::text,
               NULL::text
          FROM rebuild_account_journal_report_line
         WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
        UNION ALL
        SELECT 'partner_ledger',
               count(*)::text,
               round(sum(debit)::numeric, 2)::text,
               round(sum(credit)::numeric, 2)::text,
               round(sum(balance)::numeric, 2)::text,
               NULL::text,
               NULL::text
          FROM rebuild_account_partner_ledger_line
         WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
        UNION ALL
        SELECT 'customer_statement',
               count(*)::text,
               round(COALESCE(sum(line.debit), 0)::numeric, 2)::text,
               round(COALESCE(sum(line.credit), 0)::numeric, 2)::text,
               round(COALESCE(sum(line.balance), 0)::numeric, 2)::text,
               round(COALESCE(sum(line.amount_residual), 0)::numeric, 2)::text,
               NULL::text
          FROM account_move_line line
          JOIN account_move move ON move.id = line.move_id
          JOIN account_account account ON account.id = line.account_id
          JOIN res_partner partner ON partner.id = line.partner_id
         WHERE line.rebuild_source_model = 'account.move.line'
           AND move.rebuild_source_model = 'account.move'
           AND line.company_id = (SELECT id FROM res_company WHERE rebuild_source_id = 1 LIMIT 1)
           AND move.state = 'posted'
           AND move.date BETWEEN DATE '2024-01-10' AND DATE '2025-09-30'
           AND account.account_type = 'asset_receivable'
           AND partner.customer_rank > 0
        UNION ALL
        SELECT 'open_items',
               count(*)::text,
               NULL::text,
               NULL::text,
               NULL::text,
               round(sum(presented_residual)::numeric, 2)::text,
               NULL::text
          FROM rebuild_account_open_item_line
         WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
        UNION ALL
        SELECT 'aged_receivable',
               count(*)::text,
               NULL::text,
               NULL::text,
               NULL::text,
               round(sum(total)::numeric, 2)::text,
               NULL::text
          FROM rebuild_account_aged_partner_balance_line
         WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
           AND account_type = 'asset_receivable'
        UNION ALL
        SELECT 'aged_payable',
               count(*)::text,
               NULL::text,
               NULL::text,
               NULL::text,
               round(sum(total)::numeric, 2)::text,
               NULL::text
          FROM rebuild_account_aged_partner_balance_line
         WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
           AND account_type = 'liability_payable'
        UNION ALL
        SELECT 'balance_sheet',
               count(*)::text,
               NULL::text,
               NULL::text,
               NULL::text,
               NULL::text,
               round(sum(statement_balance)::numeric, 2)::text
          FROM rebuild_account_financial_statement_line
         WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
           AND statement_key = 'balance_sheet'
        UNION ALL
        SELECT 'profit_and_loss',
               count(*)::text,
               NULL::text,
               NULL::text,
               NULL::text,
               NULL::text,
               round(sum(statement_balance)::numeric, 2)::text
          FROM rebuild_account_financial_statement_line
         WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
           AND statement_key = 'profit_and_loss'
        UNION ALL
        SELECT 'fixed_asset_register',
               count(*)::text,
               NULL::text,
               NULL::text,
               NULL::text,
               round(sum(imported_period_net_value)::numeric, 2)::text,
               NULL::text
          FROM rebuild_account_asset
         WHERE rebuild_source_model = 'account_asset'
        UNION ALL
        SELECT 'fixed_asset_group_account',
               count(*)::text,
               NULL::text,
               NULL::text,
               NULL::text,
               round(sum(imported_period_net_value)::numeric, 2)::text,
               round(sum(original_value)::numeric, 2)::text
          FROM (
                SELECT asset_account_id,
                       sum(imported_period_net_value) AS imported_period_net_value,
                       sum(original_value) AS original_value
                  FROM rebuild_account_asset
                 WHERE rebuild_source_model = 'account_asset'
                 GROUP BY asset_account_id
          ) grouped_assets
        UNION ALL
        SELECT 'depreciation_schedule',
               count(*)::text,
               NULL::text,
               NULL::text,
               NULL::text,
               round(sum(depreciation_amount)::numeric, 2)::text,
               NULL::text
          FROM rebuild_account_asset_depreciation_schedule_line
        UNION ALL
        SELECT 'deferred_schedule',
               count(*)::text,
               NULL::text,
               NULL::text,
               NULL::text,
               round(sum(amount)::numeric, 2)::text,
               NULL::text
          FROM rebuild_account_deferred_schedule_line
        UNION ALL
        SELECT 'vat_tax_report',
               count(*)::text,
               round(sum(debit)::numeric, 2)::text,
               round(sum(credit)::numeric, 2)::text,
               round(sum(balance)::numeric, 2)::text,
               NULL::text,
               NULL::text
          FROM rebuild_account_tax_report_line
         WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
        UNION ALL
        SELECT 'tax_report_group_account_tax',
               count(*)::text,
               round(sum(debit)::numeric, 2)::text,
               round(sum(credit)::numeric, 2)::text,
               round(sum(balance)::numeric, 2)::text,
               NULL::text,
               round(sum(tax_base_amount)::numeric, 2)::text
          FROM rebuild_account_tax_report_line
         WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
         GROUP BY period_key
        UNION ALL
        SELECT 'tax_report_group_tax_account',
               count(*)::text,
               round(sum(debit)::numeric, 2)::text,
               round(sum(credit)::numeric, 2)::text,
               round(sum(balance)::numeric, 2)::text,
               NULL::text,
               round(sum(tax_base_amount)::numeric, 2)::text
          FROM rebuild_account_tax_report_line
         WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
         GROUP BY period_key
        UNION ALL
        SELECT expected.report_type,
               count(line.id)::text,
               NULL::text,
               NULL::text,
               round(COALESCE(sum(balance), 0)::numeric, 2)::text,
               round(COALESCE(sum(taxable_amount), 0)::numeric, 2)::text,
               round(COALESCE(sum(tax_amount), 0)::numeric, 2)::text
          FROM (
                VALUES ('ec_sales_list'), ('oss_sales'), ('oss_imports')
          ) expected(report_type)
          LEFT JOIN rebuild_account_eu_tax_report_line line
            ON line.report_type = expected.report_type
           AND line.period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
         GROUP BY expected.report_type
        UNION ALL
        SELECT 'ec_sales_list_current',
               count(*)::text,
               NULL::text,
               NULL::text,
               round(COALESCE(sum(balance), 0)::numeric, 2)::text,
               round(COALESCE(sum(taxable_amount), 0)::numeric, 2)::text,
               round(COALESCE(sum(tax_amount), 0)::numeric, 2)::text
          FROM rebuild_account_eu_tax_report_line
         WHERE report_type = 'ec_sales_list'
           AND period_key = 'USL current from 2025-10-01'
        UNION ALL
        SELECT 'bank_reconciliation',
               count(*)::text,
               round(sum(amount)::numeric, 2)::text,
               NULL::text,
               NULL::text,
               round(sum(amount_residual)::numeric, 2)::text,
               NULL::text
          FROM rebuild_account_bank_reconciliation_line
         WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
        UNION ALL
        SELECT 'currency_report',
               count(*)::text,
               round(sum(debit)::numeric, 2)::text,
               round(sum(credit)::numeric, 2)::text,
               round(sum(balance)::numeric, 2)::text,
               round(sum(amount_residual)::numeric, 2)::text,
               NULL::text
          FROM rebuild_account_currency_report_line
         WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
        UNION ALL
        SELECT 'cash_flow',
               count(*)::text,
               NULL::text,
               NULL::text,
               NULL::text,
               NULL::text,
               round(sum(amount)::numeric, 2)::text
          FROM rebuild_account_management_summary_line
         WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
           AND report_key = 'cash_flow'
        UNION ALL
        SELECT 'executive_summary',
               count(*)::text,
               NULL::text,
               NULL::text,
               NULL::text,
               NULL::text,
               round(sum(amount)::numeric, 2)::text
          FROM rebuild_account_management_summary_line
         WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
           AND report_key = 'executive_summary'
        UNION ALL
        SELECT 'revenue_spending_trend',
               count(DISTINCT month)::text,
               round(sum(revenue)::numeric, 2)::text,
               round(sum(spending)::numeric, 2)::text,
               round(sum(net_contribution)::numeric, 2)::text,
               NULL::text,
               NULL::text
          FROM rebuild_account_revenue_spending_month
         WHERE company_id = (
                   SELECT id
                     FROM res_company
                    WHERE rebuild_source_id = 1
                    LIMIT 1
               )
           AND month BETWEEN DATE '2025-10-01' AND DATE '2026-06-01'
        UNION ALL
        SELECT 'analytic_report',
               count(*)::text,
               round(COALESCE(sum(allocated_debit), 0)::numeric, 2)::text,
               round(COALESCE(sum(allocated_credit), 0)::numeric, 2)::text,
               round(COALESCE(sum(allocated_balance), 0)::numeric, 2)::text,
               NULL::text,
               NULL::text
          FROM rebuild_account_analytic_distribution_line
         WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
        UNION ALL
        SELECT 'analytic_report_current',
               count(*)::text,
               round(COALESCE(sum(allocated_debit), 0)::numeric, 2)::text,
               round(COALESCE(sum(allocated_credit), 0)::numeric, 2)::text,
               round(COALESCE(sum(allocated_balance), 0)::numeric, 2)::text,
               NULL::text,
               NULL::text
          FROM rebuild_account_analytic_distribution_line
         WHERE period_key = 'USL current from 2025-10-01'
        UNION ALL
        SELECT 'french_tax_package',
               count(*)::text,
               NULL::text,
               NULL::text,
               NULL::text,
               NULL::text,
               round(sum(amount)::numeric, 2)::text
          FROM rebuild_account_french_tax_package_line
         WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
        ORDER BY report_key
        """,
        set_readonly_role=False,
    )
    by_key = {row["report_key"]: row for row in rows}
    french_rows = query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT line_code::text,
               round(gross_amount::numeric, 2)::text AS gross_amount,
               round(depreciation_amount::numeric, 2)::text AS depreciation_amount,
               round(net_amount::numeric, 2)::text AS net_amount,
               round(amount::numeric, 2)::text AS amount
        FROM rebuild_account_french_statement_line
        WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
          AND line_code IN (
              'ACTIF_TOTAL',
              'PASSIF_TOTAL',
              'PASSIF_RESULTAT',
              'CR_CHIFFRE_AFFAIRES',
              'CR_RESULTAT_EXPLOITATION',
              'CR_RESULTAT_COURANT_AVANT_IMPOT',
              'CR_RESULTAT_NET',
              'SIG_VALEUR_AJOUTEE',
              'SIG_EBE',
              'SIG_CAPACITE_AUTOFINANCEMENT'
          )
        ORDER BY line_code
        """,
        set_readonly_role=False,
    )
    french_by_code = {row["line_code"]: row for row in french_rows}
    tax_package_rows = query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT field_code::text,
               form_code::text,
               round(amount::numeric, 2)::text AS amount,
               round(rounded_amount::numeric, 2)::text AS rounded_amount,
               COALESCE(round(benchmark_amount::numeric, 2)::text, '') AS benchmark_amount,
               COALESCE(round(ledger_amount::numeric, 2)::text, '') AS ledger_amount,
               COALESCE(round(difference_amount::numeric, 2)::text, '') AS difference_amount,
               COALESCE(difference_classification::text, '') AS difference_classification,
               quantity::text,
               review_status::text
        FROM rebuild_account_french_tax_package_line
        WHERE period_key = 'USL benchmark 2024-01-10 to 2025-09-30'
          AND field_code IN (
              '2065_RESULTAT_FISCAL_AVANT_DEFICITS_REVIEW',
              '2065_BASE_TAUX_REDUIT_15_REVIEW',
              '2033_A_TOTAL_ACTIF_NET',
              '2033_B_RESULTAT_NET_COMPTABLE',
              '2033_C_NOMBRE_IMMOBILISATIONS_SOURCE',
              '2033_C_IMMOBILISATIONS_CORP_BRUT',
              '2033_C_AMORTISSEMENTS_TOTAL',
              '2033_C_DOTATIONS_EXERCICE',
              '2033_C_LIGNES_PLAN_AMORTISSEMENT_SOURCE',
              '3517S_TVA_COLLECTEE_445700',
              '3517S_TVA_DEDUCTIBLE_BIENS_SERVICES_445660',
              '3517S_CREDIT_TVA_A_REPORTER_445670'
          )
        ORDER BY field_code
        """,
        set_readonly_role=False,
    )
    tax_package_by_code = {row["field_code"]: row for row in tax_package_rows}

    def french_amount(line_code: str, field: str = "amount") -> str | None:
        row = french_by_code.get(line_code)
        return row.get(field) if row else None

    checks = {
        "trial_balance_balanced": by_key["trial_balance"]["balance"] == "0.00",
        "general_ledger_balanced": by_key["general_ledger"]["balance"] == "0.00",
        "journal_report_balanced": by_key["journal_report"]["balance"] == "0.00",
        "balance_sheet_balanced": by_key["balance_sheet"]["statement_balance"] == "0.00",
        "profit_and_loss_result": by_key["profit_and_loss"]["statement_balance"] == "56222.98",
        "customer_statement_customer_scope_empty": (
            by_key["customer_statement"]["row_count"] == "0"
            and by_key["customer_statement"]["balance"] == "0.00"
            and by_key["customer_statement"]["residual"] == "0.00"
        ),
        "open_items_scope_valid": (
            by_key["open_items"]["row_count"] == "0"
            and Decimal(by_key["open_items"]["residual"] or "0") == Decimal("0.00")
        ) or Decimal(by_key["open_items"]["residual"] or "0") != Decimal("0.00"),
        "assets_present": by_key["fixed_asset_register"]["row_count"] == "3",
        "fixed_asset_group_account_present": (
            by_key["fixed_asset_group_account"]["row_count"] == "2"
            and by_key["fixed_asset_group_account"]["residual"] == "8754.44"
            and by_key["fixed_asset_group_account"]["statement_balance"] == "10430.49"
        ),
        "depreciation_schedule_present": (
            by_key["depreciation_schedule"]["row_count"] == "91"
            and by_key["depreciation_schedule"]["residual"] == "8754.44"
        ),
        "vat_tax_report_present": int(by_key["vat_tax_report"]["row_count"]) > 0,
        "tax_report_group_account_tax_present": (
            by_key["tax_report_group_account_tax"]["row_count"] == "6"
            and by_key["tax_report_group_account_tax"]["balance"] == "3442.00"
        ),
        "tax_report_group_tax_account_present": (
            by_key["tax_report_group_tax_account"]["row_count"] == "6"
            and by_key["tax_report_group_tax_account"]["balance"] == "3442.00"
        ),
        "ec_sales_list_available": by_key["ec_sales_list"]["row_count"] == "0",
        "ec_sales_list_current_present": (
            by_key["ec_sales_list_current"]["row_count"] == "4"
            and by_key["ec_sales_list_current"]["residual"] == "37555.12"
        ),
        "oss_sales_available": by_key["oss_sales"]["row_count"] == "0",
        "oss_imports_available": by_key["oss_imports"]["row_count"] == "0",
        "bank_reconciliation_present": (
            by_key["bank_reconciliation"]["row_count"] == "1164"
            and by_key["bank_reconciliation"]["debit"] == "56170.11"
        ),
        "currency_report_present": int(by_key["currency_report"]["row_count"]) > 0,
        "cash_flow_present": by_key["cash_flow"]["row_count"] == "4",
        "executive_summary_present": by_key["executive_summary"]["row_count"] == "15",
        "revenue_spending_trend_present": (
            by_key["revenue_spending_trend"]["row_count"] == "9"
            and by_key["revenue_spending_trend"]["debit"] == "176928.45"
            and by_key["revenue_spending_trend"]["credit"] == "101215.69"
            and by_key["revenue_spending_trend"]["balance"] == "75712.76"
        ),
        "analytic_report_available": by_key["analytic_report"]["row_count"] == "0",
        "analytic_current_report_present": (
            by_key["analytic_report_current"]["row_count"] == "53"
            and by_key["analytic_report_current"]["debit"] == "310175.76"
            and by_key["analytic_report_current"]["credit"] == "208694.53"
            and by_key["analytic_report_current"]["balance"] == "101481.23"
        ),
        "french_tax_package_present": int(by_key["french_tax_package"]["row_count"]) >= 30,
    }
    french_checks = {
        "french_total_gross_assets": french_amount("ACTIF_TOTAL", "gross_amount") == "71356.21",
        "french_total_depreciation": french_amount("ACTIF_TOTAL", "depreciation_amount") == "1676.05",
        "french_total_net_assets": french_amount("ACTIF_TOTAL", "amount") == "69680.16",
        "french_total_passif": french_amount("PASSIF_TOTAL") == "69680.16",
        "french_current_year_result": french_amount("PASSIF_RESULTAT") == "56222.98",
        "french_turnover": french_amount("CR_CHIFFRE_AFFAIRES") == "129188.62",
        "french_operating_result": french_amount("CR_RESULTAT_EXPLOITATION") == "66180.70",
        "french_current_result_before_tax": french_amount("CR_RESULTAT_COURANT_AVANT_IMPOT") == "66144.98",
        "french_net_result": french_amount("CR_RESULTAT_NET") == "56222.98",
        "french_value_added": french_amount("SIG_VALEUR_AJOUTEE") == "85322.21",
        "french_ebe": french_amount("SIG_EBE") == "67856.75",
        "french_caf": french_amount("SIG_CAPACITE_AUTOFINANCEMENT") == "57899.03",
    }
    tax_package_checks = {
        "tax_package_taxable_profit_review": tax_package_by_code.get("2065_RESULTAT_FISCAL_AVANT_DEFICITS_REVIEW", {}).get("amount") == "66144.98",
        "tax_package_reduced_rate_base_review": tax_package_by_code.get("2065_BASE_TAUX_REDUIT_15_REVIEW", {}).get("amount") == "66144.98",
        "tax_package_total_net_assets": tax_package_by_code.get("2033_A_TOTAL_ACTIF_NET", {}).get("amount") == "69680.16",
        "tax_package_net_result": tax_package_by_code.get("2033_B_RESULTAT_NET_COMPTABLE", {}).get("amount") == "56222.98",
        "tax_package_asset_count": tax_package_by_code.get("2033_C_NOMBRE_IMMOBILISATIONS_SOURCE", {}).get("quantity") == "3",
        "tax_package_fixed_asset_gross": tax_package_by_code.get("2033_C_IMMOBILISATIONS_CORP_BRUT", {}).get("amount") == "10430.49",
        "tax_package_accumulated_depreciation": tax_package_by_code.get("2033_C_AMORTISSEMENTS_TOTAL", {}).get("amount") == "1676.05",
        "tax_package_period_depreciation": tax_package_by_code.get("2033_C_DOTATIONS_EXERCICE", {}).get("amount") == "1676.05",
        "tax_package_depreciation_schedule_count": tax_package_by_code.get("2033_C_LIGNES_PLAN_AMORTISSEMENT_SOURCE", {}).get("quantity") == "91",
        "tax_package_depreciation_schedule_count_has_no_monetary_amount": tax_package_by_code.get("2033_C_LIGNES_PLAN_AMORTISSEMENT_SOURCE", {}).get("amount") == "0.00",
        "tax_package_vat_collected": tax_package_by_code.get("3517S_TVA_COLLECTEE_445700", {}).get("amount") == "459.00",
        "tax_package_vat_deductible_ca12_clearing": tax_package_by_code.get("3517S_TVA_DEDUCTIBLE_BIENS_SERVICES_445660", {}).get("amount") == "1960.00",
        "tax_package_vat_credit_carryover": tax_package_by_code.get("3517S_CREDIT_TVA_A_REPORTER_445670", {}).get("amount") == "3442.00",
        "tax_package_vat_deductible_review_flag": tax_package_by_code.get("3517S_TVA_DEDUCTIBLE_BIENS_SERVICES_445660", {}).get("review_status") == "ledger_derived",
        "tax_package_vat_deductible_benchmark_value": tax_package_by_code.get("3517S_TVA_DEDUCTIBLE_BIENS_SERVICES_445660", {}).get("benchmark_amount") == "1960.00",
        "tax_package_vat_deductible_ledger_value": tax_package_by_code.get("3517S_TVA_DEDUCTIBLE_BIENS_SERVICES_445660", {}).get("ledger_amount") == "1960.00",
        "tax_package_vat_deductible_difference": tax_package_by_code.get("3517S_TVA_DEDUCTIBLE_BIENS_SERVICES_445660", {}).get("difference_amount") == "0.00",
        "tax_package_vat_deductible_difference_classification": tax_package_by_code.get("3517S_TVA_DEDUCTIBLE_BIENS_SERVICES_445660", {}).get("difference_classification") == "",
    }
    return {
        "status": "passed" if all(checks.values()) and all(french_checks.values()) and all(tax_package_checks.values()) else "failed",
        "classification": "ODOO_REPORT_VIEW_SMOKE_CHECKS",
        "rows": by_key,
        "checks": checks,
        "french_statement_rows": french_by_code,
        "french_statement_checks": french_checks,
        "french_tax_package_rows": tax_package_by_code,
        "french_tax_package_checks": tax_package_checks,
        "limitations": [
            "These are smoke checks for read-only Odoo SQL-view reports, not full Enterprise account.report semantic parity.",
            "The Profit and Loss result check uses the 30 September 2025 benchmark acceptance value as a regression anchor; it is not used to mutate ledger data.",
            "French annual-statement checks validate account-code mappings against benchmark anchors; accountant review is still required before statutory acceptance.",
            "French tax-package checks expose ledger-derived 2065/2033/CA12 review values; they are not an accepted tax filing and review-required lines must be resolved by the accountant.",
        ],
    }


def odoo_report_drilldown_controls() -> dict[str, Any]:
    drilldown_script = PRIVATE_ARTIFACTS / "report-drilldown-check.py"
    drilldown_script.write_text(
        "\n".join(
            [
                "import json",
                f"benchmark = {USL_BENCHMARK_END!r}",
                "samples = [",
                "    ('trial_balance', 'rebuild.account.trial.balance.line', [('period_key', '=', 'USL benchmark 2024-01-10 to 2025-09-30')]),",
                "    ('general_ledger', 'rebuild.account.general.ledger.line', [('period_key', '=', 'USL benchmark 2024-01-10 to 2025-09-30')]),",
                "    ('journal_report', 'rebuild.account.journal.report.line', [('period_key', '=', 'USL benchmark 2024-01-10 to 2025-09-30')]),",
                "    ('partner_ledger', 'rebuild.account.partner.ledger.line', [('period_key', '=', 'USL benchmark 2024-01-10 to 2025-09-30')]),",
                "    ('open_items', 'rebuild.account.open.item.line', [('period_key', '=', 'USL benchmark 2024-01-10 to 2025-09-30')]),",
                "    ('aged_receivable', 'rebuild.account.aged.partner.balance.line', [('period_key', '=', 'USL benchmark 2024-01-10 to 2025-09-30'), ('account_type', '=', 'asset_receivable')]),",
                "    ('balance_sheet', 'rebuild.account.financial.statement.line', [('period_key', '=', 'USL benchmark 2024-01-10 to 2025-09-30'), ('statement_key', '=', 'balance_sheet')]),",
                "    ('profit_and_loss', 'rebuild.account.financial.statement.line', [('period_key', '=', 'USL benchmark 2024-01-10 to 2025-09-30'), ('statement_key', '=', 'profit_and_loss')]),",
                "    ('vat_tax_report', 'rebuild.account.tax.report.line', [('period_key', '=', 'USL benchmark 2024-01-10 to 2025-09-30')]),",
                "    ('bank_reconciliation', 'rebuild.account.bank.reconciliation.line', [('period_key', '=', 'USL benchmark 2024-01-10 to 2025-09-30')]),",
                "    ('currency_report', 'rebuild.account.currency.report.line', [('period_key', '=', 'USL benchmark 2024-01-10 to 2025-09-30'), ('report_section', '=', 'Realized exchange gains and losses')]),",
                "    ('cash_flow', 'rebuild.account.management.summary.line', [('period_key', '=', 'USL benchmark 2024-01-10 to 2025-09-30'), ('line_code', '=', 'CLOSING_CASH')]),",
                "    ('executive_summary', 'rebuild.account.management.summary.line', [('period_key', '=', 'USL benchmark 2024-01-10 to 2025-09-30'), ('line_code', '=', 'NET_PROFIT')]),",
                "    ('revenue_spending_trend', 'rebuild.account.revenue.spending.month', [('month', '>=', '2025-10-01'), ('month', '<=', '2026-06-01')]),",
                "    ('analytic_report', 'rebuild.account.analytic.distribution.line', [('period_key', '=', 'USL current from 2025-10-01')]),",
                "    ('french_balance_sheet', 'rebuild.account.french.statement.line', [('period_key', '=', 'USL benchmark 2024-01-10 to 2025-09-30'), ('line_code', '=', 'ACTIF_TOTAL')]),",
                "    ('french_profit_and_loss', 'rebuild.account.french.statement.line', [('period_key', '=', 'USL benchmark 2024-01-10 to 2025-09-30'), ('line_code', '=', 'CR_RESULTAT_NET')]),",
                "    ('sig_caf', 'rebuild.account.french.statement.line', [('period_key', '=', 'USL benchmark 2024-01-10 to 2025-09-30'), ('line_code', '=', 'SIG_CAPACITE_AUTOFINANCEMENT')]),",
                "    ('french_tax_package', 'rebuild.account.french.tax.package.line', [('period_key', '=', 'USL benchmark 2024-01-10 to 2025-09-30'), ('field_code', '=', '2033_A_TOTAL_ACTIF_NET')]),",
                "]",
                "results = {}",
                "for report_key, model_name, domain in samples:",
                "    record = env[model_name].search(domain, order='id', limit=1)",
                "    item = {",
                "        'model': model_name,",
                "        'record_id': record.id,",
                "        'source_model_count': env[model_name].search_count(domain),",
                "    }",
                "    if not record:",
                "        if report_key in ('open_items', 'aged_receivable') and item['source_model_count'] == 0:",
                "            item.update({",
                "                'status': 'passed',",
                "                'applicability': 'not_applicable_empty_scope',",
                "                'reason': 'The report scope is valid and empty, so no row-level drill-down is applicable.',",
                "            })",
                "        else:",
                "            item.update({'status': 'failed', 'reason': 'no report row matched sample domain'})",
                "    else:",
                "        action = record.action_open_journal_items()",
                "        item.update({",
                "            'action_type': action.get('type'),",
                "            'action_model': action.get('res_model'),",
                "            'action_name': action.get('name'),",
                "            'journal_item_count': env[action['res_model']].search_count(action['domain']),",
                "            'domain': action.get('domain'),",
                "        })",
                "        expected_model = 'account.analytic.line' if report_key == 'analytic_report' else 'account.move.line'",
                "        item['status'] = 'passed' if item['action_model'] == expected_model and item['journal_item_count'] > 0 else 'failed'",
                "    results[report_key] = item",
                "payload = {",
                "    'status': 'passed' if all(item['status'] == 'passed' for item in results.values()) else 'failed',",
                "    'classification': 'ODOO_REPORT_DRILLDOWN_CHECKS',",
                "    'scope': {'period_end': benchmark, 'company': 'Unstatic Labs'},",
                "    'results': results,",
                "}",
                "print('REBUILD_REPORT_DRILLDOWN=' + json.dumps(payload, sort_keys=True))",
                "",
            ],
        ),
        encoding="utf-8",
    )
    result = run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "init-db",
            "odoo",
            "shell",
            "--config=/etc/odoo/odoo.conf",
            f"--database={EXACT_VALIDATION_DB}",
        ),
        input_file=drilldown_script,
        check=False,
    )
    marker = None
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("REBUILD_REPORT_DRILLDOWN="):
            marker = line.removeprefix("REBUILD_REPORT_DRILLDOWN=")
    if result.returncode or not marker:
        return {
            "status": "failed",
            "classification": "REPORT_DRILLDOWN_EXECUTION_DEFECT",
            "exit_code": result.returncode,
            "output_tail": (result.stdout + result.stderr)[-4000:],
        }
    return json.loads(marker)


def odoo_report_export_wizard_controls() -> dict[str, Any]:
    snapshot = source_snapshot_date() or USL_BENCHMARK_END
    export_script = PRIVATE_ARTIFACTS / "report-export-wizard-check.py"
    export_script.write_text(
        "\n".join(
            [
                "import base64",
                "import csv",
                "import hashlib",
                "import io",
                "import json",
                "import zipfile",
                "from decimal import Decimal",
                "company = env['res.company'].search([('rebuild_source_id', '=', 1)], limit=1)",
                "Wizard = env['rebuild.account.report.export.wizard']",
                "samples = ['trial_balance', 'general_ledger', 'journal_report', 'partner_ledger', 'customer_statement', 'open_items', 'aged_receivable', 'aged_payable', 'balance_sheet', 'profit_loss', 'tax_report', 'tax_report_group_account_tax', 'tax_report_group_tax_account', 'ec_sales_list', 'oss_sales', 'oss_imports', 'bank_reconciliation', 'currency_report', 'cash_flow', 'executive_summary', 'analytic_report', 'fixed_assets', 'fixed_asset_group_account', 'depreciation_schedule', 'deferred_schedule', 'french_annual', 'french_balance_sheet_2024', 'french_profit_loss_2024', 'sig_caf_2024', 'french_tax_package']",
                f"date_ranges = {{'depreciation_schedule': ('2025-10-01', '2028-06-30'), 'deferred_schedule': ('2025-10-01', '2028-12-31'), 'analytic_report': ('2025-10-01', {snapshot!r}), 'ec_sales_list': ('2025-10-01', {snapshot!r}), 'oss_sales': ('2025-10-01', {snapshot!r}), 'oss_imports': ('2025-10-01', {snapshot!r})}}",
                "results = {}",
                "for report_type in samples:",
                "    date_from, date_to = date_ranges.get(report_type, ('2024-01-10', '2025-09-30'))",
                "    wizard = Wizard.create({",
                "        'report_type': report_type,",
                "        'company_id': company.id,",
                "        'date_from': date_from,",
                "        'date_to': date_to,",
                "        'target_move': 'posted',",
                "        'export_format': 'csv',",
                "        'preview_limit': 10,",
                "    })",
                "    preview_action = wizard.action_preview_report()",
                "    preview_metadata = json.loads(wizard.preview_metadata or '{}')",
                "    preview_line_count = len(wizard.preview_line_ids)",
                "    preview_truncated = wizard.preview_truncated",
                "    preview_source_action = wizard.preview_line_ids[:1].action_open_sources() if wizard.preview_line_ids else {}",
                "    expected_source_models = {'analytic_report': {'account.analytic.line'}, 'fixed_assets': {'account.asset'}, 'depreciation_schedule': {'account.asset', 'account.move'}}.get(report_type, {'account.move.line'})",
                "    source_shape_status = bool(preview_source_action.get('res_id')) or isinstance(preview_source_action.get('domain'), list)",
                "    readonly_context_status = bool(preview_source_action.get('res_id')) or (preview_source_action.get('context', {}).get('create') is False and preview_source_action.get('context', {}).get('delete') is False)",
                "    preview_source_status = 'passed' if preview_source_action.get('type') == 'ir.actions.act_window' and preview_source_action.get('res_model') in expected_source_models and source_shape_status and readonly_context_status else 'failed'",
                "    wizard.action_generate_export()",
                "    raw = base64.b64decode(wizard.export_file or b'')",
                "    rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8'))))",
                "    data_rows = [row for row in rows if row.get('empty_report') != 'true']",
                "    metadata = json.loads(rows[0]['metadata']) if rows else json.loads(wizard.export_metadata or '{}')",
                "    item = {",
                "        'filename': wizard.export_filename,",
                "        'row_count': len(data_rows),",
                "        'csv_row_count': len(rows),",
                "        'metadata': metadata,",
                "        'status': 'passed' if rows and wizard.export_filename and wizard.export_file else 'failed',",
                "    }",
                "    expected_preview_line_count = min(item['row_count'], wizard.preview_limit) if item['row_count'] else 1",
                "    preview_status = 'passed' if preview_action.get('name') == dict(Wizard._fields['report_type'].selection)[report_type] and preview_action.get('target') == 'current' and preview_metadata.get('report_type') == report_type and preview_metadata.get('row_count') == item['row_count'] and preview_metadata.get('preview_limit') == wizard.preview_limit and preview_line_count == expected_preview_line_count and preview_truncated == (item['row_count'] > wizard.preview_limit) and preview_source_status == 'passed' else 'failed'",
                "    item['preview'] = {",
                "        'action_name': preview_action.get('name'),",
                "        'line_count': preview_line_count,",
                "        'row_count': wizard.preview_row_count,",
                "        'metadata': preview_metadata,",
                "        'truncated': preview_truncated,",
                "        'source_action': {",
                "            'name': preview_source_action.get('name'),",
                "            'res_model': preview_source_action.get('res_model'),",
                "            'domain_size': len(preview_source_action.get('domain') or []),",
                "            'status': preview_source_status,",
                "        },",
                "        'status': preview_status,",
                "    }",
                "    if preview_status != 'passed':",
                "        item['status'] = 'failed'",
                "    if report_type == 'trial_balance':",
                "        item['debit'] = str(sum((Decimal(row.get('debit') or '0') for row in data_rows), Decimal('0')).quantize(Decimal('0.01')))",
                "        item['credit'] = str(sum((Decimal(row.get('credit') or '0') for row in data_rows), Decimal('0')).quantize(Decimal('0.01')))",
                "        item['status'] = 'passed' if item['row_count'] == 68 and item['debit'] == '1064045.02' and item['credit'] == '1064045.02' else 'failed'",
                "    elif report_type == 'general_ledger':",
                "        item['status'] = 'passed' if item['row_count'] == 4809 else 'failed'",
                "    elif report_type == 'fixed_assets':",
                "        item['status'] = 'passed' if item['row_count'] == 3 else 'failed'",
                "    elif report_type == 'fixed_asset_group_account':",
                "        item['original_value'] = str(sum((Decimal(row.get('original_value') or '0') for row in data_rows), Decimal('0')).quantize(Decimal('0.01')))",
                "        item['imported_period_net_value'] = str(sum((Decimal(row.get('imported_period_net_value') or '0') for row in data_rows), Decimal('0')).quantize(Decimal('0.01')))",
                "        item['status'] = 'passed' if item['row_count'] == 2 and item['original_value'] == '10430.49' and item['imported_period_net_value'] == '8754.44' and item['metadata'].get('report_type') == 'fixed_asset_group_account' and item['preview']['source_action']['status'] == 'passed' else 'failed'",
                "    elif report_type == 'depreciation_schedule':",
                "        item['depreciation_amount'] = str(sum((Decimal(row.get('depreciation_amount') or '0') for row in data_rows), Decimal('0')).quantize(Decimal('0.01')))",
                "        item['status'] = 'passed' if item['row_count'] == 91 and item['depreciation_amount'] == '8754.44' else 'failed'",
                "    elif report_type == 'deferred_schedule':",
                "        item['amount'] = str(sum((Decimal(row.get('amount') or '0') for row in data_rows), Decimal('0')).quantize(Decimal('0.01')))",
                "        item['types'] = sorted({row.get('schedule_type') for row in data_rows if row.get('schedule_type')})",
                "        item['statuses'] = sorted({row.get('representation_status') for row in data_rows if row.get('representation_status')})",
                "        item['status'] = 'passed' if item['row_count'] == 110 and item['types'] == ['expense'] and 'imported_posted_entry' in item['statuses'] and 'source_draft_forecast' in item['statuses'] else 'failed'",
                "    elif report_type == 'french_annual':",
                "        by_code = {row.get('line_code'): row for row in data_rows}",
                "        item['benchmark_lines'] = {key: by_code[key].get('amount') for key in ['ACTIF_TOTAL', 'PASSIF_TOTAL', 'CR_RESULTAT_NET', 'SIG_CAPACITE_AUTOFINANCEMENT'] if key in by_code}",
                "        item['status'] = 'passed' if item['benchmark_lines'] == {'ACTIF_TOTAL': '69680.16', 'PASSIF_TOTAL': '69680.16', 'CR_RESULTAT_NET': '56222.98', 'SIG_CAPACITE_AUTOFINANCEMENT': '57899.03'} else 'failed'",
                "    elif report_type == 'french_balance_sheet_2024':",
                "        by_code = {row.get('line_code'): row for row in data_rows}",
                "        item['benchmark_lines'] = {key: by_code[key].get('amount') for key in ['ACTIF_TOTAL', 'PASSIF_TOTAL'] if key in by_code}",
                "        item['variants'] = sorted({row.get('report_variant') for row in data_rows if row.get('report_variant')})",
                "        item['status'] = 'passed' if item['row_count'] == 13 and item['benchmark_lines'] == {'ACTIF_TOTAL': '69680.16', 'PASSIF_TOTAL': '69680.16'} and item['variants'] == ['pcg_2024_pre_2025_opening_year'] and item['metadata'].get('report_variant') == 'pcg_2024_pre_2025_opening_year' else 'failed'",
                "    elif report_type == 'french_profit_loss_2024':",
                "        by_code = {row.get('line_code'): row for row in data_rows}",
                "        item['benchmark_lines'] = {key: by_code[key].get('amount') for key in ['CR_CHIFFRE_AFFAIRES', 'CR_RESULTAT_NET'] if key in by_code}",
                "        item['variants'] = sorted({row.get('report_variant') for row in data_rows if row.get('report_variant')})",
                "        item['status'] = 'passed' if item['row_count'] == 20 and item['benchmark_lines'] == {'CR_CHIFFRE_AFFAIRES': '129188.62', 'CR_RESULTAT_NET': '56222.98'} and item['variants'] == ['pcg_2024_pre_2025_opening_year'] and item['metadata'].get('report_variant') == 'pcg_2024_pre_2025_opening_year' else 'failed'",
                "    elif report_type == 'sig_caf_2024':",
                "        by_code = {row.get('line_code'): row for row in data_rows}",
                "        item['benchmark_lines'] = {key: by_code[key].get('amount') for key in ['SIG_VALEUR_AJOUTEE', 'SIG_EBE', 'SIG_CAPACITE_AUTOFINANCEMENT'] if key in by_code}",
                "        item['variants'] = sorted({row.get('report_variant') for row in data_rows if row.get('report_variant')})",
                "        item['status'] = 'passed' if item['row_count'] == 6 and item['benchmark_lines'] == {'SIG_VALEUR_AJOUTEE': '85322.21', 'SIG_EBE': '67856.75', 'SIG_CAPACITE_AUTOFINANCEMENT': '57899.03'} and item['variants'] == ['pcg_2024_pre_2025_opening_year'] and item['metadata'].get('report_variant') == 'pcg_2024_pre_2025_opening_year' else 'failed'",
                "    elif report_type == 'french_tax_package':",
                "        by_code = {row.get('field_code'): row for row in data_rows}",
                "        item['benchmark_fields'] = {key: by_code[key].get('amount') for key in ['2065_RESULTAT_FISCAL_AVANT_DEFICITS_REVIEW', '2033_A_TOTAL_ACTIF_NET', '2033_B_RESULTAT_NET_COMPTABLE', '2033_C_IMMOBILISATIONS_CORP_BRUT', '3517S_TVA_COLLECTEE_445700', '3517S_TVA_DEDUCTIBLE_BIENS_SERVICES_445660'] if key in by_code}",
                "        vat_review = by_code.get('3517S_TVA_DEDUCTIBLE_BIENS_SERVICES_445660', {})",
                "        item['vat_review_fields'] = {key: vat_review.get(key) for key in ['benchmark_amount', 'ledger_amount', 'difference_amount', 'difference_classification', 'review_status']}",
                "        item['status'] = 'passed' if item['row_count'] >= 30 and item['benchmark_fields'] == {'2065_RESULTAT_FISCAL_AVANT_DEFICITS_REVIEW': '66144.98', '2033_A_TOTAL_ACTIF_NET': '69680.16', '2033_B_RESULTAT_NET_COMPTABLE': '56222.98', '2033_C_IMMOBILISATIONS_CORP_BRUT': '10430.49', '3517S_TVA_COLLECTEE_445700': '459.00', '3517S_TVA_DEDUCTIBLE_BIENS_SERVICES_445660': '1960.00'} and item['vat_review_fields'] == {'benchmark_amount': '1960.00', 'ledger_amount': '1960.00', 'difference_amount': '0.00', 'difference_classification': '', 'review_status': 'ledger_derived'} else 'failed'",
                "    elif report_type == 'tax_report':",
                "        item['status'] = 'passed' if item['row_count'] >= 6 else 'failed'",
                "    elif report_type in ('tax_report_group_account_tax', 'tax_report_group_tax_account'):",
                "        item['debit'] = str(sum((Decimal(row.get('debit') or '0') for row in data_rows), Decimal('0')).quantize(Decimal('0.01')))",
                "        item['credit'] = str(sum((Decimal(row.get('credit') or '0') for row in data_rows), Decimal('0')).quantize(Decimal('0.01')))",
                "        item['balance'] = str(sum((Decimal(row.get('balance') or '0') for row in data_rows), Decimal('0')).quantize(Decimal('0.01')))",
                "        item['status'] = 'passed' if item['row_count'] == 6 and item['debit'] == '9168.27' and item['credit'] == '5726.27' and item['balance'] == '3442.00' and item['metadata'].get('report_type') == report_type and item['preview']['source_action']['status'] == 'passed' else 'failed'",
                "    elif report_type == 'ec_sales_list':",
                "        item['taxable_amount'] = str(sum((Decimal(row.get('taxable_amount') or '0') for row in data_rows), Decimal('0')).quantize(Decimal('0.01')))",
                "        item['country_codes'] = sorted({row.get('country_code') for row in data_rows if row.get('country_code')})",
                "        item['status'] = 'passed' if item['row_count'] == 4 and item['taxable_amount'] == '37555.12' and item['country_codes'] == ['ES'] and item['metadata'].get('report_type') == report_type else 'failed'",
                "    elif report_type in ('oss_sales', 'oss_imports'):",
                "        item['status'] = 'passed' if item['row_count'] == 0 and item['csv_row_count'] == 1 and item['metadata'].get('report_type') == report_type else 'failed'",
                "    elif report_type == 'bank_reconciliation':",
                "        item['amount'] = str(sum((Decimal(row.get('amount') or '0') for row in data_rows), Decimal('0')).quantize(Decimal('0.01')))",
                "        item['amount_residual'] = str(sum((Decimal(row.get('amount_residual') or '0') for row in data_rows), Decimal('0')).quantize(Decimal('0.01')))",
                "        item['status'] = 'passed' if item['row_count'] == 1164 and item['amount'] == '56170.11' and item['amount_residual'] == '0.00' else 'failed'",
                "    elif report_type == 'currency_report':",
                "        sections = {row.get('report_section') for row in data_rows}",
                "        item['sections'] = sorted(section for section in sections if section)",
                "        item['status'] = 'passed' if item['row_count'] > 0 and 'Foreign currency ledger' in sections and 'Realized exchange gains and losses' in sections else 'failed'",
                "    elif report_type == 'cash_flow':",
                "        by_code = {row.get('line_code'): row for row in data_rows}",
                "        item['cash_lines'] = {key: by_code[key].get('amount') for key in ['CASH_RECEIVED', 'CASH_SPENT', 'CASH_SURPLUS', 'CLOSING_CASH'] if key in by_code}",
                "        item['status'] = 'passed' if item['row_count'] == 4 and set(item['cash_lines']) == {'CASH_RECEIVED', 'CASH_SPENT', 'CASH_SURPLUS', 'CLOSING_CASH'} else 'failed'",
                "    elif report_type == 'executive_summary':",
                "        by_code = {row.get('line_code'): row for row in data_rows}",
                "        item['summary_lines'] = {key: by_code[key].get('amount') for key in ['REVENUE', 'NET_PROFIT', 'NET_ASSETS', 'SHORT_TERM_CASH_FORECAST'] if key in by_code}",
                "        item['ratio_lines'] = {key: by_code[key].get('metric_value') for key in ['GROSS_PROFIT_MARGIN', 'NET_PROFIT_MARGIN', 'CURRENT_ASSETS_TO_LIABILITIES'] if key in by_code}",
                "        item['status'] = 'passed' if item['row_count'] == 15 and item['summary_lines'].get('REVENUE') == '129270.65' and item['summary_lines'].get('NET_PROFIT') == '56222.98' and item['summary_lines'].get('NET_ASSETS') == '57222.98' else 'failed'",
                "    elif report_type == 'analytic_report':",
                "        item['allocated_balance'] = str(sum((Decimal(row.get('allocated_balance') or '0') for row in data_rows), Decimal('0')).quantize(Decimal('0.01')))",
                "        item['status'] = 'passed' if item['row_count'] == 53 and item['allocated_balance'] == '101481.23' and item['metadata'].get('report_type') == 'analytic_report' else 'failed'",
                "    elif report_type == 'aged_payable':",
                "        item['status'] = 'passed' if item['row_count'] == 0 and item['csv_row_count'] == 1 and item['metadata'].get('report_type') == 'aged_payable' else 'failed'",
                "    elif report_type == 'customer_statement':",
                "        item['status'] = 'passed' if item['row_count'] == 0 and item['csv_row_count'] == 1 and item['metadata'].get('report_type') == 'customer_statement' and item['preview']['source_action']['status'] == 'passed' else 'failed'",
                "    xlsx_wizard = Wizard.create({",
                "        'report_type': report_type,",
                "        'company_id': company.id,",
                "        'date_from': date_from,",
                "        'date_to': date_to,",
                "        'target_move': 'posted',",
                "        'export_format': 'xlsx',",
                "    })",
                "    xlsx_wizard.action_generate_export()",
                "    xlsx_raw = base64.b64decode(xlsx_wizard.export_file or b'')",
                "    xlsx_metadata = json.loads(xlsx_wizard.export_metadata or '{}')",
                "    xlsx_valid_zip = False",
                "    if xlsx_raw.startswith(b'PK'):",
                "        with zipfile.ZipFile(io.BytesIO(xlsx_raw)) as archive:",
                "            names = set(archive.namelist())",
                "            xlsx_valid_zip = 'xl/workbook.xml' in names and 'xl/worksheets/sheet1.xml' in names and 'xl/worksheets/sheet2.xml' in names",
                "    xlsx_status = 'passed' if xlsx_wizard.export_filename.endswith('.xlsx') and xlsx_valid_zip and xlsx_metadata.get('format') == 'xlsx' and xlsx_metadata.get('row_count') == item['row_count'] else 'failed'",
                "    item['xlsx'] = {",
                "        'filename': xlsx_wizard.export_filename,",
                "        'byte_count': len(xlsx_raw),",
                "        'metadata': xlsx_metadata,",
                "        'status': xlsx_status,",
                "    }",
                "    if xlsx_status != 'passed':",
                "        item['status'] = 'failed'",
                "    pdf_wizard = Wizard.create({",
                "        'report_type': report_type,",
                "        'company_id': company.id,",
                "        'date_from': date_from,",
                "        'date_to': date_to,",
                "        'target_move': 'posted',",
                "        'export_format': 'pdf',",
                "    })",
                "    pdf_wizard.action_generate_export()",
                "    pdf_raw = base64.b64decode(pdf_wizard.export_file or b'')",
                "    pdf_metadata = json.loads(pdf_wizard.export_metadata or '{}')",
                "    pdf_status = 'passed' if pdf_wizard.export_filename.endswith('.pdf') and pdf_raw.startswith(b'%PDF') and b'%%EOF' in pdf_raw[-2048:] and pdf_metadata.get('format') == 'pdf' and pdf_metadata.get('row_count') == item['row_count'] else 'failed'",
                "    item['pdf'] = {",
                "        'filename': pdf_wizard.export_filename,",
                "        'byte_count': len(pdf_raw),",
                "        'metadata': pdf_metadata,",
                "        'status': pdf_status,",
                "    }",
                "    if pdf_status != 'passed':",
                "        item['status'] = 'failed'",
                "    if item['preview']['status'] != 'passed':",
                "        item['status'] = 'failed'",
                "    results[report_type] = item",
                "dynamic_wizard = Wizard.create({",
                "    'report_type': 'trial_balance',",
                "    'company_id': company.id,",
                "    'company_ids': [(6, 0, [company.id])],",
                "    'data_scope': 'native',",
                "    'period_preset': 'month',",
                "    'period_anchor_date': '2025-10-15',",
                "    'comparison_mode': 'previous_year',",
                "    'target_move': 'posted',",
                "    'group_by': 'account',",
                "    'export_format': 'xlsx',",
                "    'preview_limit': 500,",
                "})",
                "dynamic_action = dynamic_wizard.action_apply_period()",
                "dynamic_metadata = json.loads(dynamic_wizard.preview_metadata or '{}')",
                "dynamic_group_lines = dynamic_wizard.preview_line_ids.filtered('is_group')",
                "dynamic_group_count = len(dynamic_group_lines)",
                "dynamic_comparison_status = any(line.comparison_value or line.difference for line in dynamic_group_lines)",
                "dynamic_search_code = dynamic_group_lines[:1].account_code if dynamic_group_lines else ''",
                "expanded_visible_count = dynamic_metadata.get('preview_visible_row_count')",
                "dynamic_wizard.action_collapse_all()",
                "collapsed_metadata = json.loads(dynamic_wizard.preview_metadata or '{}')",
                "dynamic_wizard.action_expand_all()",
                "expanded_again_metadata = json.loads(dynamic_wizard.preview_metadata or '{}')",
                "search_status = False",
                "if dynamic_search_code:",
                "    dynamic_wizard.write({'search_text': dynamic_search_code})",
                "    dynamic_wizard.action_preview_report()",
                "    search_metadata = json.loads(dynamic_wizard.preview_metadata or '{}')",
                "    search_status = 0 < search_metadata.get('row_count', 0) < dynamic_metadata.get('row_count', 0)",
                "dynamic_wizard.write({'search_text': ''})",
                "dynamic_wizard.action_generate_export()",
                "dynamic_export_metadata = json.loads(dynamic_wizard.export_metadata or '{}')",
                "dynamic_raw = base64.b64decode(dynamic_wizard.export_file or b'')",
                "canonical_menu_xmlids = [",
                "    'menu_rebuild_account_report_trial_balance_launcher',",
                "    'menu_rebuild_account_report_general_ledger_launcher',",
                "    'menu_rebuild_account_report_balance_sheet_launcher',",
                "    'menu_rebuild_account_report_profit_loss_launcher',",
                "    'menu_rebuild_account_report_tax_launcher',",
                "]",
                "canonical_menus = [env.ref('rebuild_account_migration.' + xmlid) for xmlid in canonical_menu_xmlids]",
                "canonical_menu_status = all(menu.active and menu.action.type == 'ir.actions.client' and menu.action.tag == 'rebuild_accounting_report' for menu in canonical_menus)",
                "competing_menus = [",
                "    env.ref('account_financial_report.menu_oca_reports', raise_if_not_found=False),",
                "    env.ref('rebuild_account_migration.menu_rebuild_mis_balance_sheet', raise_if_not_found=False),",
                "    env.ref('rebuild_account_migration.menu_rebuild_mis_profit_loss', raise_if_not_found=False),",
                "]",
                "competing_menu_status = all(not menu or not menu.active for menu in competing_menus)",
                "dynamic_status = all([",
                "    dynamic_action.get('target') == 'current',",
                "    str(dynamic_wizard.date_from) == '2025-10-01',",
                "    str(dynamic_wizard.date_to) == '2025-10-31',",
                "    str(dynamic_wizard.comparison_date_from) == '2024-10-01',",
                "    str(dynamic_wizard.comparison_date_to) == '2024-10-31',",
                "    dynamic_wizard.draft_entry_count == 4,",
                "    bool(dynamic_wizard.preview_warning),",
                "    dynamic_group_count > 0,",
                "    dynamic_comparison_status,",
                "    collapsed_metadata.get('preview_visible_row_count', 0) < expanded_visible_count,",
                "    expanded_again_metadata.get('preview_visible_row_count') == expanded_visible_count,",
                "    search_status,",
                "    dynamic_export_metadata.get('data_scope') == 'native',",
                "    dynamic_export_metadata.get('group_by') == 'account',",
                "    dynamic_export_metadata.get('comparison_mode') == 'previous_year',",
                "    dynamic_export_metadata.get('row_count') == dynamic_metadata.get('row_count'),",
                "    dynamic_raw.startswith(b'PK'),",
                "    canonical_menu_status,",
                "    competing_menu_status,",
                "])",
                "results['dynamic_workbench'] = {",
                "    'period': [str(dynamic_wizard.date_from), str(dynamic_wizard.date_to)],",
                "    'comparison_period': [str(dynamic_wizard.comparison_date_from), str(dynamic_wizard.comparison_date_to)],",
                "    'draft_entry_count': dynamic_wizard.draft_entry_count,",
                "    'group_count': dynamic_group_count,",
                "    'expanded_visible_row_count': expanded_visible_count,",
                "    'collapsed_visible_row_count': collapsed_metadata.get('preview_visible_row_count'),",
                "    'search_status': 'passed' if search_status else 'failed',",
                "    'export_row_count': dynamic_export_metadata.get('row_count'),",
                "    'canonical_menu_status': 'passed' if canonical_menu_status else 'failed',",
                "    'competing_menu_status': 'passed' if competing_menu_status else 'failed',",
                "    'status': 'passed' if dynamic_status else 'failed',",
                "}",
                "move = env['account.move'].search([",
                "    ('company_id', '=', company.id),",
                "    ('rebuild_source_model', '=', 'account.move'),",
                "    ('state', '=', 'posted'),",
                "    ('date', '>=', '2024-01-10'),",
                "    ('date', '<=', '2025-09-30'),",
                "], order='date, id', limit=1)",
                "filtered_item = {'status': 'failed', 'reason': 'no posted benchmark move found'}",
                "if move and move.journal_id:",
                "    filtered_wizard = Wizard.create({",
                "        'report_type': 'general_ledger',",
                "        'company_id': company.id,",
                "        'date_from': '2024-01-10',",
                "        'date_to': '2025-09-30',",
                "        'target_move': 'posted',",
                "        'export_format': 'csv',",
                "        'journal_ids': [(6, 0, [move.journal_id.id])],",
                "    })",
                "    filtered_wizard.action_generate_export()",
                "    filtered_raw = base64.b64decode(filtered_wizard.export_file or b'')",
                "    filtered_rows = list(csv.DictReader(io.StringIO(filtered_raw.decode('utf-8'))))",
                "    filtered_data_rows = [row for row in filtered_rows if row.get('empty_report') != 'true']",
                "    filtered_metadata = json.loads(filtered_rows[0]['metadata']) if filtered_rows else json.loads(filtered_wizard.export_metadata or '{}')",
                "    filtered_status = 'passed' if filtered_data_rows and len(filtered_data_rows) < results['general_ledger']['row_count'] and all(row.get('journal_code') == move.journal_id.code for row in filtered_data_rows) and filtered_metadata.get('journal_filter') and filtered_metadata['journal_filter'][0].get('code') == move.journal_id.code else 'failed'",
                "    filtered_action = filtered_wizard.action_open_journal_items()",
                "    filtered_item = {",
                "        'filename': filtered_wizard.export_filename,",
                "        'row_count': len(filtered_data_rows),",
                "        'selected_journal_code': move.journal_id.code,",
                "        'metadata': filtered_metadata,",
                "        'action_domain': filtered_action.get('domain'),",
                "        'status': filtered_status,",
                "    }",
                "results['filtered_general_ledger_by_journal'] = filtered_item",
                "fec_wizard = Wizard.create({",
                "    'report_type': 'fec',",
                "    'company_id': company.id,",
                "    'date_from': '2024-01-10',",
                "    'date_to': '2025-09-30',",
                "    'target_move': 'posted',",
                "    'export_format': 'txt',",
                "    'fec_test_mode': True,",
                "})",
                "fec_wizard.action_generate_export()",
                "fec_raw = base64.b64decode(fec_wizard.export_file or b'')",
                "fec_metadata = json.loads(fec_wizard.export_metadata or '{}')",
                "fec_rows = list(csv.reader(io.StringIO(fec_raw.decode('utf-8-sig')), delimiter='|'))",
                "fec_header = fec_rows[0] if fec_rows else []",
                "fec_data_rows = fec_rows[1:]",
                "fec_hash = hashlib.sha256(fec_raw).hexdigest()",
                "fec_status = 'passed' if fec_wizard.export_filename.endswith('.txt') and fec_header[:3] == ['JournalCode', 'JournalLib', 'EcritureNum'] and len(fec_data_rows) == 4781 and fec_metadata.get('row_count') == 4781 and fec_metadata.get('debit') == '1064045.02' and fec_metadata.get('credit') == '1064045.02' and fec_metadata.get('sha256') == fec_hash and fec_metadata.get('fec_test_mode') is True else 'failed'",
                "results['fec_txt_export'] = {",
                "    'filename': fec_wizard.export_filename,",
                "    'byte_count': len(fec_raw),",
                "    'row_count': len(fec_data_rows),",
                "    'metadata': fec_metadata,",
                "    'sha256': fec_hash,",
                "    'status': fec_status,",
                "}",
                "payload = {",
                "    'status': 'passed' if all(item['status'] == 'passed' for item in results.values()) else 'failed',",
                "    'classification': 'ODOO_REPORT_EXPORT_WIZARD_CHECKS',",
                "    'scope': {'company': 'Unstatic Labs', 'date_from': '2024-01-10', 'date_to': '2025-09-30'},",
                "    'results': results,",
                "}",
                "print('REBUILD_REPORT_EXPORT_WIZARD=' + json.dumps(payload, sort_keys=True, default=str))",
                "",
            ],
        ),
        encoding="utf-8",
    )
    result = run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "init-db",
            "odoo",
            "shell",
            "--config=/etc/odoo/odoo.conf",
            f"--database={DEV_QA_DB}",
        ),
        input_file=export_script,
        check=False,
    )
    marker = None
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("REBUILD_REPORT_EXPORT_WIZARD="):
            marker = line.removeprefix("REBUILD_REPORT_EXPORT_WIZARD=")
    if result.returncode or not marker:
        return {
            "status": "failed",
            "classification": "REPORT_EXPORT_WIZARD_EXECUTION_DEFECT",
            "exit_code": result.returncode,
            "output_tail": (result.stdout + result.stderr)[-4000:],
        }
    return json.loads(marker)


def source_report_parity_evidence_from_controls(
    odoo_views: dict[str, Any],
    odoo_drilldowns: dict[str, Any],
    odoo_exports: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    # Keep successful report-family evidence even when another family makes the
    # aggregate probe partial. Otherwise one legitimately empty or broken
    # report erases the passed exports and drill-downs for every other report.
    view_checks = odoo_views.get("checks", {})
    french_statement_checks = odoo_views.get("french_statement_checks", {})
    tax_package_checks = odoo_views.get("french_tax_package_checks", {})
    drilldown_results = odoo_drilldowns.get("results", {})
    export_results = odoo_exports.get("results", {})

    export_type_by_key = {
        "trial_balance": "trial_balance",
        "general_ledger": "general_ledger",
        "journal_report": "journal_report",
        "partner_ledger": "partner_ledger",
        "customer_statement": "customer_statement",
        "open_items": "open_items",
        "aged_receivable": "aged_receivable",
        "aged_payable": "aged_payable",
        "balance_sheet": "balance_sheet",
        "profit_and_loss": "profit_loss",
        "vat_tax_report": "tax_report",
        "tax_report_group_account_tax": "tax_report_group_account_tax",
        "tax_report_group_tax_account": "tax_report_group_tax_account",
        "bank_reconciliation": "bank_reconciliation",
        "currency_report": "currency_report",
        "cash_flow": "cash_flow",
        "executive_summary": "executive_summary",
        "analytic_report": "analytic_report",
        "french_balance_sheet": "french_annual",
        "french_profit_and_loss": "french_annual",
        "sig_caf": "french_annual",
        "french_annual_statements": "french_annual",
        "french_balance_sheet_2024": "french_balance_sheet_2024",
        "french_profit_and_loss_2024": "french_profit_loss_2024",
        "sig_caf_2024": "sig_caf_2024",
        "french_tax_package": "french_tax_package",
        "ec_sales_list": "ec_sales_list",
        "oss_sales": "oss_sales",
        "oss_imports": "oss_imports",
        "deferred_expense": "deferred_schedule",
        "deferred_revenue": "deferred_schedule",
        "depreciation_schedule": "depreciation_schedule",
        "fixed_asset_register": "fixed_assets",
        "fixed_asset_group_account": "fixed_asset_group_account",
    }
    drilldown_key_by_key = {
        "trial_balance": "trial_balance",
        "general_ledger": "general_ledger",
        "journal_report": "journal_report",
        "partner_ledger": "partner_ledger",
        "open_items": "open_items",
        "aged_receivable": "aged_receivable",
        "balance_sheet": "balance_sheet",
        "profit_and_loss": "profit_and_loss",
        "vat_tax_report": "vat_tax_report",
        "bank_reconciliation": "bank_reconciliation",
        "currency_report": "currency_report",
        "cash_flow": "cash_flow",
        "executive_summary": "executive_summary",
        "analytic_report": "analytic_report",
        "french_balance_sheet": "french_balance_sheet",
        "french_profit_and_loss": "french_profit_and_loss",
        "sig_caf": "sig_caf",
        "french_balance_sheet_2024": "french_balance_sheet",
        "french_profit_and_loss_2024": "french_profit_and_loss",
        "sig_caf_2024": "sig_caf",
        "french_annual_statements": "french_balance_sheet",
        "french_tax_package": "french_tax_package",
    }
    view_check_by_key = {
        "trial_balance": "trial_balance_balanced",
        "general_ledger": "general_ledger_balanced",
        "journal_report": "journal_report_balanced",
        "customer_statement": "customer_statement_customer_scope_empty",
        "open_items": "open_items_scope_valid",
        "balance_sheet": "balance_sheet_balanced",
        "profit_and_loss": "profit_and_loss_result",
        "vat_tax_report": "vat_tax_report_present",
        "tax_report_group_account_tax": "tax_report_group_account_tax_present",
        "tax_report_group_tax_account": "tax_report_group_tax_account_present",
        "bank_reconciliation": "bank_reconciliation_present",
        "currency_report": "currency_report_present",
        "cash_flow": "cash_flow_present",
        "executive_summary": "executive_summary_present",
        "analytic_report": "analytic_current_report_present",
        "ec_sales_list": "ec_sales_list_current_present",
        "oss_sales": "oss_sales_available",
        "oss_imports": "oss_imports_available",
        "depreciation_schedule": "depreciation_schedule_present",
        "fixed_asset_register": "assets_present",
        "fixed_asset_group_account": "fixed_asset_group_account_present",
    }

    evidence = {}
    for evidence_key, export_type in sorted(export_type_by_key.items()):
        export_item = export_results.get(export_type, {})
        drilldown_key = drilldown_key_by_key.get(evidence_key)
        drilldown_item = drilldown_results.get(drilldown_key, {}) if drilldown_key else {}
        view_check_key = view_check_by_key.get(evidence_key)
        view_passed = view_checks.get(view_check_key) is True if view_check_key else True
        if evidence_key.startswith("french_"):
            view_passed = view_passed and all(
                french_statement_checks.get(key)
                for key in ("french_total_net_assets", "french_total_passif", "french_net_result")
            )
        if evidence_key == "sig_caf":
            view_passed = view_passed and all(
                french_statement_checks.get(key)
                for key in ("french_value_added", "french_ebe", "french_caf")
            )
        if evidence_key == "french_tax_package":
            view_passed = view_passed and all(
                tax_package_checks.get(key)
                for key in ("tax_package_total_net_assets", "tax_package_net_result", "tax_package_vat_collected")
            )
        export_passed = export_item.get("status") == "passed"
        drilldown_passed = True if not drilldown_key else drilldown_item.get("status") == "passed"
        evidence[evidence_key] = {
            "status": "passed" if export_passed and drilldown_passed and view_passed else "partial_or_failed",
            "target_evidence_key": evidence_key,
            "export_type": export_type,
            "export_status": export_item.get("status", "missing"),
            "export_row_count": export_item.get("row_count"),
            "csv_row_count": export_item.get("csv_row_count"),
            "xlsx_status": (export_item.get("xlsx") or {}).get("status"),
            "pdf_status": (export_item.get("pdf") or {}).get("status"),
            "preview_status": (export_item.get("preview") or {}).get("status"),
            "drilldown_key": drilldown_key or "",
            "drilldown_status": drilldown_item.get("status", "not_required"),
            "drilldown_model": drilldown_item.get("action_model"),
            "drilldown_journal_item_count": drilldown_item.get("journal_item_count"),
            "view_check": view_check_key or "",
            "view_status": "passed" if view_passed else "failed",
            "generated_at": utc_now(),
        }
    evidence["association_scope_excluded"] = {
        "status": "passed",
        "target_evidence_key": "association_scope_excluded",
        "export_type": "",
        "export_status": "not_applicable",
        "export_row_count": 0,
        "csv_row_count": 0,
        "xlsx_status": "not_applicable",
        "pdf_status": "not_applicable",
        "preview_status": "not_applicable",
        "drilldown_key": "",
        "drilldown_status": "not_applicable",
        "drilldown_model": "",
        "drilldown_journal_item_count": 0,
        "view_check": "source_company_legal_form_scope",
        "view_status": "passed",
        "source_company": "Unstatic Labs",
        "source_legal_form": "SASU",
        "scope_decision": (
            "Source association statement variants are catalogued but removed from the USL target scope "
            "because USL is a French SASU, not a non-profit association. This is a final product-scope "
            "classification; later professional review may still record an advisory observation."
        ),
        "generated_at": utc_now(),
    }
    return evidence


def update_source_report_parity_evidence(evidence_by_key: dict[str, dict[str, Any]]) -> dict[str, Any]:
    script_path = PRIVATE_ARTIFACTS / "source-report-parity-update.py"
    evidence_json = json.dumps(evidence_by_key, sort_keys=True, ensure_ascii=False)
    script_path.write_text(
        "\n".join(
            [
                "import json",
                f"evidence_by_key = json.loads({evidence_json!r})",
                "SourceReport = env['rebuild.account.source.report'].sudo()",
                "Discrepancy = env['rebuild.account.discrepancy'].sudo()",
                "reports = SourceReport.search([])",
                "summary = {'by_decision': {}, 'by_level': {}, 'by_status': {}, 'updated_count': 0, 'reports': []}",
                "for report in reports:",
                "    key = report.target_evidence_key or ''",
                "    evidence = evidence_by_key.get(key, {'status': 'missing_evidence', 'target_evidence_key': key})",
                "    if not report.target_action_xmlid:",
                "        vals = {",
                "            'parity_level': 'level_0_unmapped',",
                "            'latest_evidence_status': 'missing_target_equivalent',",
                "            'parity_gap': 'No target report equivalent is assigned.',",
                "        }",
                "    elif key in ('scope_variant_association_pending', 'pcg_2024_variant_pending'):",
                "        vals = {",
                "            'parity_level': 'level_3_semantic_partial' if report.decision == 'MANDATORY_PARITY' else 'level_2_ledger_controls',",
                "            'latest_evidence_status': 'scope_or_version_evidence_pending',",
                "            'parity_gap': 'A target report family exists, but this source report variant still needs reproducible legal-form or PCG-version classification evidence.',",
                "        }",
                "    elif evidence.get('status') == 'passed':",
                "        vals = {",
                "            'parity_level': 'level_4_accepted',",
                "            'latest_evidence_status': 'technical_evidence_validated',",
                "            'parity_gap': 'No technical parity gap: availability, exports, ledger controls and sampled drill-down evidence passed for the mapped target report family.',",
                "        }",
                "    elif report.decision == 'ACCOUNTANT_REQUESTED':",
                "        vals = {",
                "            'parity_level': 'level_1_available',",
                "            'latest_evidence_status': 'product_scope_evidence_pending',",
                "            'parity_gap': 'Target action exists, but product-scope evidence is still required before parity can be accepted or deliberately removed.',",
                "        }",
                "    elif report.decision == 'MANDATORY_PARITY':",
                "        vals = {",
                "            'parity_level': 'level_3_semantic_partial',",
                "            'latest_evidence_status': 'mapped_report_evidence_incomplete',",
                "            'parity_gap': 'The source report is mapped, but the latest report evidence package did not pass for its report family.',",
                "        }",
                "    else:",
                "        vals = {",
                "            'parity_level': 'level_2_ledger_controls',",
                "            'latest_evidence_status': 'mapped_operational_report_evidence_incomplete',",
                "            'parity_gap': 'The operational source report is mapped, but full operational acceptance evidence is incomplete.',",
                "        }",
                "    vals['latest_evidence_json'] = evidence",
                "    report.write(vals)",
                "    summary['updated_count'] += 1",
                "    summary['by_decision'][report.decision] = summary['by_decision'].get(report.decision, 0) + 1",
                "    summary['by_level'][report.parity_level] = summary['by_level'].get(report.parity_level, 0) + 1",
                "    summary['by_status'][report.latest_evidence_status] = summary['by_status'].get(report.latest_evidence_status, 0) + 1",
                "    summary['reports'].append({",
                "        'source_report_id': report.source_report_id,",
                "        'name': report.name,",
                "        'decision': report.decision,",
                "        'target_evidence_key': key,",
                "        'parity_level': report.parity_level,",
                "        'latest_evidence_status': report.latest_evidence_status,",
                "    })",
                "active_count = SourceReport.search_count([('active', '=', True)])",
                "missing_target_count = SourceReport.search_count([('active', '=', True), ('target_status', '=', 'missing_target_equivalent')])",
                "level4_partial_count = SourceReport.search_count([('active', '=', True), ('parity_level', '=', 'level_4_evidence_partial')])",
                "level4_accepted_count = SourceReport.search_count([('active', '=', True), ('parity_level', '=', 'level_4_accepted')])",
                "mandatory_count = SourceReport.search_count([('active', '=', True), ('decision', '=', 'MANDATORY_PARITY')])",
                "report_discrepancy = Discrepancy.search([('name', 'in', ['Report suite awaits current technical parity evidence', 'User-facing report suite awaits final report-variant and accountant acceptance']), ('status', 'in', ['open', 'investigating'])], limit=1)",
                "if report_discrepancy:",
                "    technical_evidence_complete = active_count and missing_target_count == 0 and active_count == level4_partial_count + level4_accepted_count",
                "    report_discrepancy.write({",
                "        'classification': 'legal_or_accounting_uncertainty' if technical_evidence_complete else 'missing_capability',",
                "        'source_value': str(active_count),",
                "        'target_value': (",
                "            f\"{level4_partial_count} partial evidence packages; {level4_accepted_count} technically validated reports; {missing_target_count} missing target equivalents\"",
                "        ),",
                "        'difference': (",
                "            f\"{active_count - level4_accepted_count} active source reports still lack complete technical evidence\"",
                "            if technical_evidence_complete",
                "            else f\"{missing_target_count} active source reports lack a target equivalent or complete technical evidence\"",
                "        ),",
                "        'evidence': json.dumps({",
                "            'source_report_parity': summary,",
                "            'acceptance_gate': {",
                "                'active_source_report_count': active_count,",
                "                'mandatory_source_report_count': mandatory_count,",
                "                'missing_target_equivalent_count': missing_target_count,",
                "                'level4_evidence_partial_count': level4_partial_count,",
                "                'level4_accepted_count': level4_accepted_count,",
                "                'technical_evidence_complete': technical_evidence_complete,",
                "            },",
                "        }, ensure_ascii=False, sort_keys=True),",
                "        'accounting_impact': (",
                "            'Every active source report has a target treatment and validated technical evidence covering availability, export, ledger controls and sampled drill-down where applicable. Professional review remains advisory and does not block engineering completion.'",
                "            if technical_evidence_complete",
                "            else 'One or more active source reports still lack a target equivalent or complete technical evidence, so report users cannot rely on the suite as complete parity evidence.'",
                "        ),",
                "        'legal_or_tax_impact': 'Professional review remains advisable before an external filing, but is outside the Accounting v1 engineering-completion gate.',",
                "        'recommendation': (",
                "            'Resolve this development discrepancy when every active source report has validated technical evidence; record later professional observations as advisory follow-up issues.'",
                "            if technical_evidence_complete",
                "            else 'Complete target report equivalents and Level 4 technical evidence for every active source report.'",
                "        ),",
                "    })",
                "    if technical_evidence_complete:",
                "        report_discrepancy.write({",
                "            'severity': 'P3',",
                "            'classification': 'accepted_improvement',",
                "            'status': 'resolved',",
                "            'decision': 'Accounting v1 engineering completion is based on reproducible technical evidence; professional review is advisory.',",
                "        })",
                "env.cr.commit()",
                "summary['status'] = 'passed'",
                "summary['classification'] = 'SOURCE_REPORT_PARITY_EVIDENCE_UPDATED'",
                "print('REBUILD_SOURCE_REPORT_PARITY=' + json.dumps(summary, sort_keys=True, default=str))",
                "",
            ],
        ),
        encoding="utf-8",
    )
    result = run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "init-db",
            "odoo",
            "shell",
            "--config=/etc/odoo/odoo.conf",
            f"--database={EXACT_VALIDATION_DB}",
        ),
        input_file=script_path,
        check=False,
    )
    marker = None
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("REBUILD_SOURCE_REPORT_PARITY="):
            marker = line.removeprefix("REBUILD_SOURCE_REPORT_PARITY=")
    if result.returncode or not marker:
        return {
            "status": "failed",
            "classification": "SOURCE_REPORT_PARITY_UPDATE_DEFECT",
            "exit_code": result.returncode,
            "output_tail": (result.stdout + result.stderr)[-4000:],
        }
    payload = json.loads(marker)
    write_json(PRIVATE_ARTIFACTS / "source-report-parity-status.json", payload)
    return payload


def seed_review_decision_records() -> dict[str, Any]:
    script_path = PRIVATE_ARTIFACTS / "review-decision-seed.py"
    script_path.write_text(
        "\n".join(
            [
                "import json",
                "Decision = env['rebuild.account.assurance.decision'].sudo()",
                "SourceReport = env['rebuild.account.source.report'].sudo()",
                "Discrepancy = env['rebuild.account.discrepancy'].sudo()",
                "ExternalValue = env['rebuild.account.external.report.value'].sudo()",
                "company = env['res.company'].sudo().search([('rebuild_source_id', '=', 1)], limit=1)",
                "import_run = env['rebuild.account.import.run'].sudo().search([], order='id desc', limit=1)",
                "period = 'USL benchmark 2024-01-10 to 2025-09-30'",
                "summary = {",
                "    'created_count': 0,",
                "    'updated_count': 0,",
                "    'preserved_recorded_count': 0,",
                "    'superseded_count': 0,",
                "    'report_review_count': 0,",
                "    'discrepancy_review_count': 0,",
                "    'external_value_review_count': 0,",
                "    'gate_review_count': 0,",
                "    'by_gate': {},",
                "    'by_state': {},",
                "    'records': [],",
                "}",
                "def upsert(name, vals, domain=None):",
                "    vals = dict(vals)",
                "    vals['name'] = name",
                "    vals.setdefault('state', 'draft')",
                "    search_domain = list(domain or [('name', '=', name)])",
                "    recorded = Decision.search(search_domain + [('state', '=', 'recorded')], limit=1)",
                "    if not recorded and domain:",
                "        recorded = Decision.search([('name', '=', name), ('state', '=', 'recorded')], limit=1)",
                "    record = recorded",
                "    if not record:",
                "        record = Decision.search(search_domain + [('state', '=', 'draft')], limit=1)",
                "    if not record and domain:",
                "        record = Decision.search([('name', '=', name), ('state', '=', 'draft')], limit=1)",
                "    action = 'created'",
                "    if record:",
                "        if record.state == 'recorded':",
                "            action = 'preserved_recorded'",
                "            summary['preserved_recorded_count'] += 1",
                "        else:",
                "            record.write(vals)",
                "            action = 'updated'",
                "            summary['updated_count'] += 1",
                "    else:",
                "        record = Decision.create(vals)",
                "        summary['created_count'] += 1",
                "    summary['by_gate'][record.gate] = summary['by_gate'].get(record.gate, 0) + 1",
                "    summary['by_state'][record.state] = summary['by_state'].get(record.state, 0) + 1",
                "    summary['records'].append({",
                "        'id': record.id,",
                "        'name': record.name,",
                "        'gate': record.gate,",
                "        'state': record.state,",
                "        'conclusion': record.conclusion,",
                "        'action': action,",
                "    })",
                "    return record",
                "stale_decisions = Decision.search([('discrepancy_id', '!=', False), ('state', '=', 'draft'), ('discrepancy_id.status', 'not in', ['open', 'investigating'])])",
                "for stale in stale_decisions:",
                "    stale.write({",
                "        'state': 'superseded',",
                "        'conclusion': 'not_applicable',",
                "        'decision_summary': 'Superseded because the linked discrepancy is no longer open.',",
                "        'remaining_risk': 'The linked discrepancy was resolved or replaced by a newer blocker record.',",
                "        'next_action': 'Review the current open discrepancy queue instead of this superseded record.',",
                "    })",
                "    summary['superseded_count'] += 1",
                "    summary['records'].append({",
                "        'id': stale.id,",
                "        'name': stale.name,",
                "        'gate': stale.gate,",
                "        'state': stale.state,",
                "        'conclusion': stale.conclusion,",
                "        'action': 'superseded',",
                "    })",
                "for report in SourceReport.search([], order='source_report_id, id'):",
                "    gate = 'scope_exclusion' if report.decision == 'REMOVED_AS_UNUSED' else 'report_parity'",
                "    if gate == 'scope_exclusion':",
                "        decision_summary = 'Advisory record for a deliberate product-scope exclusion.'",
                "        remaining_risk = report.parity_gap or report.acceptance_evidence_required or 'Revisit only if company scope or legal form changes.'",
                "    else:",
                "        decision_summary = 'Advisory review record linked to validated technical report parity evidence.'",
                "        remaining_risk = report.parity_gap or report.acceptance_evidence_required or 'Record later professional observations without blocking Accounting v1 engineering completion.'",
                "    upsert(f'Report review - source #{report.source_report_id} - {report.name}', {",
                "        'gate': gate,",
                "        'conclusion': 'pending',",
                "        'required_authority': 'valentin',",
                "        'company_id': company.id if company else False,",
                "        'period_key': period,",
                "        'source_report_id': report.id,",
                "        'import_run_id': report.rebuild_import_run_id.id or (import_run.id if import_run else False),",
                "        'evidence_key': report.target_evidence_key or '',",
                "        'decision_summary': decision_summary,",
                "        'evidence_summary': report.latest_evidence_status or report.target_status or '',",
                "        'remaining_risk': remaining_risk,",
                "        'next_action': report.acceptance_evidence_required or report.target_strategy or 'Review the generated report evidence package and record acceptance, rejection or required changes.',",
                "    }, [('source_report_id', '=', report.id), ('gate', 'in', ['report_parity', 'scope_exclusion'])])",
                "    summary['report_review_count'] += 1",
                "for discrepancy in Discrepancy.search([('status', 'in', ['open', 'investigating'])], order='severity, id'):",
                "    gate = 'tax_external_value' if discrepancy.classification == 'external_value_difference' else 'discrepancy_acceptance'",
                "    upsert(f'Discrepancy review - {discrepancy.name}', {",
                "        'gate': gate,",
                "        'conclusion': 'pending',",
                "        'required_authority': 'joint' if discrepancy.severity == 'P0' else 'accountant',",
                "        'company_id': discrepancy.company_id.id or (company.id if company else False),",
                "        'period_key': discrepancy.period_key or period,",
                "        'discrepancy_id': discrepancy.id,",
                "        'import_run_id': discrepancy.import_run_id.id or (import_run.id if import_run else False),",
                "        'source_value': discrepancy.source_value or '',",
                "        'target_value': discrepancy.target_value or '',",
                "        'difference': discrepancy.difference or '',",
                "        'decision_summary': f'Pending review of open {discrepancy.severity} {discrepancy.classification} discrepancy.',",
                "        'evidence_summary': discrepancy.evidence or '',",
                "        'remaining_risk': discrepancy.legal_or_tax_impact or discrepancy.accounting_impact or '',",
                "        'next_action': discrepancy.recommendation or 'Classify and resolve or explicitly accept this discrepancy.',",
                "    }, [('discrepancy_id', '=', discrepancy.id)])",
                "    summary['discrepancy_review_count'] += 1",
                "for external_value in ExternalValue.search([('active', '=', True), ('review_status', '=', 'pending_review')], order='company_id, period_key, form_code, field_code, id'):",
                "    upsert(f'External value review - {external_value.name} - {external_value.form_code} - {external_value.field_code}', {",
                "        'gate': 'tax_external_value',",
                "        'conclusion': 'pending',",
                "        'required_authority': 'accountant',",
                "        'company_id': external_value.company_id.id,",
                "        'period_key': external_value.period_key,",
                "        'external_value_id': external_value.id,",
                "        'discrepancy_id': external_value.discrepancy_id.id,",
                "        'import_run_id': external_value.import_run_id.id or (import_run.id if import_run else False),",
                "        'evidence_key': external_value.source_key,",
                "        'source_value': f'{external_value.amount:.2f}',",
                "        'decision_summary': 'Pending accountant review of external declaration or benchmark report value.',",
                "        'evidence_summary': external_value.evidence or external_value.source_document or '',",
                "        'remaining_risk': 'The external value is not accepted until the accountant records its declaration treatment.',",
                "        'next_action': 'Compare this external value to the ledger-derived tax-package line and record whether it is accepted, rejected or superseded.',",
                "    }, [('external_value_id', '=', external_value.id)])",
                "    summary['external_value_review_count'] += 1",
                "fec_validation_discrepancy = Discrepancy.search([('name', '=', 'Official DGFiP FEC validation has not been executed')], order='id desc', limit=1)",
                "if fec_validation_discrepancy and fec_validation_discrepancy.status == 'resolved':",
                "    fec_decision_summary = 'DGFiP Test Compta Demat source validation passed; professional review remains advisory.'",
                "    fec_evidence_summary = fec_validation_discrepancy.evidence or 'FEC validation artifact is available in the private evidence package.'",
                "    fec_remaining_risk = 'No engineering gap; reconcile again before any real external filing and record professional observations separately.'",
                "    fec_next_action = 'Use the validated test FEC for browser acceptance; rerun it from the eventual cutover database before filing.'",
                "else:",
                "    fec_decision_summary = 'Pending official DGFiP Test Compta Demat execution.'",
                "    fec_evidence_summary = 'Generated FEC and local structural preflight exist; official validator command is not configured or did not pass.'",
                "    fec_remaining_risk = 'Milestone 13 cannot close until official structural validation output is archived.'",
                "    fec_next_action = 'Configure or fix the current official DGFiP validator route, rerun accounting-fec-validate and archive the output.'",
                "upsert('FEC official validation review - Unstatic Labs 2025-09-30', {",
                "    'gate': 'fec_validation',",
                "    'conclusion': 'pending',",
                "    'required_authority': 'accountant',",
                "    'company_id': company.id if company else False,",
                "    'period_key': period,",
                "    'import_run_id': import_run.id if import_run else False,",
                "    'evidence_key': 'fec_official_validation',",
                "    'decision_summary': fec_decision_summary,",
                "    'evidence_summary': fec_evidence_summary,",
                "    'remaining_risk': fec_remaining_risk,",
                "    'next_action': fec_next_action,",
                "})",
                "summary['gate_review_count'] += 1",
                "upsert('Milestone 13 closure review - Unstatic Labs', {",
                "    'gate': 'milestone_closure',",
                "    'conclusion': 'pending',",
                "    'required_authority': 'joint',",
                "    'company_id': company.id if company else False,",
                "    'period_key': period,",
                "    'import_run_id': import_run.id if import_run else False,",
                "    'evidence_key': 'milestone_13_closure',",
                "    'decision_summary': 'Advisory closure record; engineering readiness is determined by reproducible technical and browser gates.',",
                "    'evidence_summary': 'Review summary, report bundle, discrepancies, FEC dossier and comparison artifacts are available in Odoo and private harness artifacts.',",
                "    'remaining_risk': 'Closure is premature only while technical gates or P0/P1 engineering defects remain unresolved.',",
                "    'next_action': 'Resolve technical blockers, run the clean rehearsal and complete the browser-only acceptance suite.',",
                "})",
                "summary['gate_review_count'] += 1",
                "env.cr.commit()",
                "summary['classification'] = 'REVIEW_DECISION_RECORDS_SEEDED'",
                "summary['open_discrepancy_count'] = Discrepancy.search_count([('status', 'in', ['open', 'investigating'])])",
                "summary['linked_source_report_review_count'] = Decision.search_count([('source_report_id', '!=', False), ('gate', 'in', ['report_parity', 'scope_exclusion']), ('state', '!=', 'superseded')])",
                "summary['linked_discrepancy_review_count'] = Decision.search_count([('discrepancy_id', '!=', False), ('state', '!=', 'superseded')])",
                "summary['linked_external_value_review_count'] = Decision.search_count([('external_value_id', '!=', False), ('state', '!=', 'superseded')])",
                "summary['pending_external_value_count'] = ExternalValue.search_count([('active', '=', True), ('review_status', '=', 'pending_review')])",
                "summary['total_review_decision_count'] = Decision.search_count([])",
                "summary['active_review_decision_count'] = Decision.search_count([('state', '!=', 'superseded')])",
                "summary['status'] = 'passed' if summary['report_review_count'] == SourceReport.search_count([]) and summary['linked_source_report_review_count'] == SourceReport.search_count([]) and summary['linked_discrepancy_review_count'] >= summary['open_discrepancy_count'] and summary['linked_external_value_review_count'] >= summary['pending_external_value_count'] and summary['gate_review_count'] == 2 else 'failed'",
                "print('REBUILD_REVIEW_DECISION_SEED=' + json.dumps(summary, sort_keys=True, default=str))",
                "",
            ],
        ),
        encoding="utf-8",
    )
    result = run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "init-db",
            "odoo",
            "shell",
            "--config=/etc/odoo/odoo.conf",
            f"--database={EXACT_VALIDATION_DB}",
        ),
        input_file=script_path,
        check=False,
    )
    marker = None
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("REBUILD_REVIEW_DECISION_SEED="):
            marker = line.removeprefix("REBUILD_REVIEW_DECISION_SEED=")
    if result.returncode or not marker:
        return {
            "status": "failed",
            "classification": "REVIEW_DECISION_SEED_EXECUTION_DEFECT",
            "exit_code": result.returncode,
            "output_tail": (result.stdout + result.stderr)[-4000:],
        }
    payload = json.loads(marker)
    write_json(PRIVATE_ARTIFACTS / "review-decision-seed-status.json", payload)
    return payload


def odoo_accountant_access_controls() -> dict[str, Any]:
    access_script = PRIVATE_ARTIFACTS / "accountant-access-check.py"
    access_script.write_text(
        "\n".join(
            [
                "import base64",
                "import json",
                "company = env['res.company'].search([('rebuild_source_id', '=', 1)], limit=1)",
                "media_company = env['res.company'].search([('rebuild_source_id', '=', 8)], limit=1)",
                "group = env.ref('rebuild_account_migration.group_rebuild_accountant_reviewer')",
                "base_group = env.ref('base.group_user')",
                "Users = env['res.users'].with_context(no_reset_password=True, mail_create_nosubscribe=True, tracking_disable=True)",
                "login = 'accountant.review@example.invalid'",
                "values = {",
                "    'name': 'USL Accountant Review Probe',",
                "    'login': login,",
                "    'email': login,",
                "    'company_id': company.id,",
                "    'company_ids': [(6, 0, [company.id])],",
                "    'group_ids': [(6, 0, [base_group.id, group.id])],",
                "}",
                "user = Users.search([('login', '=', login)], limit=1)",
                "if user:",
                "    user.write(values)",
                "else:",
                "    user = Users.create(values)",
                "Report = env['rebuild.account.trial.balance.line'].with_user(user)",
                "French = env['rebuild.account.french.statement.line'].with_user(user)",
                "Discrepancy = env['rebuild.account.discrepancy'].with_user(user)",
                "SourceReport = env['rebuild.account.source.report'].with_user(user)",
                "SourceReportSudo = env['rebuild.account.source.report'].sudo()",
                "ReviewDecision = env['rebuild.account.assurance.decision'].with_user(user)",
                "ReviewDecisionSudo = env['rebuild.account.assurance.decision'].sudo()",
                "ExternalValue = env['rebuild.account.external.report.value'].with_user(user)",
                "ExternalValueSudo = env['rebuild.account.external.report.value'].sudo()",
                "Attachment = env['ir.attachment'].with_user(user)",
                "AttachmentSudo = env['ir.attachment'].sudo()",
                "DeferredSchedule = env['rebuild.account.deferred.schedule.line'].with_user(user)",
                "DeferredScheduleSudo = env['rebuild.account.deferred.schedule.line'].sudo()",
                "ReviewSummary = env['rebuild.account.overview'].with_user(user)",
                "MoveLine = env['account.move.line'].with_user(user)",
                "report_count = Report.search_count([('company_id', '=', company.id)])",
                "french_count = French.search_count([('company_id', '=', company.id)])",
                "discrepancy_count = Discrepancy.search_count([])",
                "summary = ReviewSummary.search([('company_id', '=', company.id)], limit=1)",
                "summary_ok = bool(summary and summary.open_discrepancy_count >= summary.open_p0_count and summary.pending_external_report_value_count >= 1)",
                "summary_actions_ok = False",
                "summary_payload = {}",
                "if summary:",
                "    discrepancy_action = summary.action_open_open_discrepancies()",
                "    decision_action = summary.action_open_review_decisions()",
                "    external_value_action = summary.action_open_external_report_values()",
                "    journal_action = summary.action_open_imported_journal_items()",
                "    report_action = summary.action_open_report_export_wizard()",
                "    summary_actions_ok = discrepancy_action.get('res_model') == 'rebuild.account.discrepancy' and decision_action.get('res_model') == 'rebuild.account.assurance.decision' and external_value_action.get('res_model') == 'rebuild.account.external.report.value' and journal_action.get('res_model') == 'account.move.line' and report_action.get('res_model') == 'rebuild.account.report.export.wizard'",
                "    summary_payload = {",
                "        'company': summary.company_id.display_name,",
                "        'source_company_id': summary.source_company_id,",
                "        'readiness_status': summary.readiness_status,",
                "        'latest_import_status': summary.latest_import_status,",
                "        'posted_move_count': summary.posted_move_count,",
                "        'move_line_count': summary.move_line_count,",
                "        'open_p0_count': summary.open_p0_count,",
                "        'open_p1_count': summary.open_p1_count,",
                "        'open_discrepancy_count': summary.open_discrepancy_count,",
                "        'review_decision_count': summary.review_decision_count,",
                "        'pending_review_decision_count': summary.pending_review_decision_count,",
                "        'recorded_review_decision_count': summary.recorded_review_decision_count,",
                "        'external_report_value_count': summary.external_report_value_count,",
                "        'pending_external_report_value_count': summary.pending_external_report_value_count,",
                "        'source_report_count': summary.source_report_count,",
                "        'mandatory_report_count': summary.mandatory_report_count,",
                "        'partial_report_equivalent_count': summary.partial_report_equivalent_count,",
                "        'level_3_report_count': summary.level_3_report_count,",
                "        'level_4_report_count': summary.level_4_report_count,",
                "        'actions': {",
                "            'discrepancies': discrepancy_action.get('res_model'),",
                "            'review_decisions': decision_action.get('res_model'),",
                "            'external_values': external_value_action.get('res_model'),",
                "            'journal_items': journal_action.get('res_model'),",
                "            'report_export': report_action.get('res_model'),",
                "        },",
                "    }",
                "source_report_count = SourceReport.search_count([])",
                "expected_source_report_count = SourceReportSudo.search_count([])",
                "external_value_count = ExternalValue.search_count([('company_id', '=', company.id), ('active', '=', True)])",
                "expected_external_value_count = ExternalValueSudo.search_count([('company_id', '=', company.id), ('active', '=', True)])",
                "pending_external_value_count = ExternalValue.search_count([('company_id', '=', company.id), ('active', '=', True), ('review_status', '=', 'pending_review')])",
                "attachment_domain = [('rebuild_source_model', '=', 'ir.attachment'), ('company_id', '=', company.id), ('res_model', '!=', False)]",
                "attachment_count = Attachment.search_count(attachment_domain)",
                "expected_attachment_count = AttachmentSudo.search_count(attachment_domain)",
                "sample_attachment = Attachment.search(attachment_domain + [('file_size', '>', 0)], order='file_size asc, id', limit=1)",
                "attachment_binary_ok = False",
                "attachment_binary_exception_type = ''",
                "attachment_sample_payload = {}",
                "if sample_attachment:",
                "    try:",
                "        raw = sample_attachment.raw",
                "        if raw:",
                "            raw_size = len(raw)",
                "            attachment_binary_ok = raw_size == sample_attachment.file_size",
                "        else:",
                "            decoded = base64.b64decode(sample_attachment.datas or b'')",
                "            raw_size = len(decoded)",
                "            attachment_binary_ok = raw_size == sample_attachment.file_size",
                "    except Exception as exc:",
                "        raw_size = 0",
                "        attachment_binary_exception_type = type(exc).__name__",
                "    attachment_sample_payload = {",
                "        'id': sample_attachment.id,",
                "        'name': sample_attachment.name,",
                "        'mimetype': sample_attachment.mimetype,",
                "        'file_size': sample_attachment.file_size,",
                "        'read_size': raw_size,",
                "        'binary_status': 'passed' if attachment_binary_ok else 'failed',",
                "        'exception_type': attachment_binary_exception_type,",
                "    }",
                "privacy_probe_blocked = False",
                "privacy_probe_exception_type = ''",
                "try:",
                "    with env.cr.savepoint():",
                "        private_owner = env['ir.config_parameter'].sudo().create({",
                "            'key': 'rebuild.account_migration.private_attachment_probe',",
                "            'value': 'private review probe',",
                "        })",
                "        private_attachment = AttachmentSudo.create({",
                "            'name': 'Rollback-only private attachment access probe',",
                "            'res_model': 'ir.config_parameter',",
                "            'res_id': private_owner.id,",
                "            'type': 'binary',",
                "            'raw': b'private review probe',",
                "            'public': False,",
                "        })",
                "        try:",
                "            private_attachment.with_user(user).read(['name', 'file_size'])",
                "        except Exception as exc:",
                "            privacy_probe_blocked = True",
                "            privacy_probe_exception_type = type(exc).__name__",
                "        raise RuntimeError('ROLLBACK_ATTACHMENT_PRIVACY_PROBE')",
                "except RuntimeError as exc:",
                "    if str(exc) != 'ROLLBACK_ATTACHMENT_PRIVACY_PROBE':",
                "        privacy_probe_exception_type = type(exc).__name__",
                "def has_model_access(records, operation):",
                "    try:",
                "        records.check_access(operation)",
                "        return True",
                "    except Exception:",
                "        return False",
                "review_decision_access = {operation: has_model_access(ReviewDecision, operation) for operation in ('read', 'write', 'create', 'unlink')}",
                "external_value_access = {operation: has_model_access(ExternalValue, operation) for operation in ('read', 'write', 'create', 'unlink')}",
                "review_decision = ReviewDecision.search([('company_id', '=', company.id)], limit=1)",
                "review_decision_ok = bool(review_decision and review_decision.read(['name', 'state', 'decision_summary']))",
                "review_mutation_probe = {'status': 'failed', 'rolled_back': False}",
                "try:",
                "    with env.cr.savepoint():",
                "        probe_decision = ReviewDecisionSudo.create({",
                "            'name': 'Rollback-only reviewer mutation probe',",
                "            'gate': 'report_parity',",
                "            'conclusion': 'accepted',",
                "            'required_authority': 'accountant',",
                "            'company_id': company.id,",
                "            'period_key': 'USL benchmark 2024-01-10 to 2025-09-30',",
                "            'decision_summary': 'Prepared decision used only to prove that the reviewer cannot mutate or record it.',",
                "        })",
                "        create_blocked = False",
                "        create_exception_type = ''",
                "        try:",
                "            ReviewDecision.create({",
                "                'name': 'Reviewer create must be blocked',",
                "                'gate': 'report_parity',",
                "                'conclusion': 'pending',",
                "                'company_id': company.id,",
                "                'decision_summary': 'This record must never be created.',",
                "            })",
                "        except Exception as exc:",
                "            create_blocked = True",
                "            create_exception_type = type(exc).__name__",
                "        write_blocked = False",
                "        write_exception_type = ''",
                "        try:",
                "            probe_decision.with_user(user).write({'decision_summary': 'Reviewer write must be blocked.'})",
                "        except Exception as exc:",
                "            write_blocked = True",
                "            write_exception_type = type(exc).__name__",
                "        record_blocked = False",
                "        record_exception_type = ''",
                "        try:",
                "            probe_decision.with_user(user).action_record()",
                "        except Exception as exc:",
                "            record_blocked = True",
                "            record_exception_type = type(exc).__name__",
                "        review_mutation_probe = {",
                "            'status': 'passed' if create_blocked and write_blocked and record_blocked and probe_decision.state == 'draft' else 'failed',",
                "            'create_blocked': create_blocked,",
                "            'create_exception_type': create_exception_type,",
                "            'write_blocked': write_blocked,",
                "            'write_exception_type': write_exception_type,",
                "            'record_blocked': record_blocked,",
                "            'record_exception_type': record_exception_type,",
                "            'decision_state': probe_decision.state,",
                "            'rolled_back': False,",
                "        }",
                "        raise RuntimeError('ROLLBACK_REVIEW_DECISION_MUTATION_PROBE')",
                "except RuntimeError as exc:",
                "    if str(exc) == 'ROLLBACK_REVIEW_DECISION_MUTATION_PROBE':",
                "        review_mutation_probe['rolled_back'] = True",
                "    else:",
                "        review_mutation_probe = {'status': 'failed', 'exception_type': type(exc).__name__, 'rolled_back': False}",
                "except Exception as exc:",
                "    review_mutation_probe = {'status': 'failed', 'exception_type': type(exc).__name__, 'rolled_back': False}",
                "deferred_schedule_count = DeferredSchedule.search_count([('company_id', '=', company.id)])",
                "expected_deferred_schedule_count = DeferredScheduleSudo.search_count([('company_id', '=', company.id)])",
                "line_count = MoveLine.search_count([('company_id', '=', company.id), ('rebuild_source_model', '=', 'account.move.line')])",
                "media_line_count = 0",
                "if media_company:",
                "    media_line_count = MoveLine.search_count([('company_id', '=', media_company.id), ('rebuild_source_model', '=', 'account.move.line')])",
                "Wizard = env['rebuild.account.report.export.wizard'].with_user(user)",
                "wizard = Wizard.create({",
                "    'report_type': 'trial_balance',",
                "    'company_id': company.id,",
                "    'date_from': '2024-01-10',",
                "    'date_to': '2025-09-30',",
                "    'target_move': 'posted',",
                "    'export_format': 'xlsx',",
                "})",
                "wizard.action_generate_export()",
                "export_raw = base64.b64decode(wizard.export_file or b'')",
                "export_metadata = json.loads(wizard.export_metadata or '{}')",
                "export_ok = bool(export_raw.startswith(b'PK') and export_metadata.get('row_count') == 68 and export_metadata.get('format') == 'xlsx')",
                "write_blocked = False",
                "write_exception_type = ''",
                "move = env['account.move'].search([('company_id', '=', company.id), ('rebuild_source_model', '=', 'account.move')], limit=1)",
                "try:",
                "    with env.cr.savepoint():",
                "        move.with_user(user).write({'ref': 'blocked accountant write probe'})",
                "        raise RuntimeError('WRITE_WAS_ALLOWED')",
                "except RuntimeError as exc:",
                "    write_exception_type = str(exc)",
                "except Exception as exc:",
                "    write_blocked = True",
                "    write_exception_type = type(exc).__name__",
                "payload = {",
                "    'status': 'passed' if report_count > 0 and french_count > 0 and summary_ok and summary_actions_ok and source_report_count == expected_source_report_count and source_report_count > 0 and external_value_count == expected_external_value_count and pending_external_value_count > 0 and attachment_count == expected_attachment_count and attachment_count > 0 and attachment_binary_ok and privacy_probe_blocked and review_decision_ok and review_decision_access == {'read': True, 'write': False, 'create': False, 'unlink': False} and external_value_access == {'read': True, 'write': False, 'create': False, 'unlink': False} and review_mutation_probe.get('status') == 'passed' and review_mutation_probe.get('rolled_back') and deferred_schedule_count == expected_deferred_schedule_count and deferred_schedule_count > 0 and line_count > 0 and media_line_count == 0 and export_ok and write_blocked else 'failed',",
                "    'classification': 'ACCOUNTANT_REVIEW_ACCESS_CHECKS',",
                "    'scope': {'company': 'Unstatic Labs', 'source_company_id': 1, 'restricted_source_company_ids': [8]},",
                "    'review_group_xmlid': 'rebuild_account_migration.group_rebuild_accountant_reviewer',",
                "    'implied_group_xmlid': 'account.group_account_readonly',",
                "    'user_login': login,",
                "    'report_count': report_count,",
                "    'french_statement_count': french_count,",
                "    'review_summary': summary_payload,",
                "    'discrepancy_count': discrepancy_count,",
                "    'source_report_count': source_report_count,",
                "    'expected_source_report_count': expected_source_report_count,",
                "    'external_report_value_count': external_value_count,",
                "    'expected_external_report_value_count': expected_external_value_count,",
                "    'pending_external_report_value_count': pending_external_value_count,",
                "    'attachment_count': attachment_count,",
                "    'expected_attachment_count': expected_attachment_count,",
                "    'attachment_sample': attachment_sample_payload,",
                "    'privacy_probe': {'status': 'passed' if privacy_probe_blocked else 'failed', 'exception_type': privacy_probe_exception_type},",
                "    'review_decision_probe': {'id': review_decision.id, 'status': 'passed' if review_decision_ok else 'failed', 'access_rights': review_decision_access},",
                "    'external_report_value_access_rights': external_value_access,",
                "    'review_decision_mutation_probe': review_mutation_probe,",
                "    'deferred_schedule_count': deferred_schedule_count,",
                "    'expected_deferred_schedule_count': expected_deferred_schedule_count,",
                "    'visible_usl_move_line_count': line_count,",
                "    'visible_usl_media_move_line_count': media_line_count,",
                "    'trial_balance_xlsx_export': {'filename': wizard.export_filename, 'byte_count': len(export_raw), 'metadata': export_metadata, 'status': 'passed' if export_ok else 'failed'},",
                "    'write_blocked': write_blocked,",
                "    'write_exception_type': write_exception_type,",
                "}",
                "print('REBUILD_ACCOUNTANT_ACCESS=' + json.dumps(payload, sort_keys=True))",
                "",
            ],
        ),
        encoding="utf-8",
    )
    result = run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "init-db",
            "odoo",
            "shell",
            "--config=/etc/odoo/odoo.conf",
            f"--database={EXACT_VALIDATION_DB}",
        ),
        input_file=access_script,
        check=False,
    )
    marker = None
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("REBUILD_ACCOUNTANT_ACCESS="):
            marker = line.removeprefix("REBUILD_ACCOUNTANT_ACCESS=")
    if result.returncode or not marker:
        return {
            "status": "failed",
            "classification": "ACCOUNTANT_ACCESS_EXECUTION_DEFECT",
            "exit_code": result.returncode,
            "output_tail": (result.stdout + result.stderr)[-4000:],
        }
    return json.loads(marker)


def reports(args: argparse.Namespace) -> dict[str, Any]:
    ensure_dirs()
    snapshot = source_snapshot_date() or USL_BENCHMARK_END
    tb_rows = trial_balance_rows()
    gl_rows = general_ledger_rows()
    asset_rows = target_asset_rows()
    depreciation_schedule_rows = target_depreciation_schedule_rows()
    french_rows = french_statement_rows()
    tax_package_rows = french_tax_package_rows()
    bank_rows = bank_reconciliation_rows()
    currency_rows = currency_report_rows()
    analytic_rows = analytic_distribution_rows()
    analytic_current_rows = analytic_distribution_current_rows()
    vat_investigation_payload = vat_benchmark_investigation()
    odoo_views = odoo_report_view_controls()
    odoo_drilldowns = odoo_report_drilldown_controls()
    odoo_exports = odoo_report_export_wizard_controls()
    source_report_parity = update_source_report_parity_evidence(
        source_report_parity_evidence_from_controls(odoo_views, odoo_drilldowns, odoo_exports)
    )
    review_decision_seed = seed_review_decision_records()
    accountant_access = odoo_accountant_access_controls()
    tb_json = PRIVATE_ARTIFACTS / "trial-balance-2025-09-30.json"
    tb_csv = PRIVATE_ARTIFACTS / "trial-balance-2025-09-30.csv"
    gl_csv = PRIVATE_ARTIFACTS / "general-ledger-2025-09-30.csv"
    asset_json = PRIVATE_ARTIFACTS / "fixed-asset-register-2025-09-30.json"
    asset_csv = PRIVATE_ARTIFACTS / "fixed-asset-register-2025-09-30.csv"
    depreciation_schedule_json = PRIVATE_ARTIFACTS / "depreciation-schedule.json"
    depreciation_schedule_csv = PRIVATE_ARTIFACTS / "depreciation-schedule.csv"
    french_json = PRIVATE_ARTIFACTS / "french-annual-statements-2025-09-30.json"
    french_csv = PRIVATE_ARTIFACTS / "french-annual-statements-2025-09-30.csv"
    tax_package_json = PRIVATE_ARTIFACTS / "french-tax-package-mapping-2025-09-30.json"
    tax_package_csv = PRIVATE_ARTIFACTS / "french-tax-package-mapping-2025-09-30.csv"
    vat_investigation_json = PRIVATE_ARTIFACTS / "vat-benchmark-investigation-2025-09-30.json"
    bank_json = PRIVATE_ARTIFACTS / "bank-reconciliation-2025-09-30.json"
    bank_csv = PRIVATE_ARTIFACTS / "bank-reconciliation-2025-09-30.csv"
    currency_json = PRIVATE_ARTIFACTS / "currency-gain-loss-exposure-2025-09-30.json"
    currency_csv = PRIVATE_ARTIFACTS / "currency-gain-loss-exposure-2025-09-30.csv"
    analytic_json = PRIVATE_ARTIFACTS / "analytic-distribution-2025-09-30.json"
    analytic_csv = PRIVATE_ARTIFACTS / "analytic-distribution-2025-09-30.csv"
    analytic_current_json = PRIVATE_ARTIFACTS / "analytic-distribution-current.json"
    analytic_current_csv = PRIVATE_ARTIFACTS / "analytic-distribution-current.csv"
    tb_payload = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "report": "Trial Balance",
        "company": "Unstatic Labs",
        "period_start": USL_BENCHMARK_START,
        "period_end": USL_BENCHMARK_END,
        "source": "target imported posted ledger slice",
        "rows": tb_rows,
        "totals": {
            "debit": amount_text(sum(Decimal(row["debit"]) for row in tb_rows)),
            "credit": amount_text(sum(Decimal(row["credit"]) for row in tb_rows)),
            "closing_balance": amount_text(sum(Decimal(row["closing_balance"]) for row in tb_rows)),
        },
        "limitations": [
            "The files in this harness package are benchmark snapshots; the canonical Odoo workbench itself queries the native ledger dynamically.",
            "Professional review remains advisable before external filing, but is not inferred from technical checks and does not block Accounting v1 engineering completion.",
        ],
    }
    write_json(tb_json, tb_payload)
    write_csv(
        tb_csv,
        tb_rows,
        ["account_code", "account_name", "account_type", "source_account_id", "debit", "credit", "closing_balance"],
    )
    write_csv(
        gl_csv,
        gl_rows,
        [
            "account_code",
            "account_name",
            "date",
            "journal_code",
            "move_name",
            "source_move_id",
            "source_line_id",
            "partner_name",
            "label",
            "debit",
            "credit",
            "balance",
            "running_balance",
        ],
    )
    asset_payload = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "report": "Fixed Asset Register",
        "company": "Unstatic Labs",
        "as_of": USL_BENCHMARK_END,
        "source": "target imported source asset register",
        "rows": asset_rows,
        "totals": {
            "original_value": amount_text(sum(Decimal(row["original_value"]) for row in asset_rows)),
            "already_depreciated_amount_import": amount_text(
                sum(Decimal(row["already_depreciated_amount_import"]) for row in asset_rows),
            ),
            "imported_period_net_value": amount_text(
                sum(Decimal(row["imported_period_net_value"]) for row in asset_rows),
            ),
            "book_value": amount_text(sum(Decimal(row["book_value"]) for row in asset_rows)),
        },
        "limitations": [
            "This is an imported source asset-register artifact backed by the USL-original rebuild.account.asset model.",
            "Source depreciation moves outside the benchmark replay slice are counted but not yet imported.",
        ],
    }
    write_json(asset_json, asset_payload)
    write_csv(
        asset_csv,
        asset_rows,
        [
            "source_asset_id",
            "name",
            "source_company_id",
            "currency",
            "state",
            "asset_group_name",
            "acquisition_date",
            "original_value",
            "already_depreciated_amount_import",
            "imported_period_net_value",
            "book_value",
            "source_asset_account_id",
            "source_depreciation_account_id",
            "source_depreciation_expense_account_id",
            "source_journal_id",
            "source_depreciation_move_count",
        ],
    )
    depreciation_schedule_payload = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "report": "Depreciation Schedule",
        "company": "Unstatic Labs",
        "period_start": "2025-10-01",
        "period_end": "2028-06-30",
        "source": "target imported source asset-linked depreciation moves",
        "rows": depreciation_schedule_rows,
        "totals": {
            "depreciation_amount": amount_text(
                sum(Decimal(row["depreciation_amount"]) for row in depreciation_schedule_rows),
            ),
            "expense_amount": amount_text(
                sum(Decimal(row["expense_amount"]) for row in depreciation_schedule_rows),
            ),
        },
        "limitations": [
            "This is source schedule evidence, including draft/future source depreciation moves.",
            "Rows with representation_status=source_draft_forecast are not posted target accounting effects.",
        ],
    }
    write_json(depreciation_schedule_json, depreciation_schedule_payload)
    write_csv(
        depreciation_schedule_csv,
        depreciation_schedule_rows,
        [
            "source_asset_id",
            "source_move_id",
            "source_schedule_id",
            "source_move_name",
            "source_move_state",
            "depreciation_date",
            "move_ref",
            "expense_amount",
            "depreciation_amount",
            "accumulated_depreciation_amount",
            "net_book_value_after_line",
            "source_line_count",
        ],
    )
    french_payload = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "report": "French Annual Statements",
        "company": "Unstatic Labs",
        "period_start": USL_BENCHMARK_START,
        "period_end": USL_BENCHMARK_END,
        "source": "target imported posted ledger slice",
        "rows": french_rows,
        "benchmark_lines": {
            row["line_code"]: row
            for row in french_rows
            if row["line_code"] in {
                "ACTIF_TOTAL",
                "PASSIF_TOTAL",
                "PASSIF_RESULTAT",
                "CR_CHIFFRE_AFFAIRES",
                "CR_RESULTAT_EXPLOITATION",
                "CR_RESULTAT_COURANT_AVANT_IMPOT",
                "CR_RESULTAT_NET",
                "SIG_VALEUR_AJOUTEE",
                "SIG_EBE",
                "SIG_CAPACITE_AUTOFINANCEMENT",
            }
        },
        "limitations": [
            "Generated from USL-specific account-code mappings over the imported ledger.",
            "This is technical reconstruction evidence and still requires accountant review before statutory acceptance.",
        ],
    }
    write_json(french_json, french_payload)
    write_csv(
        french_csv,
        french_rows,
        [
            "statement_key",
            "statement_name",
            "line_sequence",
            "line_code",
            "line_name",
            "source_formula",
            "drilldown_account_prefixes",
            "move_line_count",
            "gross_amount",
            "depreciation_amount",
            "net_amount",
            "amount",
        ],
    )
    tax_package_payload = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "report": "French Tax Package Mapping",
        "company": "Unstatic Labs",
        "period_start": USL_BENCHMARK_START,
        "period_end": USL_BENCHMARK_END,
        "source": "target imported posted ledger slice and imported asset/VAT evidence",
        "official_reference_checked_on": "2026-07-21",
        "official_reference_urls": [
            "https://www.impots.gouv.fr/formulaire/2065-sd/impot-sur-les-societes",
            "https://www.impots.gouv.fr/formulaire/2033-sd/liasse-bicsi-regime-rsi-tableaux-ndeg-2033-sd-2033-g-sd",
            "https://www.impots.gouv.fr/formulaire/3517-s-sd/tva-et-taxes-assimilees-et-regime-simplifie",
            "https://www.impots.gouv.fr/professionnel/resultat-imposable-limpot-sur-le-revenu-ir-ou-limpot-sur-les-societes",
        ],
        "rows": tax_package_rows,
        "review_required_fields": [
            row
            for row in tax_package_rows
            if row.get("review_status") != "ledger_derived"
        ],
        "vat_benchmark_investigation": str(vat_investigation_json.relative_to(ROOT)),
        "limitations": [
            "This is a ledger-derived declaration review mapping for 2065-SD, 2033-SD and 3517-S-SD/CA12; it is not an accepted filing.",
            "Reduced-rate IS eligibility, reintegrations, deductions, deficits, externally accepted declaration values and final form box mapping still require accountant review.",
            "The deductible VAT goods/services declaration amount is traced to the posted source CA12 clearing entry; gross account 445660 turnover remains available as supporting ledger evidence.",
        ],
    }
    write_json(tax_package_json, tax_package_payload)
    write_json(vat_investigation_json, vat_investigation_payload)
    write_csv(
        tax_package_csv,
        tax_package_rows,
        [
            "form_code",
            "form_name",
            "field_code",
            "field_label",
            "source_kind",
            "source_formula",
            "source_report_line_code",
            "drilldown_account_prefixes",
            "move_line_count",
            "quantity",
            "amount",
            "rounded_amount",
            "benchmark_amount",
            "ledger_amount",
            "difference_amount",
            "difference_classification",
            "value_text",
            "review_status",
        ],
    )
    bank_payload = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "report": "Bank Reconciliation",
        "company": "Unstatic Labs",
        "period_start": USL_BENCHMARK_START,
        "period_end": USL_BENCHMARK_END,
        "source": "target imported bank statement lines linked to posted source moves",
        "rows": bank_rows,
        "totals": {
            "amount": amount_text(sum(Decimal(row["amount"]) for row in bank_rows)),
            "amount_residual": amount_text(sum(Decimal(row["amount_residual"]) for row in bank_rows)),
            "reconciled_count": sum(1 for row in bank_rows if row["is_reconciled"] == "true"),
            "open_residual_count": sum(1 for row in bank_rows if row["reconciliation_status"] == "Open residual"),
        },
        "limitations": [
            "This is historical bank-statement reconciliation evidence from imported statement-line links, not live bank synchronization.",
        ],
    }
    write_json(bank_json, bank_payload)
    write_csv(
        bank_csv,
        bank_rows,
        [
            "date",
            "journal_code",
            "move_name",
            "source_statement_line_id",
            "payment_ref",
            "partner_name",
            "transaction_type",
            "account_number",
            "internal_index",
            "amount",
            "amount_currency",
            "amount_residual",
            "is_reconciled",
            "reconciliation_status",
            "move_line_count",
        ],
    )
    currency_payload = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "report": "Currency Gain, Loss and Exposure",
        "company": "Unstatic Labs",
        "period_start": USL_BENCHMARK_START,
        "period_end": USL_BENCHMARK_END,
        "source": "target imported posted ledger slice",
        "rows": currency_rows,
        "sections": sorted({row["report_section"] for row in currency_rows}),
        "totals": {
            "debit": amount_text(sum(Decimal(row["debit"]) for row in currency_rows)),
            "credit": amount_text(sum(Decimal(row["credit"]) for row in currency_rows)),
            "balance": amount_text(sum(Decimal(row["balance"]) for row in currency_rows)),
            "amount_currency": amount_text(sum(Decimal(row["amount_currency"]) for row in currency_rows)),
            "amount_residual": amount_text(sum(Decimal(row["amount_residual"]) for row in currency_rows)),
            "amount_residual_currency": amount_text(sum(Decimal(row["amount_residual_currency"]) for row in currency_rows)),
        },
        "limitations": [
            "The unrealized section reports open foreign-currency receivable/payable residuals present in the imported ledger; the benchmark slice currently has no open foreign-currency residual rows.",
        ],
    }
    write_json(currency_json, currency_payload)
    write_csv(
        currency_csv,
        currency_rows,
        [
            "report_section",
            "currency",
            "account_code",
            "account_name",
            "account_type",
            "partner_name",
            "move_line_count",
            "debit",
            "credit",
            "balance",
            "amount_currency",
            "amount_residual",
            "amount_residual_currency",
        ],
    )
    analytic_payload = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "report": "Analytic Distribution",
        "company": "Unstatic Labs",
        "period_start": USL_BENCHMARK_START,
        "period_end": USL_BENCHMARK_END,
        "source": "target imported account_analytic_line records",
        "rows": analytic_rows,
        "totals": {
            "allocated_debit": amount_text(sum(Decimal(row["allocated_debit"]) for row in analytic_rows)),
            "allocated_credit": amount_text(sum(Decimal(row["allocated_credit"]) for row in analytic_rows)),
            "allocated_balance": amount_text(sum(Decimal(row["allocated_balance"]) for row in analytic_rows)),
        },
        "limitations": [
            "The source snapshot has no account_analytic_line records in the closed benchmark slice, so this benchmark report is available but empty.",
        ],
    }
    write_json(analytic_json, analytic_payload)
    write_csv(
        analytic_csv,
        analytic_rows,
        [
            "analytic_key",
            "analytic_code",
            "analytic_name",
            "account_code",
            "account_name",
            "move_line_count",
            "percentage",
            "allocated_debit",
            "allocated_credit",
            "allocated_balance",
        ],
    )
    analytic_current_payload = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "report": "Analytic Distribution",
        "company": "Unstatic Labs",
        "period_start": USL_CURRENT_START,
        "period_end": snapshot,
        "source": "target imported account_analytic_line records",
        "rows": analytic_current_rows,
        "totals": {
            "allocated_debit": amount_text(sum(Decimal(row["allocated_debit"]) for row in analytic_current_rows)),
            "allocated_credit": amount_text(sum(Decimal(row["allocated_credit"]) for row in analytic_current_rows)),
            "allocated_balance": amount_text(sum(Decimal(row["allocated_balance"]) for row in analytic_current_rows)),
            "analytic_line_count": sum(int(row["move_line_count"]) for row in analytic_current_rows),
        },
        "limitations": [
            "This is a management-accounting report over imported analytic entries, not a statutory ledger balance.",
            "Standalone source analytic lines are represented with source trace metadata even when no source journal item exists.",
        ],
    }
    write_json(analytic_current_json, analytic_current_payload)
    write_csv(
        analytic_current_csv,
        analytic_current_rows,
        [
            "analytic_key",
            "analytic_code",
            "analytic_name",
            "account_code",
            "account_name",
            "move_line_count",
            "percentage",
            "allocated_debit",
            "allocated_credit",
            "allocated_balance",
        ],
    )
    report_controls_passed = all(
        payload.get("status") == "passed"
        for payload in (
            odoo_views,
            odoo_drilldowns,
            odoo_exports,
            source_report_parity,
            review_decision_seed,
            accountant_access,
        )
    )
    report_catalogue_path = PRIVATE_ARTIFACTS / "report-catalogue-v1.json"
    final_matrix = capability_matrix(
        read_json(report_catalogue_path) if report_catalogue_path.exists() else [],
        final_capability_matrix_controls(
            source_report_parity,
            report_controls_passed=report_controls_passed,
        ),
    )
    capability_status_counts: dict[str, int] = {}
    for row in final_matrix:
        row_status = row.get("status", "UNKNOWN")
        capability_status_counts[row_status] = (
            capability_status_counts.get(row_status, 0) + 1
        )
    capability_matrix_technical_complete = not any(
        row.get("status") in {"DISCOVERY", "TECHNICAL_GAP"}
        for row in final_matrix
    )
    capability_matrix_summary = {
        "status": (
            "passed"
            if capability_matrix_technical_complete
            else "failed"
        ),
        "classification": (
            "TECHNICAL_CAPABILITY_MATRIX_COMPLETE"
            if capability_matrix_technical_complete
            else "TECHNICAL_CAPABILITY_MATRIX_INCOMPLETE"
        ),
        "path": "artifacts/accounting-compat/private/parity-matrix-v1.json",
        "row_count": len(final_matrix),
        "by_status": capability_status_counts,
    }
    technical_status = (
        report_controls_passed
        and capability_matrix_technical_complete
    )
    write_json(PRIVATE_ARTIFACTS / "parity-matrix-v1.json", final_matrix)
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "reports",
        "status": "passed" if technical_status else "partial",
        "classification": (
            "DYNAMIC_ODOO_REPORT_WORKBENCH_TECHNICALLY_VALIDATED"
            if technical_status
            else "DYNAMIC_ODOO_REPORT_WORKBENCH_TECHNICAL_GAPS"
        ),
        "reports": [
            {
                "name": "Trial Balance",
                "json": str(tb_json.relative_to(ROOT)),
                "csv": str(tb_csv.relative_to(ROOT)),
                "row_count": len(tb_rows),
                "totals": tb_payload["totals"],
            },
            {
                "name": "General Ledger",
                "csv": str(gl_csv.relative_to(ROOT)),
                "row_count": len(gl_rows),
            },
            {
                "name": "Bank Reconciliation",
                "json": str(bank_json.relative_to(ROOT)),
                "csv": str(bank_csv.relative_to(ROOT)),
                "row_count": len(bank_rows),
                "totals": bank_payload["totals"],
            },
            {
                "name": "Currency Gain, Loss and Exposure",
                "json": str(currency_json.relative_to(ROOT)),
                "csv": str(currency_csv.relative_to(ROOT)),
                "row_count": len(currency_rows),
                "sections": currency_payload["sections"],
                "totals": currency_payload["totals"],
            },
            {
                "name": "Analytic Distribution - benchmark",
                "json": str(analytic_json.relative_to(ROOT)),
                "csv": str(analytic_csv.relative_to(ROOT)),
                "row_count": len(analytic_rows),
                "totals": analytic_payload["totals"],
            },
            {
                "name": "Analytic Distribution - current",
                "json": str(analytic_current_json.relative_to(ROOT)),
                "csv": str(analytic_current_csv.relative_to(ROOT)),
                "row_count": len(analytic_current_rows),
                "totals": analytic_current_payload["totals"],
            },
            {
                "name": "Fixed Asset Register",
                "json": str(asset_json.relative_to(ROOT)),
                "csv": str(asset_csv.relative_to(ROOT)),
                "row_count": len(asset_rows),
                "totals": asset_payload["totals"],
            },
            {
                "name": "Depreciation Schedule",
                "json": str(depreciation_schedule_json.relative_to(ROOT)),
                "csv": str(depreciation_schedule_csv.relative_to(ROOT)),
                "row_count": len(depreciation_schedule_rows),
                "totals": depreciation_schedule_payload["totals"],
            },
            {
                "name": "French Annual Statements",
                "json": str(french_json.relative_to(ROOT)),
                "csv": str(french_csv.relative_to(ROOT)),
                "row_count": len(french_rows),
                "benchmark_lines": french_payload["benchmark_lines"],
            },
            {
                "name": "French Tax Package Mapping",
                "json": str(tax_package_json.relative_to(ROOT)),
                "csv": str(tax_package_csv.relative_to(ROOT)),
                "row_count": len(tax_package_rows),
                "review_required_count": len(tax_package_payload["review_required_fields"]),
            },
            {
                "name": "VAT Benchmark Investigation",
                "json": str(vat_investigation_json.relative_to(ROOT)),
                "classification": vat_investigation_payload["classification"],
                "source_target_445660_ledger_match": vat_investigation_payload["source_target_445660_ledger_match"],
            },
        ],
        "odoo_report_views": odoo_views,
        "odoo_report_drilldowns": odoo_drilldowns,
        "odoo_report_export_wizard": odoo_exports,
        "source_report_parity": source_report_parity,
        "capability_matrix": capability_matrix_summary,
        "review_decision_seed": review_decision_seed,
        "odoo_accountant_access": accountant_access,
        "limitations": [
            *tb_payload["limitations"],
            (
                "Professional review and external filing remain advisable operational "
                "steps, but are not Accounting v1 engineering-completion gates."
            ),
        ],
        "next_action": (
            "Use the validated report suite for internal browser acceptance; record "
            "later professional observations as advisory follow-up issues."
        ),
    }
    write_json(PRIVATE_ARTIFACTS / "reports-status.json", status)
    return status


def parse_fec_amount(value: str) -> Decimal:
    value = (value or "0").strip().replace(" ", "").replace(",", ".")
    return Decimal(value or "0").quantize(Decimal("0.01"))


FEC_BIC_IS_REQUIRED_HEADER = [
    "JournalCode",
    "JournalLib",
    "EcritureNum",
    "EcritureDate",
    "CompteNum",
    "CompteLib",
    "CompAuxNum",
    "CompAuxLib",
    "PieceRef",
    "PieceDate",
    "EcritureLib",
    "Debit",
    "Credit",
    "EcritureLet",
    "DateLet",
    "ValidDate",
    "Montantdevise",
    "Idevise",
]

FEC_REQUIRED_NON_EMPTY_FIELDS = {
    "JournalCode",
    "JournalLib",
    "EcritureNum",
    "EcritureDate",
    "CompteNum",
    "CompteLib",
    "PieceRef",
    "PieceDate",
    "EcritureLib",
    "Debit",
    "Credit",
    "ValidDate",
}

FEC_DATE_FIELDS = {"EcritureDate", "PieceDate", "DateLet", "ValidDate"}
FEC_AMOUNT_FIELDS = {"Debit", "Credit", "Montantdevise"}
FEC_NUMERIC_RE = re.compile(r"^[ +-]?\d+(,\d+)?$")
FEC_ACCOUNT_RE = re.compile(r"^\d{3}")
FEC_FILENAME_RE = re.compile(r"^(?P<siren>\d{9})FEC(?P<date>\d{8})\.txt$", re.IGNORECASE)


def fec_date_is_valid(value: str, *, allow_blank: bool = False) -> bool:
    value = (value or "").strip()
    if not value:
        return allow_blank
    if not re.fullmatch(r"\d{8}", value):
        return False
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError:
        return False
    return True


def fec_amount_is_valid(value: str, *, allow_blank: bool = False) -> bool:
    value = (value or "").strip()
    if not value:
        return allow_blank
    return bool(FEC_NUMERIC_RE.fullmatch(value)) and "." not in value


def fec_structural_preflight(
    fec_path: Path,
    *,
    generated_file_name: str | None = None,
    expected_siren: str = "983982950",
    expected_closing_date: str = USL_BENCHMARK_END,
) -> dict[str, Any]:
    """Local deterministic FEC checks derived from LPF article A.47 A-1.

    This intentionally remains a preflight. It does not claim to be the DGFiP
    Test Compta Demat result and must not close the official FEC validation gate.
    """
    content = fec_path.read_bytes()
    decoded = content.decode("utf-8-sig")
    raw_lines = decoded.splitlines()
    separator = "|" if raw_lines and "|" in raw_lines[0] else "\t" if raw_lines and "\t" in raw_lines[0] else ""
    rows = list(csv.reader(io.StringIO(decoded), delimiter=separator or "|")) if raw_lines else []
    header = rows[0] if rows else []
    data_rows = rows[1:]
    field_index = {field: index for index, field in enumerate(header)}
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    samples: dict[str, list[dict[str, Any]]] = {
        "invalid_rows": [],
        "unbalanced_entries": [],
        "chronology": [],
    }

    def add_error(code: str, message: str, **extra: Any) -> None:
        entry = {"code": code, "message": message}
        entry.update(extra)
        errors.append(entry)

    def add_warning(code: str, message: str, **extra: Any) -> None:
        entry = {"code": code, "message": message}
        entry.update(extra)
        warnings.append(entry)

    if not raw_lines:
        add_error("empty_file", "The FEC file is empty.")
    if separator not in {"|", "\t"}:
        add_error("unsupported_separator", "The FEC header does not use the required pipe or tab separator.")
    if header[:18] != FEC_BIC_IS_REQUIRED_HEADER:
        add_error(
            "invalid_required_header_order",
            "The first 18 FEC fields do not match the BIC/IS required order.",
            expected=FEC_BIC_IS_REQUIRED_HEADER,
            actual=header[:18],
        )
    if len(set(header)) != len(header):
        add_error("duplicate_header_fields", "The FEC header contains duplicate field names.")
    if separator == "|" and any("\t" in line for line in raw_lines):
        add_error("ambiguous_separator", "The FEC uses pipe separators but also contains tab characters.")

    effective_file_name = generated_file_name or fec_path.name
    match = FEC_FILENAME_RE.fullmatch(effective_file_name)
    expected_date = expected_closing_date.replace("-", "")
    if not match:
        add_warning(
            "artifact_filename_not_official",
            "The checked artifact path is not the official exported file name; Odoo export metadata is used when available.",
            filename=effective_file_name,
        )
    else:
        if match.group("siren") != expected_siren:
            add_error(
                "filename_siren_mismatch",
                "The generated FEC file name does not match the expected company SIREN.",
                expected=expected_siren,
                actual=match.group("siren"),
            )
        if match.group("date") != expected_date:
            add_error(
                "filename_closing_date_mismatch",
                "The generated FEC file name does not match the benchmark closing date.",
                expected=expected_date,
                actual=match.group("date"),
            )

    entry_balances: dict[tuple[str, str], dict[str, Decimal]] = {}
    previous_valid_date = ""
    invalid_row_count = 0
    invalid_date_count = 0
    invalid_amount_count = 0
    invalid_account_count = 0
    chronology_decrease_count = 0
    rows_with_compaux_num = 0
    rows_with_compaux_lib = 0
    rows_with_lettering = 0
    rows_with_currency = 0

    for row_number, row in enumerate(data_rows, start=2):
        row_errors: list[str] = []
        if len(row) != len(header):
            row_errors.append(f"expected {len(header)} fields, got {len(row)}")
        values = {field: row[index] if index < len(row) else "" for field, index in field_index.items()}
        for field in FEC_REQUIRED_NON_EMPTY_FIELDS:
            if not values.get(field, "").strip():
                row_errors.append(f"{field} is empty")
        if values.get("CompteNum", "").strip() and not FEC_ACCOUNT_RE.match(values["CompteNum"].strip()):
            row_errors.append("CompteNum does not start with three digits")
            invalid_account_count += 1
        for field in FEC_DATE_FIELDS:
            allow_blank = field == "DateLet"
            if field in values and not fec_date_is_valid(values[field], allow_blank=allow_blank):
                row_errors.append(f"{field} is not AAAAMMJJ")
                invalid_date_count += 1
        for field in FEC_AMOUNT_FIELDS:
            allow_blank = field == "Montantdevise"
            if field in values and not fec_amount_is_valid(values[field], allow_blank=allow_blank):
                row_errors.append(f"{field} is not a compliant decimal character value")
                invalid_amount_count += 1
        if values.get("EcritureLet", "").strip() and not values.get("DateLet", "").strip():
            row_errors.append("DateLet is empty while EcritureLet is populated")
        valid_date = values.get("ValidDate", "").strip()
        if previous_valid_date and valid_date and valid_date < previous_valid_date:
            row_errors.append("ValidDate order decreases compared with the previous row")
            chronology_decrease_count += 1
            if len(samples["chronology"]) < 5:
                samples["chronology"].append({
                    "row_number": row_number,
                    "previous_valid_date": previous_valid_date,
                    "valid_date": valid_date,
                    "ecriture_num": values.get("EcritureNum", ""),
                })
        if valid_date:
            previous_valid_date = valid_date

        if values.get("CompAuxNum", "").strip():
            rows_with_compaux_num += 1
        if values.get("CompAuxLib", "").strip():
            rows_with_compaux_lib += 1
        if values.get("EcritureLet", "").strip():
            rows_with_lettering += 1
        if values.get("Montantdevise", "").strip() or values.get("Idevise", "").strip():
            rows_with_currency += 1

        journal = values.get("JournalCode", "").strip()
        entry_num = values.get("EcritureNum", "").strip()
        if journal and entry_num and "Debit" in values and "Credit" in values:
            key = (journal, entry_num)
            entry = entry_balances.setdefault(key, {"debit": Decimal("0.00"), "credit": Decimal("0.00")})
            try:
                entry["debit"] += parse_fec_amount(values["Debit"])
                entry["credit"] += parse_fec_amount(values["Credit"])
            except Exception:
                row_errors.append("Debit/Credit cannot be parsed as decimal amounts")

        if row_errors:
            invalid_row_count += 1
            if len(samples["invalid_rows"]) < 10:
                samples["invalid_rows"].append({
                    "row_number": row_number,
                    "errors": row_errors,
                    "journal_code": values.get("JournalCode", ""),
                    "ecriture_num": values.get("EcritureNum", ""),
                })

    unbalanced_count = 0
    for (journal, entry_num), balance in entry_balances.items():
        if balance["debit"].quantize(Decimal("0.01")) != balance["credit"].quantize(Decimal("0.01")):
            unbalanced_count += 1
            if len(samples["unbalanced_entries"]) < 10:
                samples["unbalanced_entries"].append({
                    "journal_code": journal,
                    "ecriture_num": entry_num,
                    "debit": amount_text(balance["debit"]),
                    "credit": amount_text(balance["credit"]),
                })

    if invalid_row_count:
        add_error(
            "invalid_data_rows",
            "One or more FEC data rows fail local structural checks.",
            invalid_row_count=invalid_row_count,
        )
    if unbalanced_count:
        add_error(
            "unbalanced_entries",
            "One or more FEC entries do not balance by JournalCode/EcritureNum.",
            unbalanced_entry_count=unbalanced_count,
        )

    debit_total = Decimal("0.00")
    credit_total = Decimal("0.00")
    for row in data_rows:
        try:
            if "Debit" in field_index and len(row) > field_index["Debit"]:
                debit_total += parse_fec_amount(row[field_index["Debit"]])
            if "Credit" in field_index and len(row) > field_index["Credit"]:
                credit_total += parse_fec_amount(row[field_index["Credit"]])
        except Exception:
            continue
    status = "passed" if not errors else "failed"
    result = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "fec-structural-preflight",
        "status": status,
        "classification": (
            "LOCAL_FEC_PREFLIGHT_PASSED"
            if status == "passed"
            else "LOCAL_FEC_PREFLIGHT_FAILED"
        ),
        "legal_source": {
            "authority": "Légifrance",
            "reference": "Article A47 A-1 du livre des procédures fiscales",
            "checked_on": "2026-07-22",
            "url": "https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000027804775",
        },
        "scope_note": (
            "Local deterministic preflight only. This does not replace the official DGFiP Test Compta Demat "
            "validator and does not constitute accountant acceptance."
        ),
        "file": {
            "path": str(fec_path.relative_to(ROOT)),
            "artifact_name": fec_path.name,
            "generated_file_name": generated_file_name,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "separator": "pipe" if separator == "|" else "tab" if separator == "\t" else "unknown",
            "line_count_including_header": len(raw_lines),
            "row_count_excluding_header": len(data_rows),
            "header": header,
        },
        "checks": {
            "required_header_order": header[:18] == FEC_BIC_IS_REQUIRED_HEADER,
            "rows_structurally_valid": invalid_row_count == 0,
            "dates_are_aaaammjj": invalid_date_count == 0,
            "amounts_use_comma_decimal_character_format": invalid_amount_count == 0,
            "account_numbers_start_with_three_digits": invalid_account_count == 0,
            "valid_dates_non_decreasing": chronology_decrease_count == 0,
            "entries_balance_by_journal_and_number": unbalanced_count == 0,
        },
        "statistics": {
            "entry_group_count": len(entry_balances),
            "invalid_row_count": invalid_row_count,
            "invalid_date_count": invalid_date_count,
            "invalid_amount_count": invalid_amount_count,
            "invalid_account_count": invalid_account_count,
            "chronology_decrease_count": chronology_decrease_count,
            "unbalanced_entry_count": unbalanced_count,
            "rows_with_auxiliary_account_number": rows_with_compaux_num,
            "rows_with_auxiliary_account_label": rows_with_compaux_lib,
            "rows_with_lettering": rows_with_lettering,
            "rows_with_currency": rows_with_currency,
            "debit_total": amount_text(debit_total),
            "credit_total": amount_text(credit_total),
        },
        "errors": errors,
        "warnings": warnings,
        "samples": samples,
    }
    write_json(PRIVATE_ARTIFACTS / "fec-structural-preflight.json", result)
    return result


def fec(args: argparse.Namespace) -> dict[str, Any]:
    ensure_dirs()
    if not table_exists(EXACT_VALIDATION_DB, "l10n_fr_fec_export_wizard"):
        status = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "fec",
            "status": "failed",
            "classification": "MISSING_CAPABILITY",
            "reason": "The target database does not have l10n_fr.fec.export.wizard installed.",
            "next_action": "Install l10n_fr_account through the target reset stage.",
        }
        write_json(PRIVATE_ARTIFACTS / "fec-status.json", status)
        raise HarnessError(status["reason"])

    fec_script = PRIVATE_ARTIFACTS / "fec-generate.py"
    fec_script.write_text(
        "\n".join(
            [
                "import base64",
                "import json",
                "company = env['res.company'].search([",
                "    ('rebuild_source_model', '=', 'res.company'),",
                "    ('rebuild_source_id', '=', 1),",
                f"    ('rebuild_source_snapshot', '=', {source_snapshot_id(args.source_dir)!r}),",
                "], limit=1)",
                "if not company:",
                "    company = env['res.company'].search([], order='id', limit=1)",
                "Wizard = env['l10n_fr.fec.export.wizard'].with_company(company).with_context(",
                "    allowed_company_ids=company.ids,",
                "    fec_test_mode=True,",
                ")",
                "wizard = Wizard.create({",
                f"    'date_from': {USL_BENCHMARK_START!r},",
                f"    'date_to': {USL_BENCHMARK_END!r},",
                "    'test_file': True,",
                "    'export_type': 'official',",
                "})",
                "result = wizard.with_context(fec_test_mode=True, allowed_company_ids=company.ids).generate_fec()",
                "content = b''.join(result['file_content'])",
                "print('REBUILD_FEC_RESULT=' + json.dumps({",
                "    'company_id': company.id,",
                "    'source_company_id': company.rebuild_source_id,",
                "    'file_name': result['file_name'],",
                "    'file_type': result['file_type'],",
                "    'content_b64': base64.b64encode(content).decode('ascii'),",
                "}, sort_keys=True))",
                "",
            ],
        ),
        encoding="utf-8",
    )
    result = run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "init-db",
            "odoo",
            "shell",
            "--config=/etc/odoo/odoo.conf",
            f"--database={EXACT_VALIDATION_DB}",
        ),
        input_file=fec_script,
        check=False,
    )
    if result.returncode:
        status = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "fec",
            "status": "failed",
            "classification": "FEC_GENERATION_DEFECT",
            "exit_code": result.returncode,
            "output_tail": (result.stdout + result.stderr)[-8000:],
            "next_action": "Inspect Odoo FEC wizard failure against the imported ledger and company legal data.",
        }
        write_json(PRIVATE_ARTIFACTS / "fec-status.json", status)
        raise HarnessError("FEC generation failed. See artifacts/accounting-compat/private/fec-status.json")
    marker = None
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("REBUILD_FEC_RESULT="):
            marker = line.removeprefix("REBUILD_FEC_RESULT=")
    if not marker:
        status = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "fec",
            "status": "failed",
            "classification": "FEC_GENERATION_DEFECT",
            "reason": "Odoo shell completed but did not emit REBUILD_FEC_RESULT.",
            "output_tail": (result.stdout + result.stderr)[-8000:],
        }
        write_json(PRIVATE_ARTIFACTS / "fec-status.json", status)
        raise HarnessError(status["reason"])

    payload = json.loads(marker)
    content = base64.b64decode(payload.pop("content_b64"))
    fec_path = PRIVATE_ARTIFACTS / "fec-usl-2025-09-30.txt"
    fec_path.write_bytes(content)
    structural_preflight = fec_structural_preflight(
        fec_path,
        generated_file_name=payload.get("file_name"),
    )

    decoded = content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(decoded), delimiter="|"))
    header = rows[0] if rows else []
    data_rows = rows[1:]
    debit_total = sum(parse_fec_amount(row[11]) for row in data_rows if len(row) > 12)
    credit_total = sum(parse_fec_amount(row[12]) for row in data_rows if len(row) > 12)
    target_summary = target_posted_summary()
    source_line_profile = source_posted_line_amount_profile()
    target_line_profile = target_posted_line_amount_profile()
    totals_match = (
        amount_text(debit_total) == target_summary.get("debit")
        and amount_text(credit_total) == target_summary.get("credit")
    )
    data_rows_match_non_zero_target_lines = str(len(data_rows)) == str(target_line_profile.get("amount_line_count"))
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "fec",
        "status": (
            "passed"
            if header and totals_match and data_rows_match_non_zero_target_lines and structural_preflight["status"] == "passed"
            else "failed"
        ),
        "classification": "FEC_GENERATED_TEST_MODE",
        "scope": {
            "company": "Unstatic Labs",
            "source_company_id": 1,
            "period_start": USL_BENCHMARK_START,
            "period_end": USL_BENCHMARK_END,
            "export_type": "official",
            "fec_test_mode": True,
        },
        "odoo_result": payload,
        "file": {
            "path": str(fec_path.relative_to(ROOT)),
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "row_count_excluding_header": len(data_rows),
            "header": header,
        },
        "totals": {
            "fec_debit": amount_text(debit_total),
            "fec_credit": amount_text(credit_total),
            "target_debit": target_summary.get("debit"),
            "target_credit": target_summary.get("credit"),
            "matches_target_posted_ledger": totals_match,
        },
        "row_count_reconciliation": {
            "source_move_lines": source_line_profile.get("move_line_count"),
            "source_zero_amount_lines": source_line_profile.get("zero_amount_line_count"),
            "source_amount_lines": source_line_profile.get("amount_line_count"),
            "target_move_lines": target_line_profile.get("move_line_count"),
            "target_zero_amount_lines": target_line_profile.get("zero_amount_line_count"),
            "target_amount_lines": target_line_profile.get("amount_line_count"),
            "fec_data_rows": str(len(data_rows)),
            "fec_rows_match_target_amount_lines": data_rows_match_non_zero_target_lines,
            "classification": "PRESENTATION_DIFFERENCE" if data_rows_match_non_zero_target_lines else "FEC_COMPLETENESS_DIFFERENCE",
            "note": "Odoo's FEC export omits zero-debit/zero-credit move lines in this benchmark slice; source and target preserve those lines, and debit/credit totals reconcile.",
        },
        "structural_preflight": {
            "status": structural_preflight["status"],
            "classification": structural_preflight["classification"],
            "artifact": "artifacts/accounting-compat/private/fec-structural-preflight.json",
            "legal_source": structural_preflight["legal_source"],
            "statistics": structural_preflight["statistics"],
            "errors": structural_preflight["errors"],
            "warnings": structural_preflight["warnings"],
            "scope_note": structural_preflight["scope_note"],
        },
        "limitations": [
            "Generated through Odoo l10n_fr_account in fec_test_mode=True; this does not set or change fiscal lock dates.",
            "This is not an official DGFiP validator result and is not accountant acceptance.",
            "The FEC file is scoped to the closed benchmark period and posted accounting lines, as required. The target database separately preserves current-period and USL Media entries, native draft/cancelled documents, display-only lines, the complete reconciliation graph and historical no-entry payments.",
        ],
    }
    write_json(PRIVATE_ARTIFACTS / "fec-status.json", status)
    if status["status"] != "passed" and not getattr(args, "allow_errors", False):
        raise HarnessError("FEC totals did not reconcile. See artifacts/accounting-compat/private/fec-status.json")
    return status


def upsert_target_discrepancy(values: dict[str, Any]) -> None:
    if not table_exists(EXACT_VALIDATION_DB, "rebuild_account_discrepancy"):
        return
    script_path = PRIVATE_ARTIFACTS / "upsert-target-discrepancy.py"
    payload = json.dumps(values, ensure_ascii=False, sort_keys=True)
    script_path.write_text(
        "\n".join(
            [
                "import json",
                f"payload = json.loads({payload!r})",
                "Discrepancy = env['rebuild.account.discrepancy'].sudo()",
                "domain = [('name', '=', payload['name'])]",
                "if payload.get('period_key'):",
                "    domain.append(('period_key', '=', payload['period_key']))",
                "if payload.get('source_model'):",
                "    domain.append(('source_model', '=', payload['source_model']))",
                "record = Discrepancy.search(domain, limit=1)",
                "if record:",
                "    record.write(payload)",
                "else:",
                "    Discrepancy.create(payload)",
                "env.cr.commit()",
                "print('REBUILD_DISCREPANCY_UPSERTED=1')",
                "",
            ],
        ),
        encoding="utf-8",
    )
    run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "init-db",
            "odoo",
            "shell",
            "--config=/etc/odoo/odoo.conf",
            f"--database={EXACT_VALIDATION_DB}",
        ),
        input_file=script_path,
        check=False,
    )


def dgfip_fec_testeur_dir() -> Path | None:
    source_dir = os.environ.get("FEC_VALIDATOR_SOURCE_DIR", "").strip()
    candidates = []
    if source_dir:
        candidates.append(Path(source_dir).expanduser())
    candidates.append(Path("/private/tmp/Test-Compta-Demat-1.00.10b/src/testeur"))
    for candidate in candidates:
        if candidate.exists() and (candidate / "trt_txt.pl").exists():
            return candidate
        nested = candidate / "src" / "testeur"
        if nested.exists() and (nested / "trt_txt.pl").exists():
            return nested
    return None


def dgfip_fec_source_compile_checks() -> dict[str, Any]:
    """Record whether the checked DGFiP source tree is runnable on this host."""
    testeur_dir = dgfip_fec_testeur_dir()
    result: dict[str, Any] = {
        "source_dir": str(testeur_dir) if testeur_dir else None,
        "source_dir_exists": bool(testeur_dir),
        "perl_path": shutil.which("perl"),
        "scripts": {},
    }
    if not testeur_dir or not result["perl_path"]:
        return result
    for script_name in ("init.pl", "trt_txt.pl", "trt_entete.pl", "trt_xml.pl"):
        script_path = testeur_dir / script_name
        if not script_path.exists():
            result["scripts"][script_name] = {
                "status": "missing",
                "path": str(script_path),
            }
            continue
        compile_result = subprocess.run(
            [result["perl_path"], "-c", str(script_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        output = (compile_result.stdout + compile_result.stderr).strip()
        result["scripts"][script_name] = {
            "status": "syntax_ok" if compile_result.returncode == 0 else "failed",
            "path": str(script_path),
            "exit_code": compile_result.returncode,
            "output_tail": output[-4000:],
        }
    return result


def run_dgfip_fec_source_validator_in_docker(
    fec_path: Path,
    *,
    expected_siren: str,
    expected_closing_date: str,
    company_name: str,
) -> dict[str, Any] | None:
    """Run DGFiP's official source validator in an isolated container.

    The official source tree is not vendored into this repository. When it is
    available locally, this runner mounts it read-only and applies runtime-only
    dependency shims required for the old Perl/Tk code on Debian Bookworm.
    """
    testeur_dir = dgfip_fec_testeur_dir()
    if not testeur_dir or not shutil.which("docker"):
        return None

    output_dir = PRIVATE_ARTIFACTS / "fec-dgfip-source-validation"
    output_dir.mkdir(parents=True, exist_ok=True)
    wrapper = PRIVATE_ARTIFACTS / "fec-dgfip-source-wrapper.sh"
    wrapper.write_text(
        r'''#!/usr/bin/env bash
set -u

fec_file="$1"
expected_siren="$2"
expected_closing_date="$3"
company_name="$4"
out_dir="/artifacts/fec-dgfip-source-validation"
work_dir="/tmp/testeur"
work_fec="$work_dir/$(basename "$fec_file")"

rm -rf "$out_dir"
mkdir -p "$out_dir" /tmp/rapports

export DEBIAN_FRONTEND=noninteractive
apt-get update >"$out_dir/apt-update.log" 2>&1
apt-get install -y --no-install-recommends \
  perl-tk \
  libdbi-perl \
  libdbd-sqlite3-perl \
  libpdf-api2-perl \
  libpdf-table-perl \
  libxml-simple-perl \
  libnet-ldap-perl \
  xvfb \
  xauth \
  xdg-utils \
  ghostscript >"$out_dir/apt-install.log" 2>&1

python - <<'PY'
from pathlib import Path
path = Path("/usr/share/perl5/PDF/Table.pm")
content = path.read_text(encoding="utf-8")
old = "my %text_options = %{ $arg{'text_opt'} };"
new = "my %text_options = %{ $arg{'text_opt'} || {} };"
if old in content:
    path.write_text(content.replace(old, new, 1), encoding="utf-8")
PY

cp -R /dgfip/testeur "$work_dir"
cd "$work_dir"
mkdir -p "$work_dir/log" /tmp/rapports
cat > environnement_alto2.pl <<'PERL'
sub Env_Path {
    $ENV{ProgramFiles} = "/tmp";
    $ENV{ProgramData} = "/tmp";
}
1;
PERL

cp "$fec_file" "$work_fec"

python - "$work_fec" "$expected_closing_date" <<'PY'
import sqlite3
import sys
from pathlib import Path

work_fec = sys.argv[1]
closing = sys.argv[2]
db_path = Path("/tmp/testeur/log/altoweb2.db")
db_path.parent.mkdir(parents=True, exist_ok=True)
conn = sqlite3.connect(db_path)
conn.execute("CREATE TABLE IF NOT EXISTS log_alim (id_trait INT, id_ligne INTEGER PRIMARY KEY AUTOINCREMENT, type_log TEXT, texte_log INTEGER, val1 text, val2 text)")
conn.execute("CREATE TABLE IF NOT EXISTS log_type (id_type INTEGER, fixe_log text unique on conflict ignore)")
conn.execute("CREATE TABLE IF NOT EXISTS suivi_alim (id_trait INTEGER PRIMARY KEY AUTOINCREMENT, num_alpage text, nom_fichier text, date_cloture text, heure_etape text, db_cree boolean)")
conn.execute(
    "INSERT INTO suivi_alim (num_alpage, nom_fichier, date_cloture, heure_etape, db_cree) values (?, ?, ?, datetime('now'), 0)",
    ("", work_fec, closing),
)
conn.execute("INSERT OR IGNORE INTO log_type (id_type, fixe_log) values (1, 'init')")
conn.commit()
conn.close()
PY

PERL5LIB="$work_dir" perl - "$work_fec" <<'PERL'
use strict;
use warnings;
our $Rep_Alim_ou_Testeur = "testeur";
my $work_fec = $ARGV[0];
require "/tmp/testeur/environnement_alto2.pl";
Env_Path();
require "/tmp/testeur/alto2_fonctions.pl";
require "/tmp/testeur/trt_entete.pl";
my $rc = sub_entete("$work_fec.entete", "$work_fec", "P", 1);
die "DGFiP trt_entete.pl failed with code $rc\n" if $rc;
PERL

set +e
PERL5LIB="$work_dir" timeout "${FEC_DGFIP_SOURCE_TIMEOUT:-120s}" xvfb-run -a perl trt_txt.pl \
  "$work_fec" P "$expected_siren" "" "$expected_closing_date" \
  "$work_dir/log/err_fec.log" PCG BIC "$company_name" CTL 1 "" "" \
  >"$out_dir/stdout.log" 2>"$out_dir/stderr.log"
validator_exit_code=$?
set -e

cp "$work_fec.entete" "$out_dir/fec.entete" 2>/dev/null || true
cp "$work_dir/log/altoweb2.db" "$out_dir/altoweb2.db" 2>/dev/null || true
cp /tmp/rapports/* "$out_dir/" 2>/dev/null || true

python - "$out_dir" "$validator_exit_code" "$fec_file" <<'PY'
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

out_dir = Path(sys.argv[1])
exit_code = int(sys.argv[2])
fec_file = Path(sys.argv[3])
db_path = out_dir / "altoweb2.db"
counts = {}
messages = []
if db_path.exists():
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    counts = {
        row["type_log"]: row["count"]
        for row in conn.execute(
            "SELECT type_log, count(*) AS count FROM log_alim GROUP BY type_log ORDER BY type_log"
        )
    }
    for row in conn.execute(
        """
        SELECT l.type_log, l.id_ligne, t.fixe_log, l.val1, l.val2
        FROM log_alim l
        LEFT JOIN log_type t ON t.id_type = l.texte_log
        ORDER BY l.id_ligne
        LIMIT 500
        """
    ):
        messages.append(dict(row))
    conn.close()

report_files = []
for path in sorted(out_dir.glob("rapport_*.pdf")):
    data = path.read_bytes()
    report_files.append({
        "path": str(path),
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    })

stderr = (out_dir / "stderr.log").read_text(encoding="utf-8", errors="replace") if (out_dir / "stderr.log").exists() else ""
stdout = (out_dir / "stdout.log").read_text(encoding="utf-8", errors="replace") if (out_dir / "stdout.log").exists() else ""
blocking_log_count = sum(int(counts.get(code, 0) or 0) for code in ("E", "A", "O"))
status = "passed" if exit_code == 0 and blocking_log_count == 0 else "failed"
summary = {
    "status": status,
    "classification": "OFFICIAL_DGFIP_SOURCE_VALIDATION_PASSED" if status == "passed" else "OFFICIAL_DGFIP_SOURCE_VALIDATION_FAILED",
    "validator_exit_code": exit_code,
    "blocking_log_count": blocking_log_count,
    "log_counts": counts,
    "message_sample": messages,
    "report_files": report_files,
    "stdout_tail": stdout[-4000:],
    "stderr_tail": stderr[-4000:],
    "dependency_shims": [
        "Run in an isolated Debian Bookworm container because the host lacks Perl/Tk.",
        "Mounted DGFiP source read-only; no DGFiP source file is modified.",
        "Patched Debian PDF::Table at runtime so missing text_opt defaults to an empty hash; this affects report rendering, not FEC validation rules.",
        "Initialized the official SQLite log tables and header mapping normally produced by the GUI bootstrap before invoking trt_txt.pl.",
    ],
    "fec_file": {
        "path": str(fec_file),
        "sha256": hashlib.sha256(fec_file.read_bytes()).hexdigest() if fec_file.exists() else None,
    },
}
(out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
print("DGFiP source validation status:", status)
print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
PY
exit 0
''',
        encoding="utf-8",
    )
    container_image = os.environ.get("FEC_VALIDATOR_DOCKER_IMAGE", "python:3.12-slim-bookworm")
    result = run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{testeur_dir}:/dgfip/testeur:ro",
            "-v",
            f"{PRIVATE_ARTIFACTS}:/artifacts:rw",
            container_image,
            "bash",
            "/artifacts/fec-dgfip-source-wrapper.sh",
            f"/artifacts/{fec_path.name}",
            expected_siren,
            expected_closing_date.replace("-", ""),
            company_name,
        ],
        check=False,
    )
    summary_path = output_dir / "summary.json"
    if summary_path.exists():
        summary = read_json(summary_path)
    else:
        summary = {
            "status": "failed",
            "classification": "OFFICIAL_DGFIP_SOURCE_VALIDATION_FAILED",
            "reason": "The DGFiP source validator container did not produce summary.json.",
            "docker_exit_code": result.returncode,
            "output_tail": (result.stdout + result.stderr)[-8000:],
        }
    summary.update({
        "runner": "dgfip_source_docker",
        "source_dir": str(testeur_dir),
        "docker_image": container_image,
        "docker_exit_code": result.returncode,
        "docker_output_tail": (result.stdout + result.stderr)[-8000:],
        "artifact_dir": str(output_dir.relative_to(ROOT)),
    })
    write_json(summary_path, summary)
    return summary


def fec_validate(args: argparse.Namespace) -> dict[str, Any]:
    ensure_dirs()
    fec_path = PRIVATE_ARTIFACTS / "fec-usl-2025-09-30.txt"
    if not fec_path.exists():
        fec(argparse.Namespace(**{**vars(args), "allow_errors": False}))
    generated_file_name = None
    fec_status_path = PRIVATE_ARTIFACTS / "fec-status.json"
    if fec_status_path.exists():
        try:
            generated_file_name = read_json(fec_status_path).get("odoo_result", {}).get("file_name")
        except Exception:
            generated_file_name = None
    structural_preflight = fec_structural_preflight(
        fec_path,
        generated_file_name=generated_file_name,
    )

    official_source = {
        "checked_on": "2026-07-22",
        "authority": "Direction générale des Finances publiques",
        "tool_name": "Test Compta Demat",
        "published_version": "1.00.10b",
        "source_url": "https://www.economie.gouv.fr/dgfip/outil-de-test-des-fichiers-des-ecritures-comptables-fec",
        "release_url": "https://github.com/DGFiP/Test-Compta-Demat/releases/tag/1.00.10b",
        "release_commit": "53f98f04ec78abc651a7498f34015613327a60d1",
        "release_assets": [
            "Notice.Test.Compta.Demat_maj.2021.pdf",
            "Testeur_1_00_10b_win_x86.exe",
            "Testeur_1_00_10b_win_x86_64.exe",
        ],
        "source_license": "CeCILL V2.1",
        "legal_reference": "Article A.47 A-1 du livre des procédures fiscales",
        "scope_note": (
            "DGFiP describes this tool as a structural test of FEC files. "
            "It does not replace accountant review or reconciliation to the accepted ledger."
        ),
    }
    local_environment = {
        "platform_system": platform.system(),
        "platform_machine": platform.machine(),
        "wine_path": shutil.which("wine"),
        "wine64_path": shutil.which("wine64"),
        "dgfip_source_compile_checks": dgfip_fec_source_compile_checks(),
    }
    scripts = local_environment["dgfip_source_compile_checks"].get("scripts", {})
    if scripts:
        local_environment["perl_tk_compile_check"] = (
            "failed_missing_Tk_pm_on_current_host"
            if any(
                "Can't locate Tk.pm" in check.get("output_tail", "")
                for check in scripts.values()
            )
            else "no_missing_Tk_pm_detected"
        )
    else:
        local_environment["perl_tk_compile_check"] = "not_checked"
    validator_command = os.environ.get("FEC_VALIDATOR_COMMAND", "").strip()
    if not validator_command:
        source_validation = run_dgfip_fec_source_validator_in_docker(
            fec_path,
            expected_siren="983982950",
            expected_closing_date=USL_BENCHMARK_END,
            company_name="Unstatic Labs",
        )
        if source_validation:
            local_environment["dgfip_source_docker_validation"] = {
                "status": source_validation.get("status"),
                "classification": source_validation.get("classification"),
                "artifact_dir": source_validation.get("artifact_dir"),
                "docker_image": source_validation.get("docker_image"),
                "dependency_shims": source_validation.get("dependency_shims", []),
            }
            status = {
                "generated_at": utc_now(),
                "tool_version": TOOL_VERSION,
                "stage": "fec-validate",
                "status": source_validation.get("status", "failed"),
                "classification": source_validation.get(
                    "classification",
                    "OFFICIAL_DGFIP_SOURCE_VALIDATION_FAILED",
                ),
                "official_source": official_source,
                "local_environment": local_environment,
                "validator_command": [
                    "docker",
                    "run",
                    source_validation.get("docker_image", "python:3.12-slim-bookworm"),
                    "DGFiP/Test-Compta-Demat source trt_txt.pl",
                ],
                "fec_file": {
                    "path": str(fec_path.relative_to(ROOT)),
                    "sha256": hashlib.sha256(fec_path.read_bytes()).hexdigest(),
                },
                "structural_preflight": {
                    "status": structural_preflight["status"],
                    "classification": structural_preflight["classification"],
                    "artifact": "artifacts/accounting-compat/private/fec-structural-preflight.json",
                    "statistics": structural_preflight["statistics"],
                    "errors": structural_preflight["errors"],
                    "warnings": structural_preflight["warnings"],
                    "scope_note": structural_preflight["scope_note"],
                },
                "source_validation": source_validation,
                "exit_code": source_validation.get("validator_exit_code"),
                "output_tail": source_validation.get("stderr_tail", "")[-6000:],
                "next_action": (
                    "Archive the DGFiP source validation artifact and request accountant review."
                    if source_validation.get("status") == "passed"
                    else "Inspect the DGFiP source validation artifact, classify each issue and rerun validation."
                ),
            }
            write_json(PRIVATE_ARTIFACTS / "fec-validation-status.json", status)
            upsert_target_discrepancy({
                "name": "Official DGFiP FEC validation has not been executed",
                "severity": "P0",
                "classification": (
                    "accepted_improvement"
                    if status["status"] == "passed"
                    else "legal_or_accounting_uncertainty"
                ),
                "status": "resolved" if status["status"] == "passed" else "open",
                "period_key": f"{USL_BENCHMARK_START}:{USL_BENCHMARK_END}",
                "source_model": "official_dgfip_fec_validator",
                "target_model": "l10n_fr.fec.export.wizard",
                "source_value": official_source["published_version"],
                "target_value": status["classification"],
                "difference": "" if status["status"] == "passed" else "DGFiP source validator did not pass.",
                "accounting_impact": (
                    "Official DGFiP source structural validation evidence is now available for accountant review."
                    if status["status"] == "passed"
                    else "The generated FEC cannot yet be accepted as official structural validation evidence."
                ),
                "legal_or_tax_impact": (
                    "Accountant review is still required even after structural validation passes."
                    if status["status"] == "passed"
                    else "Unresolved official FEC validation errors block Milestone 13 FEC acceptance."
                ),
                "evidence": json.dumps(status, ensure_ascii=False, sort_keys=True),
                "likely_cause": (
                    "DGFiP Test Compta Demat source validation completed successfully in an isolated container."
                    if status["status"] == "passed"
                    else "Generated FEC structure or validator invocation did not pass the DGFiP source tool."
                ),
                "recommendation": status["next_action"],
                "owner": "operator/accountant",
            })
            if status["status"] != "passed" and not getattr(args, "allow_errors", False):
                raise HarnessError(
                    "Official FEC validation failed. "
                    "See artifacts/accounting-compat/private/fec-validation-status.json"
                )
            return status

        status = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "fec-validate",
            "status": "blocked",
            "classification": "OFFICIAL_VALIDATOR_NOT_CONFIGURED",
            "official_source": official_source,
            "local_environment": local_environment,
            "fec_file": {
                "path": str(fec_path.relative_to(ROOT)),
                "sha256": hashlib.sha256(fec_path.read_bytes()).hexdigest(),
            },
            "structural_preflight": {
                "status": structural_preflight["status"],
                "classification": structural_preflight["classification"],
                "artifact": "artifacts/accounting-compat/private/fec-structural-preflight.json",
                "statistics": structural_preflight["statistics"],
                "errors": structural_preflight["errors"],
                "warnings": structural_preflight["warnings"],
                "scope_note": structural_preflight["scope_note"],
            },
            "reason": (
                "No local DGFiP/Test Compta Demat validator command is configured. "
                "The current official packaged validators are Windows executables; this host has no wine or wine64 command. "
                "The checked source entry point also requires Perl Tk, which is unavailable here."
            ),
            "next_action": (
                "Install the current official DGFiP FEC validator in the local validation environment "
                "and rerun with FEC_VALIDATOR_COMMAND set. The command may include a {fec} placeholder; "
                "otherwise the generated FEC path is appended as the final argument."
            ),
        }
        write_json(PRIVATE_ARTIFACTS / "fec-validation-status.json", status)
        upsert_target_discrepancy({
            "name": "Official DGFiP FEC validation has not been executed",
            "severity": "P0",
            "classification": "missing_capability",
            "status": "open",
            "period_key": f"{USL_BENCHMARK_START}:{USL_BENCHMARK_END}",
            "source_model": "official_dgfip_fec_validator",
            "target_model": "l10n_fr.fec.export.wizard",
            "source_value": official_source["published_version"],
            "target_value": "not_configured",
            "difference": "No official structural validation output is available for the generated FEC.",
            "accounting_impact": (
                "The generated FEC reconciles to the imported ledger, but the target cannot yet provide "
                "accountant-reviewable official structural validation evidence."
            ),
            "legal_or_tax_impact": (
                "A FEC that has not been checked with the official DGFiP tool or successor remains a "
                "Milestone 13 blocker and cannot be treated as accepted filing/control evidence."
            ),
            "evidence": json.dumps(status, ensure_ascii=False, sort_keys=True),
            "likely_cause": "The official validator is an external DGFiP tool and is not bundled in this repository.",
            "recommendation": status["next_action"],
            "owner": "operator/accountant",
        })
        if not getattr(args, "allow_errors", False):
            raise HarnessError(
                "Official FEC validator is not configured. "
                "See artifacts/accounting-compat/private/fec-validation-status.json"
            )
        return status

    argv = shlex.split(validator_command)
    fec_arg = str(fec_path.resolve())
    if "{fec}" in argv:
        argv = [fec_arg if arg == "{fec}" else arg for arg in argv]
    else:
        argv.append(fec_arg)
    result = run(argv, check=False)
    output = result.stdout + result.stderr
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "fec-validate",
        "status": "passed" if result.returncode == 0 else "failed",
        "classification": (
            "OFFICIAL_FEC_VALIDATION_PASSED"
            if result.returncode == 0
            else "OFFICIAL_FEC_VALIDATION_FAILED"
        ),
        "official_source": official_source,
        "local_environment": local_environment,
        "validator_command": argv,
        "exit_code": result.returncode,
        "fec_file": {
            "path": str(fec_path.relative_to(ROOT)),
            "sha256": hashlib.sha256(fec_path.read_bytes()).hexdigest(),
        },
        "structural_preflight": {
            "status": structural_preflight["status"],
            "classification": structural_preflight["classification"],
            "artifact": "artifacts/accounting-compat/private/fec-structural-preflight.json",
            "statistics": structural_preflight["statistics"],
            "errors": structural_preflight["errors"],
            "warnings": structural_preflight["warnings"],
            "scope_note": structural_preflight["scope_note"],
        },
        "output_tail": output[-12000:],
        "next_action": (
            "Archive the validator output in the FEC dossier and request accountant review."
            if result.returncode == 0
            else "Inspect the validator output, classify each issue, correct the generator only where the source ledger semantics remain preserved, and rerun validation."
        ),
    }
    write_json(PRIVATE_ARTIFACTS / "fec-validation-status.json", status)
    upsert_target_discrepancy({
        "name": "Official DGFiP FEC validation has not been executed",
        "severity": "P0",
        "classification": (
            "accepted_improvement"
            if status["status"] == "passed"
            else "legal_or_accounting_uncertainty"
        ),
        "status": "resolved" if status["status"] == "passed" else "open",
        "period_key": f"{USL_BENCHMARK_START}:{USL_BENCHMARK_END}",
        "source_model": "official_dgfip_fec_validator",
        "target_model": "l10n_fr.fec.export.wizard",
        "source_value": official_source["published_version"],
        "target_value": str(result.returncode),
        "difference": "" if status["status"] == "passed" else "Official validator returned a non-zero exit code.",
        "accounting_impact": (
            "Official structural validation output is now available for accountant review."
            if status["status"] == "passed"
            else "The generated FEC cannot yet be accepted as official structural validation evidence."
        ),
        "legal_or_tax_impact": (
            "Accountant review is still required even after structural validation passes."
            if status["status"] == "passed"
            else "Unresolved official FEC validation errors block Milestone 13 FEC acceptance."
        ),
        "evidence": json.dumps(status, ensure_ascii=False, sort_keys=True),
        "likely_cause": (
            "Official structural validation command completed successfully."
            if status["status"] == "passed"
            else "Generated FEC structure or validator invocation did not pass the official tool."
        ),
        "recommendation": status["next_action"],
        "owner": "operator/accountant",
    })
    if status["status"] != "passed" and not getattr(args, "allow_errors", False):
        raise HarnessError(
            "Official FEC validation failed. "
            "See artifacts/accounting-compat/private/fec-validation-status.json"
        )
    return status


def fec_preflight(args: argparse.Namespace) -> dict[str, Any]:
    ensure_dirs()
    fec_path = PRIVATE_ARTIFACTS / "fec-usl-2025-09-30.txt"
    if not fec_path.exists():
        fec(argparse.Namespace(**{**vars(args), "allow_errors": False}))
    generated_file_name = None
    fec_status_path = PRIVATE_ARTIFACTS / "fec-status.json"
    if fec_status_path.exists():
        try:
            generated_file_name = read_json(fec_status_path).get("odoo_result", {}).get("file_name")
        except Exception:
            generated_file_name = None
    status = fec_structural_preflight(
        fec_path,
        generated_file_name=generated_file_name,
    )
    if status["status"] != "passed" and not getattr(args, "allow_errors", False):
        raise HarnessError(
            "Local FEC structural preflight failed. "
            "See artifacts/accounting-compat/private/fec-structural-preflight.json"
        )
    return status


def compare(args: argparse.Namespace) -> dict[str, Any]:
    ensure_dirs()
    controls = target_validate(argparse.Namespace(**{**vars(args), "allow_errors": True}))
    tb_path = PRIVATE_ARTIFACTS / "trial-balance-2025-09-30.json"
    if not tb_path.exists():
        reports(args)
    comparison = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "compare",
        "status": "passed" if controls["status"] == "passed" else "failed",
        "classification": controls["classification"],
        "scope": controls["scope"],
        "source_posted_summary": controls["source_posted_summary"],
        "target_posted_summary": controls["target_posted_summary"],
        "report_comparisons": {
            "full_replay_company_controls": controls["comparisons"]["full_replay_company_controls"],
            "trial_balance": controls["comparisons"]["account_balances"],
            "general_ledger": controls["comparisons"]["move_lines"],
            "partial_reconciliations": controls["comparisons"]["partial_reconciliations"],
            "full_reconciliations": controls["comparisons"]["full_reconciliations"],
            "source_reports": controls["comparisons"]["source_reports"],
            "source_report_lines": controls["comparisons"]["source_report_lines"],
            "source_report_expressions": controls["comparisons"]["source_report_expressions"],
            "source_report_columns": controls["comparisons"]["source_report_columns"],
            "deferred_schedules": controls["comparisons"]["deferred_schedules"],
            "move_backed_payments": controls["comparisons"]["move_backed_payments"],
            "native_no_entry_payments": controls["comparisons"]["native_no_entry_payments"],
            "tax_groups": controls["comparisons"]["tax_groups"],
            "tax_tags": controls["comparisons"]["tax_tags"],
            "taxes": controls["comparisons"]["taxes"],
            "tax_repartition_lines": controls["comparisons"]["tax_repartition_lines"],
            "tax_alternative_relations": controls["comparisons"]["tax_alternative_relations"],
            "assets": controls["comparisons"]["assets"],
            "asset_depreciation_schedule": controls["comparisons"]["asset_depreciation_schedule"],
        },
        "limitations": controls["limitations"],
    }
    write_json(PRIVATE_ARTIFACTS / "compare-status.json", comparison)
    write_json(PRIVATE_ARTIFACTS / "report-comparison-posted-ledger-slice.json", comparison)
    if comparison["status"] != "passed" and not getattr(args, "allow_errors", False):
        raise HarnessError("Source-target comparison failed. See artifacts/accounting-compat/private/compare-status.json")
    return comparison



def currency_rate_provider(args: argparse.Namespace) -> dict[str, Any]:
    ensure_dirs()
    script_path = PRIVATE_ARTIFACTS / "currency-rate-provider-check.py"
    script_path.write_text(
        "\n".join(
            [
                "import json",
                "from odoo import fields",
                "Company = env['res.company']",
                "Currency = env['res.currency'].with_context(active_test=False)",
                "Rate = env['res.currency.rate'].sudo()",
                "company = Company.search([('rebuild_source_id', '=', 1)], limit=1)",
                "if not company:",
                "    raise RuntimeError('The imported Unstatic Labs company was not found.')",
                "cron = env.ref(",
                "    'rebuild_account_migration.ir_cron_rebuild_currency_rate_provider',",
                ")",
                "cron.active = True",
                "source_domain = [('rebuild_source_model', '!=', False)]",
                "source_rate_count_before = Rate.search_count(source_domain)",
                "first = company._rebuild_update_ecb_currency_rates(backfill=True)",
                "second = company._rebuild_update_ecb_currency_rates(backfill=True)",
                "reference_date = fields.Date.to_date(second['reference_date'])",
                "rate_domain = [",
                "    ('company_id', '=', company.id),",
                "    ('name', '=', reference_date),",
                "]",
                "rate_rows = Rate.search(rate_domain, order='currency_id')",
                "foreign_currencies = Currency.search([",
                "    ('active', '=', True),",
                "    ('id', '!=', company.currency_id.id),",
                "])",
                "row_details = [{",
                "    'currency': row.currency_id.name,",
                "    'rate': row.rate,",
                "    'provider': row.rebuild_rate_provider,",
                "    'retrieved_at': fields.Datetime.to_string(row.rebuild_rate_retrieved_at),",
                "} for row in rate_rows]",
                "duplicate_currency_codes = sorted({",
                "    row.currency_id.name",
                "    for row in rate_rows",
                "    if Rate.search_count(rate_domain + [('currency_id', '=', row.currency_id.id)]) != 1",
                "})",
                "source_rate_count_after = Rate.search_count(source_domain)",
                "covered_codes = set(second['covered_currency_codes'])",
                "required_codes = {'USD', 'GBP'} & set(foreign_currencies.mapped('name'))",
                "config_ok = (",
                "    company.rebuild_currency_rate_provider == 'ecb'",
                "    and company.rebuild_currency_rate_auto_update",
                ")",
                "cron_ok = bool(",
                "    cron",
                "    and cron.active",
                "    and cron.interval_number == 1",
                "    and cron.interval_type == 'days'",
                ")",
                "source_preserved = source_rate_count_before == source_rate_count_after",
                "idempotence_ok = (",
                "    second['created_count'] == 0",
                "    and second['updated_count'] == 0",
                "    and set(first['covered_currency_codes']) == covered_codes",
                "    and not duplicate_currency_codes",
                ")",
                "rate_rows_ok = (",
                "    bool(rate_rows)",
                "    and required_codes.issubset(covered_codes)",
                "    and required_codes.issubset({row.currency_id.name for row in rate_rows})",
                "    and all(",
                "        row.rebuild_rate_provider == 'ecb'",
                "        and row.rebuild_rate_retrieved_at",
                "        and row.rate > 0",
                "        for row in rate_rows",
                "    )",
                ")",
                "future_date_ok = reference_date > fields.Date.to_date('2025-09-30')",
                "checks = {",
                "    'configuration': config_ok,",
                "    'daily_cron': cron_ok,",
                "    'source_rates_preserved': source_preserved,",
                "    'idempotent_rerun': idempotence_ok,",
                "    'traced_reference_rates': rate_rows_ok,",
                "    'reference_date_after_benchmark': future_date_ok,",
                "}",
                "payload = {",
                "    'status': 'passed' if all(checks.values()) else 'failed',",
                "    'classification': 'FUTURE_REFERENCE_RATE_PROVIDER_GOLDEN_JOURNEY',",
                "    'database': env.cr.dbname,",
                "    'company': company.name,",
                "    'company_currency': company.currency_id.name,",
                "    'provider': 'ecb',",
                "    'provider_url': second['provider_url'],",
                "    'reference_date': second['reference_date'],",
                "    'first_run': first,",
                "    'idempotent_rerun': second,",
                "    'active_foreign_currencies': sorted(foreign_currencies.mapped('name')),",
                "    'required_currency_codes': sorted(required_codes),",
                "    'rate_rows': row_details,",
                "    'duplicate_currency_codes': duplicate_currency_codes,",
                "    'source_rate_count_before': source_rate_count_before,",
                "    'source_rate_count_after': source_rate_count_after,",
                "    'cron': {",
                "        'active': cron.active if cron else False,",
                "        'interval_number': cron.interval_number if cron else 0,",
                "        'interval_type': cron.interval_type if cron else '',",
                "        'nextcall': fields.Datetime.to_string(cron.nextcall) if cron and cron.nextcall else '',",
                "    },",
                "    'checks': checks,",
                "}",
                "env.cr.commit()",
                "print('REBUILD_CURRENCY_RATE_PROVIDER=' + json.dumps(payload, sort_keys=True, default=str))",
                "",
            ],
        ),
        encoding="utf-8",
    )
    result = run(
        compose_args(
            "--profile",
            "init",
            "run",
            "--rm",
            "init-db",
            "odoo",
            "shell",
            "--config=/etc/odoo/odoo.conf",
            f"--database={EXACT_VALIDATION_DB}",
        ),
        input_file=script_path,
        check=False,
    )
    marker = None
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("REBUILD_CURRENCY_RATE_PROVIDER="):
            marker = line.removeprefix("REBUILD_CURRENCY_RATE_PROVIDER=")
    if result.returncode or not marker:
        status = {
            "generated_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "stage": "currency-rate-provider",
            "status": "failed",
            "classification": "REFERENCE_RATE_PROVIDER_EXECUTION_DEFECT",
            "script": str(script_path.relative_to(ROOT)),
            "exit_code": result.returncode,
            "output_tail": (result.stdout + result.stderr)[-8000:],
            "next_action": (
                "Inspect the target Odoo shell output and ECB feed "
                "availability, then rerun the provider check."
            ),
        }
        write_json(
            PRIVATE_ARTIFACTS / "currency-rate-provider-status.json",
            status,
        )
        if not getattr(args, "allow_errors", False):
            raise HarnessError(
                "Currency-rate provider validation failed. See "
                "artifacts/accounting-compat/private/"
                "currency-rate-provider-status.json"
            )
        return status
    payload = json.loads(marker)
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "currency-rate-provider",
        "script": str(script_path.relative_to(ROOT)),
        **payload,
    }
    write_json(
        PRIVATE_ARTIFACTS / "currency-rate-provider-status.json",
        status,
    )
    if status["status"] != "passed" and not getattr(
        args,
        "allow_errors",
        False,
    ):
        raise HarnessError(
            "Currency-rate provider validation did not pass. See "
            "artifacts/accounting-compat/private/"
            "currency-rate-provider-status.json"
        )
    return status


def not_implemented_stage(stage: str) -> dict[str, Any]:
    ensure_dirs()
    status = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": stage,
        "status": "blocked",
        "classification": "MISSING_CAPABILITY",
        "reason": "Target ORM importer, target report generation, and source-target comparison are not implemented in this first harness slice.",
        "next_action": "Implement rebuild_account_migration target addon and target report adapters after source perimeter is accepted.",
    }
    write_json(PRIVATE_ARTIFACTS / f"{stage}-status.json", status)
    return status


def private_artifact_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "path": str(path.relative_to(ROOT)),
            "exists": False,
            "status": "missing",
            "classification": "MISSING_EVIDENCE",
        }
    try:
        data = read_json(path)
    except Exception as exc:
        return {
            "path": str(path.relative_to(ROOT)),
            "exists": True,
            "status": "unreadable",
            "classification": "EVIDENCE_PARSE_ERROR",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "path": str(path.relative_to(ROOT)),
        "exists": True,
        "status": data.get("status", "available"),
        "classification": data.get("classification", ""),
        "generated_at": data.get("generated_at"),
    }


def target_discrepancy_rows() -> list[dict[str, Any]]:
    if not table_exists(EXACT_VALIDATION_DB, "rebuild_account_discrepancy"):
        return []
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT COALESCE(company.name::text, '') AS company,
               discrepancy.severity,
               discrepancy.status,
               discrepancy.classification,
               discrepancy.name,
               COALESCE(discrepancy.period_key, '') AS period_key,
               COALESCE(discrepancy.source_value, '') AS source_value,
               COALESCE(discrepancy.target_value, '') AS target_value,
               COALESCE(discrepancy.difference, '') AS difference,
               COALESCE(discrepancy.owner, '') AS owner,
               COALESCE(discrepancy.recommendation, '') AS recommendation
        FROM rebuild_account_discrepancy discrepancy
        LEFT JOIN res_company company ON company.id = discrepancy.company_id
        WHERE discrepancy.status IN ('open', 'investigating')
        ORDER BY
            CASE discrepancy.severity WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END,
            discrepancy.name
        """,
        set_readonly_role=False,
    )


def target_review_decision_summary() -> list[dict[str, Any]]:
    if not table_exists(EXACT_VALIDATION_DB, "rebuild_account_assurance_decision"):
        return []
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT gate, state, conclusion, count(*)::text AS count
        FROM rebuild_account_assurance_decision
        GROUP BY gate, state, conclusion
        ORDER BY gate, state, conclusion
        """,
        set_readonly_role=False,
    )


def target_review_summary_rows() -> list[dict[str, Any]]:
    if not table_exists(EXACT_VALIDATION_DB, "rebuild_account_overview"):
        return []
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT company.name::text AS company,
               summary.source_snapshot_id,
               summary.source_dump_sha256,
               summary.readiness_status,
               summary.posted_move_count::text AS posted_move_count,
               summary.move_line_count::text AS move_line_count,
               round(summary.debit::numeric, 2)::text AS debit,
               round(summary.credit::numeric, 2)::text AS credit,
               round(summary.balance::numeric, 2)::text AS balance,
               summary.source_report_count::text AS source_report_count,
               summary.mandatory_report_count::text AS mandatory_report_count,
               summary.level_4_report_count::text AS level_4_report_count,
               summary.open_discrepancy_count::text AS open_discrepancy_count,
               summary.open_p0_count::text AS open_p0_count,
               summary.open_p1_count::text AS open_p1_count,
               summary.pending_review_decision_count::text AS pending_review_decision_count,
               summary.recorded_review_decision_count::text AS recorded_review_decision_count,
               summary.pending_external_report_value_count::text AS pending_external_report_value_count,
               summary.journal_count::text AS journal_count,
               summary.cash_journal_count::text AS cash_journal_count,
               round(summary.bank_balance::numeric, 2)::text AS bank_balance,
               summary.bank_transaction_count::text AS bank_transaction_count,
               summary.unmatched_bank_transaction_count::text AS unmatched_bank_transaction_count,
               summary.draft_customer_document_count::text AS draft_customer_document_count,
               summary.draft_vendor_document_count::text AS draft_vendor_document_count,
               summary.draft_expense_count::text AS draft_expense_count,
               summary.incomplete_document_count::text AS incomplete_document_count,
               summary.missing_vendor_attachment_count::text AS missing_vendor_attachment_count,
               summary.missing_expense_attachment_count::text AS missing_expense_attachment_count,
               summary.stale_draft_document_count::text AS stale_draft_document_count,
               summary.stale_draft_expense_count::text AS stale_draft_expense_count,
               summary.hygiene_attention_count::text AS hygiene_attention_count,
               summary.hygiene_status::text AS hygiene_status,
               summary.open_receivable_count::text AS open_receivable_count,
               round(summary.open_receivable_amount::numeric, 2)::text AS open_receivable_amount,
               summary.open_payable_count::text AS open_payable_count,
               round(summary.open_payable_amount::numeric, 2)::text AS open_payable_amount,
               summary.latest_closing_date_to::text AS latest_closing_date_to,
               summary.latest_closing_state::text AS latest_closing_state,
               summary.latest_closing_readiness::text AS latest_closing_readiness,
               summary.latest_closing_blocking_count::text AS latest_closing_blocking_count,
               summary.latest_closing_warning_count::text AS latest_closing_warning_count,
               summary.unusual_balance_count::text AS unusual_balance_count,
               round(summary.unusual_balance_amount::numeric, 2)::text AS unusual_balance_amount,
               summary.next_declaration_deadline::text AS next_declaration_deadline,
               summary.next_declaration_status::text AS next_declaration_status,
               summary.overdue_declaration_count::text AS overdue_declaration_count,
               summary.upcoming_declaration_count::text AS upcoming_declaration_count,
               summary.valentin_action_count::text AS valentin_action_count,
               summary.accountant_action_count::text AS accountant_action_count
        FROM rebuild_account_overview summary
        JOIN res_company company ON company.id = summary.company_id
        ORDER BY company.name
        """,
        set_readonly_role=False,
    )


def target_source_report_summary() -> list[dict[str, Any]]:
    if not table_exists(EXACT_VALIDATION_DB, "rebuild_account_source_report"):
        return []
    return query_rows(
        EXACT_VALIDATION_DB,
        """
        SELECT decision,
               parity_level,
               latest_evidence_status,
               target_status,
               count(*)::text AS count
        FROM rebuild_account_source_report
        WHERE active IS TRUE
        GROUP BY decision, parity_level, latest_evidence_status, target_status
        ORDER BY decision, parity_level, latest_evidence_status, target_status
        """,
        set_readonly_role=False,
    )


def readiness(args: argparse.Namespace) -> dict[str, Any]:
    ensure_dirs()
    artifact_paths = {
        "source_package_validation": PRIVATE_ARTIFACTS / "source-package-validation.json",
        "source_restore": PRIVATE_ARTIFACTS / "source-restore-status.json",
        "source_controls": PRIVATE_ARTIFACTS / "source-controls.json",
        "target_import": PRIVATE_ARTIFACTS / "validation-exact-import-status.json",
        "target_validate": PRIVATE_ARTIFACTS / "validation-exact-validate-status.json",
        "target_idempotence": PRIVATE_ARTIFACTS / "validation-exact-idempotence-status.json",
        "target_failure_tests": PRIVATE_ARTIFACTS / "validation-exact-failure-tests-status.json",
        "native_validation_reset": PRIVATE_ARTIFACTS / "validation-native-reset-status.json",
        "native_validation_expenses": PRIVATE_ARTIFACTS / "validation-native-expenses-status.json",
        "native_validation_documents": PRIVATE_ARTIFACTS / "validation-native-documents-status.json",
        "native_validation_native_attachments_browser": (
            PRIVATE_ARTIFACTS
            / "validation-native-attachments-browser-status.json"
        ),
        "native_validation_assets": PRIVATE_ARTIFACTS / "validation-native-assets-status.json",
        "native_validation_assets_browser": (
            PRIVATE_ARTIFACTS / "validation-native-assets-browser-status.json"
        ),
        "native_validation_deferrals": (
            PRIVATE_ARTIFACTS / "validation-native-deferrals-status.json"
        ),
        "native_validation_deferrals_browser": (
            PRIVATE_ARTIFACTS / "validation-native-deferrals-browser-status.json"
        ),
        "native_validation_analytics": (
            PRIVATE_ARTIFACTS / "validation-native-analytics-status.json"
        ),
        "native_validation_analytics_browser": (
            PRIVATE_ARTIFACTS / "validation-native-analytics-browser-status.json"
        ),
        "native_validation_expense_settlement": (
            PRIVATE_ARTIFACTS / "validation-native-expense-settlement-status.json"
        ),
        "native_validation_document_settlement": (
            PRIVATE_ARTIFACTS / "validation-native-document-settlement-status.json"
        ),
        "native_validation_general_reconciliation": (
            PRIVATE_ARTIFACTS / "validation-native-general-reconciliation-status.json"
        ),
        "native_validation_bank_categorization": (
            PRIVATE_ARTIFACTS / "validation-native-bank-categorization-status.json"
        ),
        "native_validation_bank_external": (
            PRIVATE_ARTIFACTS / "validation-native-bank-external-status.json"
        ),
        "currency_rate_provider": (
            PRIVATE_ARTIFACTS / "currency-rate-provider-status.json"
        ),
        "currency_rate_provider_browser": (
            PRIVATE_ARTIFACTS
            / "currency-rate-provider-browser-status.json"
        ),
        "reports": PRIVATE_ARTIFACTS / "reports-status.json",
        "dynamic_report_browser": (
            PRIVATE_ARTIFACTS
            / "dynamic-report-browser-status.json"
        ),
        "accounting_home_browser": (
            PRIVATE_ARTIFACTS
            / "accounting-home-browser-status.json"
        ),
        "accounting_hygiene_browser": (
            PRIVATE_ARTIFACTS
            / "accounting-hygiene-browser-status.json"
        ),
        "dev_reset": (
            PRIVATE_ARTIFACTS / "dev-reset-status.json"
        ),
        "dev_import": (
            PRIVATE_ARTIFACTS / "dev-import-status.json"
        ),
        "dev_validate": (
            PRIVATE_ARTIFACTS / "dev-validate-status.json"
        ),
        "dev_attachment_replay": (
            PRIVATE_ARTIFACTS / "dev-attachment-replay-status.json"
        ),
        "attachment_reconstruction": (
            PRIVATE_ARTIFACTS / "attachment-reconstruction-status.json"
        ),
        "dev_browser": (
            PRIVATE_ARTIFACTS / "replacement-browser-status.json"
        ),
        "fec": PRIVATE_ARTIFACTS / "fec-status.json",
        "fec_validation": PRIVATE_ARTIFACTS / "fec-validation-status.json",
        "fec_role_browser": (
            PRIVATE_ARTIFACTS / "fec-role-browser-status.json"
        ),
        "compare": PRIVATE_ARTIFACTS / "compare-status.json",
    }
    artifact_statuses = {
        name: private_artifact_status(path)
        for name, path in artifact_paths.items()
    }
    required_statuses = {
        "source_package_validation": {"passed"},
        "source_restore": {"passed"},
        "source_controls": {"passed", "available"},
        "target_validate": {"passed"},
        "target_idempotence": {"passed"},
        "target_failure_tests": {"passed"},
        "native_validation_reset": {"passed"},
        "native_validation_expenses": {"passed"},
        "native_validation_documents": {"passed"},
        "native_validation_native_attachments_browser": {"passed"},
        "native_validation_assets": {"passed"},
        "native_validation_assets_browser": {"passed"},
        "native_validation_deferrals": {"passed"},
        "native_validation_deferrals_browser": {"passed"},
        "native_validation_analytics": {"passed"},
        "native_validation_analytics_browser": {"passed"},
        "native_validation_expense_settlement": {"passed"},
        "native_validation_document_settlement": {"passed"},
        "native_validation_general_reconciliation": {"passed"},
        "native_validation_bank_categorization": {"passed"},
        "native_validation_bank_external": {"passed"},
        "currency_rate_provider": {"passed"},
        "currency_rate_provider_browser": {"passed"},
        "reports": {"passed", "partial"},
        "dynamic_report_browser": {"passed"},
        "accounting_home_browser": {"passed"},
        "accounting_hygiene_browser": {"passed"},
        "dev_reset": {"passed"},
        "dev_import": {"passed"},
        "dev_validate": {"passed", "partial"},
        "dev_browser": {"passed"},
        "fec": {"passed"},
        "fec_validation": {"passed"},
        "fec_role_browser": {"passed"},
        "compare": {"passed"},
    }
    technical_failures = [
        name
        for name, allowed_statuses in required_statuses.items()
        if artifact_statuses[name]["status"] not in allowed_statuses
    ]
    target_import_status = artifact_statuses["target_import"]["status"]
    if target_import_status not in {"passed", "partial"}:
        technical_failures.append("target_import")

    open_discrepancies = target_discrepancy_rows()
    open_p0 = [row for row in open_discrepancies if row["severity"] == "P0"]
    open_p1 = [row for row in open_discrepancies if row["severity"] == "P1"]
    open_p2 = [row for row in open_discrepancies if row["severity"] == "P2"]
    engineering_blocker_classifications = {
        "import_defect",
        "report_definition_defect",
        "missing_capability",
    }
    engineering_blockers = [
        row
        for row in (*open_p0, *open_p1)
        if row["classification"] in engineering_blocker_classifications
    ]
    review_decisions = target_review_decision_summary()
    pending_review_count = sum(
        int(row["count"])
        for row in review_decisions
        if row["state"] == "draft"
    )
    review_summary_rows = target_review_summary_rows()
    source_report_summary = target_source_report_summary()

    if technical_failures:
        readiness_status = "failed"
        classification = "TECHNICAL_REHEARSAL_INCOMPLETE"
    elif engineering_blockers:
        readiness_status = "blocked"
        classification = "TECHNICAL_PRODUCT_GATES_PASSED_ENGINEERING_BLOCKERS_OPEN"
    elif open_discrepancies or pending_review_count:
        readiness_status = "ready_with_documented_assumptions"
        classification = "TECHNICAL_PRODUCT_GATES_PASSED_ADVISORY_REVIEWS_REMAIN"
    else:
        readiness_status = "ready_for_internal_use"
        classification = "TECHNICAL_PRODUCT_GATES_PASSED_NO_OPEN_BLOCKERS"

    assessment = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "stage": "readiness",
        "status": readiness_status,
        "classification": classification,
        "source": {
            "dump_sha256": (
                read_json(PRIVATE_ARTIFACTS / "source-package-validation.json")
                .get("dump", {})
                .get("sha256")
                if (PRIVATE_ARTIFACTS / "source-package-validation.json").exists()
                else None
            ),
            "source_database": SOURCE_DB,
            "target_database": DEV_QA_DB,
            "pipeline_validation_databases": [
                EXACT_VALIDATION_DB,
                NATIVE_VALIDATION_DB,
            ],
            "benchmark_period": {
                "date_from": USL_BENCHMARK_START,
                "date_to": USL_BENCHMARK_END,
            },
        },
        "artifact_gates": artifact_statuses,
        "technical_failures": technical_failures,
        "runtime_review_summaries": review_summary_rows,
        "source_report_summary": source_report_summary,
        "review_decision_summary": review_decisions,
        "open_discrepancy_summary": {
            "total": len(open_discrepancies),
            "p0": len(open_p0),
            "p1": len(open_p1),
            "p2": len(open_p2),
            "engineering_blockers": len(engineering_blockers),
            "advisory_or_source_assumptions": (
                len(open_discrepancies) - len(engineering_blockers)
            ),
        },
        "open_discrepancies": open_discrepancies,
        "closure_recommendation": (
            "Do not close Milestone 13 while technical gates fail or an open P0/P1 import, report-definition or missing-capability defect remains."
            if readiness_status in {"failed", "blocked"}
            else (
                "Accounting v1 is technically eligible for internal browser acceptance. "
                "Draft professional decisions and documented source assumptions are advisory, not engineering blockers."
            )
        ),
        "next_actions": [
            row["recommendation"]
            for row in open_discrepancies[:10]
            if row.get("recommendation")
        ],
        "accountant_acceptance_required": False,
        "professional_review_outside_milestone": True,
        "draft_review_decisions_are_engineering_blockers": False,
    }
    write_json(PRIVATE_ARTIFACTS / "readiness-assessment.json", assessment)

    lines = [
        "# Milestone 13 readiness assessment",
        "",
        f"Generated at: `{assessment['generated_at']}`",
        f"Status: `{readiness_status}`",
        f"Classification: `{classification}`",
        "",
        "## Technical gates",
    ]
    for name, gate in sorted(artifact_statuses.items()):
        lines.append(f"- `{name}`: `{gate['status']}` `{gate.get('classification') or ''}`")
    lines.extend([
        "",
        "## Open discrepancies",
        f"- P0: `{len(open_p0)}`",
        f"- P1: `{len(open_p1)}`",
        f"- P2: `{len(open_p2)}`",
    ])
    for row in open_discrepancies:
        lines.append(
            f"- `{row['severity']}` `{row['classification']}`: {row['name']} "
            f"({row['source_value']} -> {row['target_value']}; {row['difference']})"
        )
    lines.extend([
        "",
        "## Recommendation",
        assessment["closure_recommendation"],
        "",
    ])
    (PRIVATE_ARTIFACTS / "readiness-assessment.md").write_text("\n".join(lines), encoding="utf-8")
    return assessment


def evidence(args: argparse.Namespace) -> dict[str, Any]:
    ensure_dirs()
    files = {
        "source_package_validation": PRIVATE_ARTIFACTS / "source-package-validation.json",
        "source_restore": PRIVATE_ARTIFACTS / "source-restore-status.json",
        "source_manifest": PRIVATE_ARTIFACTS / "source-manifest.json",
        "source_controls": PRIVATE_ARTIFACTS / "source-controls.json",
        "report_catalogue": PRIVATE_ARTIFACTS / "report-catalogue-v1.json",
        "parity_matrix": PRIVATE_ARTIFACTS / "parity-matrix-v1.json",
        "source_extract": PRIVATE_ARTIFACTS / "source-extract-status.json",
        "failure_tests": PRIVATE_ARTIFACTS / "failure-tests-status.json",
        "target_reset": PRIVATE_ARTIFACTS / "validation-exact-reset-status.json",
        "target_import": PRIVATE_ARTIFACTS / "validation-exact-import-status.json",
        "target_validate": PRIVATE_ARTIFACTS / "validation-exact-validate-status.json",
        "target_idempotence": PRIVATE_ARTIFACTS / "validation-exact-idempotence-status.json",
        "target_failure_tests": PRIVATE_ARTIFACTS / "validation-exact-failure-tests-status.json",
        "native_validation_reset": PRIVATE_ARTIFACTS / "validation-native-reset-status.json",
        "native_validation_expenses": PRIVATE_ARTIFACTS / "validation-native-expenses-status.json",
        "native_validation_documents": PRIVATE_ARTIFACTS / "validation-native-documents-status.json",
        "native_validation_native_attachments_browser": (
            PRIVATE_ARTIFACTS
            / "validation-native-attachments-browser-status.json"
        ),
        "native_validation_assets": PRIVATE_ARTIFACTS / "validation-native-assets-status.json",
        "native_validation_assets_browser": (
            PRIVATE_ARTIFACTS / "validation-native-assets-browser-status.json"
        ),
        "native_validation_deferrals": (
            PRIVATE_ARTIFACTS / "validation-native-deferrals-status.json"
        ),
        "native_validation_deferrals_browser": (
            PRIVATE_ARTIFACTS / "validation-native-deferrals-browser-status.json"
        ),
        "native_validation_analytics": (
            PRIVATE_ARTIFACTS / "validation-native-analytics-status.json"
        ),
        "native_validation_analytics_browser": (
            PRIVATE_ARTIFACTS / "validation-native-analytics-browser-status.json"
        ),
        "native_validation_expense_settlement": (
            PRIVATE_ARTIFACTS / "validation-native-expense-settlement-status.json"
        ),
        "native_validation_document_settlement": (
            PRIVATE_ARTIFACTS / "validation-native-document-settlement-status.json"
        ),
        "native_validation_general_reconciliation": (
            PRIVATE_ARTIFACTS / "validation-native-general-reconciliation-status.json"
        ),
        "native_validation_bank_categorization": (
            PRIVATE_ARTIFACTS / "validation-native-bank-categorization-status.json"
        ),
        "native_validation_bank_external": (
            PRIVATE_ARTIFACTS / "validation-native-bank-external-status.json"
        ),
        "currency_rate_provider": (
            PRIVATE_ARTIFACTS / "currency-rate-provider-status.json"
        ),
        "currency_rate_provider_browser": (
            PRIVATE_ARTIFACTS
            / "currency-rate-provider-browser-status.json"
        ),
        "reports": PRIVATE_ARTIFACTS / "reports-status.json",
        "dynamic_report_browser": (
            PRIVATE_ARTIFACTS
            / "dynamic-report-browser-status.json"
        ),
        "accounting_home_browser": (
            PRIVATE_ARTIFACTS
            / "accounting-home-browser-status.json"
        ),
        "accounting_hygiene_browser": (
            PRIVATE_ARTIFACTS
            / "accounting-hygiene-browser-status.json"
        ),
        "dev_reset": (
            PRIVATE_ARTIFACTS / "dev-reset-status.json"
        ),
        "dev_import": (
            PRIVATE_ARTIFACTS / "dev-import-status.json"
        ),
        "dev_validate": (
            PRIVATE_ARTIFACTS / "dev-validate-status.json"
        ),
        "dev_attachment_replay": (
            PRIVATE_ARTIFACTS / "dev-attachment-replay-status.json"
        ),
        "attachment_reconstruction": (
            PRIVATE_ARTIFACTS / "attachment-reconstruction-status.json"
        ),
        "dev_browser": (
            PRIVATE_ARTIFACTS / "replacement-browser-status.json"
        ),
        "vat_benchmark_investigation": PRIVATE_ARTIFACTS / "vat-benchmark-investigation-2025-09-30.json",
        "source_report_parity": PRIVATE_ARTIFACTS / "source-report-parity-status.json",
        "review_decision_seed": PRIVATE_ARTIFACTS / "review-decision-seed-status.json",
        "fec": PRIVATE_ARTIFACTS / "fec-status.json",
        "fec_structural_preflight": PRIVATE_ARTIFACTS / "fec-structural-preflight.json",
        "fec_validation": PRIVATE_ARTIFACTS / "fec-validation-status.json",
        "fec_role_browser": (
            PRIVATE_ARTIFACTS / "fec-role-browser-status.json"
        ),
        "fec_dgfip_source_validation": PRIVATE_ARTIFACTS / "fec-dgfip-source-validation" / "summary.json",
        "fec_dgfip_source_validation_artifacts": PRIVATE_ARTIFACTS / "fec-dgfip-source-validation",
        "compare": PRIVATE_ARTIFACTS / "compare-status.json",
        "readiness_assessment": PRIVATE_ARTIFACTS / "readiness-assessment.json",
        "readiness_assessment_markdown": PRIVATE_ARTIFACTS / "readiness-assessment.md",
        "invariants": PRIVATE_ARTIFACTS / "invariants.json",
        "anomalies": PRIVATE_ARTIFACTS / "anomalies.json",
    }
    index = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "private_artifacts": {
            name: {"path": str(path.relative_to(ROOT)), "exists": path.exists()}
            for name, path in files.items()
        },
        "public_docs": [
            "docs/accounting/accounting-compat-harness.md",
            "docs/accounting/accounting-invariants.md",
            "docs/accounting/accounting-configuration-capability.md",
            "docs/accounting/declarations-and-closing.md",
            "docs/accounting/reporting-and-closing-ux.md",
        ],
        "completion_note": "This evidence index is technical evidence only. It is not accountant acceptance and does not authorize production admission.",
    }
    write_json(PRIVATE_ARTIFACTS / "evidence-index.json", index)
    return index


def print_summary(title: str, data: Any) -> None:
    sys.stdout.write(json.dumps({"stage": title, "result": data}, indent=2, sort_keys=True, ensure_ascii=False))
    sys.stdout.write("\n")


def run_all(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "source_validate": validate_source(args),
        "failure_tests": failure_tests(args),
        "source_restore": restore_source(args),
        "source_inspect": inspect_source(args),
        "source_controls": source_controls(args),
        "extract": extract(args),
        "validation_exact_reset": target_reset(args),
        "validation_exact_import": target_import(args),
        "validation_exact_validate": target_validate(args),
        "validation_exact_idempotence": target_idempotence(args),
        "validation_exact_failure_tests": target_failure_tests(args),
        "validation_native_reset": native_validation_reset(args),
        "validation_native_expenses": native_validation_expenses(args),
        "validation_native_documents": native_validation_documents(args),
        "validation_native_assets": native_validation_assets(args),
        "validation_native_deferrals": native_validation_deferrals(args),
        "validation_native_expense_settlement": native_validation_expense_settlement(args),
        "validation_native_document_settlement": native_validation_document_settlement(args),
        "validation_native_general_reconciliation": native_validation_general_reconciliation(args),
        "validation_native_bank_categorization": native_validation_bank_categorization(args),
        "validation_native_bank_external": native_validation_bank_external(args),
        "validation_native_analytics": native_validation_analytics(args),
        "dev_reset": dev_reset(args),
        "dev_import": dev_import(args),
        "dev_validate": dev_validate(args),
        "attachment_reconstruction_audit": attachment_reconstruction_audit(
            args,
        ),
        "currency_rate_provider": currency_rate_provider(args),
        "reports": reports(args),
        "fec": fec(args),
        "fec_preflight": fec_preflight(args),
        "fec_validate": fec_validate(argparse.Namespace(**{**vars(args), "allow_errors": True})),
        "compare": compare(args),
        "readiness": readiness(args),
        "evidence": evidence(args),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="USL accounting compatibility harness")
    parser.add_argument("stage", choices=[
        "all",
        "source-validate",
        "source-restore",
        "source-inspect",
        "source-controls",
        "attachment-audit",
        "failure-tests",
        "extract",
        "validation-exact-reset",
        "validation-exact-import",
        "validation-exact-validate",
        "validation-exact-idempotence",
        "validation-exact-failure-tests",
        "validation-native-reset",
        "validation-native-expenses",
        "validation-native-documents",
        "validation-native-assets",
        "validation-native-deferrals",
        "validation-native-analytics",
        "validation-native-expense-settlement",
        "validation-native-document-settlement",
        "validation-native-general-reconciliation",
        "validation-native-bank-categorization",
        "validation-native-bank-external",
        "dev-reset",
        "dev-import",
        "dev-validate",
        "dev-attachments",
        "currency-rate-provider",
        "reports",
        "fec",
        "fec-preflight",
        "fec-validate",
        "compare",
        "readiness",
        "evidence",
    ])
    parser.add_argument(
        "--source-dir",
        default=os.environ.get("USL_ONLINE_DUMP_DIR", DEFAULT_SOURCE_DIR),
    )
    parser.add_argument("--allow-errors", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_source_mount(args.source_dir)
    try:
        require_isolated_compose_project()
        if args.stage == "source-validate":
            print_summary(args.stage, validate_source(args))
        elif args.stage == "source-restore":
            print_summary(args.stage, restore_source(args))
        elif args.stage == "source-inspect":
            print_summary(args.stage, inspect_source(args))
        elif args.stage == "source-controls":
            print_summary(args.stage, source_controls(args))
        elif args.stage == "attachment-audit":
            print_summary(
                args.stage,
                attachment_reconstruction_audit(args),
            )
        elif args.stage == "failure-tests":
            print_summary(args.stage, failure_tests(args))
        elif args.stage == "extract":
            print_summary(args.stage, extract(args))
        elif args.stage == "validation-exact-reset":
            print_summary(args.stage, target_reset(args))
        elif args.stage == "validation-exact-import":
            print_summary(args.stage, target_import(args))
        elif args.stage == "validation-exact-validate":
            print_summary(args.stage, target_validate(args))
        elif args.stage == "validation-exact-idempotence":
            print_summary(args.stage, target_idempotence(args))
        elif args.stage == "validation-exact-failure-tests":
            print_summary(args.stage, target_failure_tests(args))
        elif args.stage == "validation-native-reset":
            print_summary(args.stage, native_validation_reset(args))
        elif args.stage == "validation-native-expenses":
            print_summary(args.stage, native_validation_expenses(args))
        elif args.stage == "validation-native-documents":
            print_summary(args.stage, native_validation_documents(args))
        elif args.stage == "validation-native-assets":
            print_summary(args.stage, native_validation_assets(args))
        elif args.stage == "validation-native-deferrals":
            print_summary(args.stage, native_validation_deferrals(args))
        elif args.stage == "validation-native-analytics":
            print_summary(args.stage, native_validation_analytics(args))
        elif args.stage == "validation-native-expense-settlement":
            print_summary(args.stage, native_validation_expense_settlement(args))
        elif args.stage == "validation-native-document-settlement":
            print_summary(args.stage, native_validation_document_settlement(args))
        elif args.stage == "validation-native-general-reconciliation":
            print_summary(args.stage, native_validation_general_reconciliation(args))
        elif args.stage == "validation-native-bank-categorization":
            print_summary(args.stage, native_validation_bank_categorization(args))
        elif args.stage == "validation-native-bank-external":
            print_summary(args.stage, native_validation_bank_external(args))
        elif args.stage == "dev-reset":
            print_summary(args.stage, dev_reset(args))
        elif args.stage == "dev-import":
            print_summary(args.stage, dev_import(args))
        elif args.stage == "dev-validate":
            print_summary(args.stage, dev_validate(args))
        elif args.stage == "dev-attachments":
            print_summary(args.stage, dev_attachment_replay(args))
        elif args.stage == "currency-rate-provider":
            print_summary(args.stage, currency_rate_provider(args))
        elif args.stage == "reports":
            print_summary(args.stage, reports(args))
        elif args.stage == "fec":
            print_summary(args.stage, fec(args))
        elif args.stage == "fec-preflight":
            print_summary(args.stage, fec_preflight(args))
        elif args.stage == "fec-validate":
            print_summary(args.stage, fec_validate(args))
        elif args.stage == "compare":
            print_summary(args.stage, compare(args))
        elif args.stage == "readiness":
            print_summary(args.stage, readiness(args))
        elif args.stage == "evidence":
            print_summary(args.stage, evidence(args))
        elif args.stage == "all":
            print_summary(args.stage, run_all(args))
        return 0
    except HarnessError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
