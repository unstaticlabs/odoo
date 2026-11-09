"""The ``scripts/docs`` command line.

One entry point for the documentation tooling, so a contributor and the CI
jobs call the same verbs: ``check`` on every pull request, ``generate`` and
``reference`` in the qualification database job, ``evidence`` where the
release is assembled, ``mcp`` beside the MCP release verification. Each
subcommand is a thin dispatch to its module; the modules carry the logic.
"""

from __future__ import annotations

import argparse
import sys

from operations.docs import ROOT, check as docs_check, evidence, generate, mcp, reference, release_evidence


def _print_findings(findings, label):
    for path, problem in findings:
        print(f"{path}: {problem}")
    if findings:
        print(f"{label}: FAIL ({len(findings)} finding(s))")
        return 1
    print(f"{label}: PASS")
    return 0


def _print_written(written, current):
    for path in written:
        print(f"wrote {path.relative_to(ROOT).as_posix()}" if path.is_relative_to(ROOT) else f"wrote {path}")
    if not written:
        print(current)
    return 0


def _check(arguments):
    findings = docs_check.check()
    for path, problem in findings:
        print(f"{path}: {problem}")
    if arguments.coverage:
        print("Test-backed pages by type:")
        for page_type, (backed, total) in sorted(docs_check.coverage().items()):
            print(f"  {page_type}: {backed}/{total}")
    if findings:
        print(f"Documentation check: FAIL ({len(findings)} finding(s))")
        return 1
    print("Documentation check: PASS")
    return 0


def _index(arguments):
    print(docs_check.render_llms_txt(docs_check.load_pages(), docs_check.load_decisions()), end="")
    return 0


def _generate(arguments):
    if not generate.pillow_available():
        print("note: Pillow is not installed; screenshots are compared by bytes, not pixels")
    if arguments.action == "render":
        return _print_written(generate.render_all(arguments.records), "Generated documentation: current")
    return _print_findings(generate.check_all(arguments.records), "Generated documentation check")


def _reference(arguments):
    if arguments.action == "render":
        return _print_written(reference.render_all(arguments.reference), "Generated reference: current")
    return _print_findings(reference.check_all(arguments.reference), "Generated reference check")


def _mcp(arguments):
    try:
        if arguments.action == "render":
            return _print_written(mcp.render(arguments.repository), "MCP reference: current")
        return _print_findings(mcp.check(arguments.repository), "MCP reference check")
    except mcp.DocsMcpError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


def _evidence(arguments):
    if arguments.action == "create":
        payload = evidence.build(
            arguments.records,
            repository=arguments.repository,
            qualified_commit=arguments.qualified_commit,
            workflow_run_id=arguments.workflow_run_id,
            run_url=arguments.run_url,
            committed_docs=arguments.committed_docs,
            qualified_tree=arguments.qualified_tree,
        )
        evidence.validate(payload)
        print(evidence.dump(payload, arguments.output))
        print(f"{len(payload['journeys'])} journey(s) recorded")
        return 0
    if arguments.action == "recovery":
        payload = evidence.recovery(repository=arguments.repository, commit=arguments.commit)
        print(evidence.dump(payload, arguments.output))
        return 0
    if arguments.action == "resolve":
        try:
            payload = release_evidence.resolve(
                repository=arguments.repository,
                commit=arguments.commit,
                tree=arguments.tree,
                workflow_run_id=arguments.workflow_run_id,
            )
        except release_evidence.ReleaseEvidenceError as error:
            print(f"note: {error}; the release carries no docs evidence", file=sys.stderr)
            return 0
        if payload is None:
            print("No docs evidence qualifies this tree; the guide will say the build is unverified.")
            return 0
        print(evidence.dump(payload, arguments.output))
        print(f"Docs evidence adopted from run {payload['workflow_run_id']} ({len(payload['journeys'])} journey(s))")
        return 0
    evidence.validate(
        evidence.load(arguments.path),
        qualified_commit=arguments.qualified_commit,
        workflow_run_id=arguments.workflow_run_id,
        qualified_tree=arguments.qualified_tree,
    )
    print("Docs evidence: valid")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="scripts/docs",
        description="Check, render and prove the repository documentation.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    check = commands.add_parser(
        "check",
        help="prove every page is well formed and its links resolve",
        description="Every page under docs/users carries front matter that matches its directory, "
        "links only to pages and images that exist, and names sources that exist. "
        "Decision records are numbered without gaps.",
    )
    check.add_argument("--coverage", action="store_true", help="also print how many pages of each type a journey proves")
    check.set_defaults(run=_check)

    index = commands.add_parser(
        "index",
        help="print the llms.txt map of the guide to stdout",
        description="Print an llms.txt over the pages with repository-relative links. Nothing commits or checks it.",
    )
    index.set_defaults(run=_index)

    gen = commands.add_parser(
        "generate",
        help="render journey records into pages and screenshots, or prove they are current",
        description="Records come from JourneyCase tests (custom-addons/usl_docs/tests/journey.py). "
        "Screenshots are compared by pixels when Pillow is installed and by bytes otherwise.",
    )
    gen.add_argument("action", choices=("render", "check"))
    gen.add_argument("--records", default=str(ROOT / "artifacts" / "usl-docs"), help="journey records directory")
    gen.set_defaults(run=_generate)

    ref = commands.add_parser(
        "reference",
        help="render the generated reference pages from the registry, or prove they are current",
        description="The reference file is the usl-docs-reference/v1 document scripts/odoo/docs_reference.py "
        "prints from an Odoo shell on the product database.",
    )
    ref.add_argument("action", choices=("render", "check"))
    ref.add_argument("--reference", default=str(ROOT / "artifacts" / "usl-docs" / "reference.json"), help="usl-docs-reference/v1 file")
    ref.set_defaults(run=_reference)

    mcp_parser = commands.add_parser(
        "mcp",
        help="render the MCP tools page from the pinned odoo-mcp catalogue, or prove it is current",
        description="The page is the docs/tool-catalogue.md of the odoo-mcp commit pinned in "
        "deploy/odoo-mcp/release.json, read from a local checkout or from GitHub.",
    )
    mcp_parser.add_argument("action", choices=("render", "check"))
    mcp_parser.add_argument("--repository", help="local odoo-mcp checkout holding the pinned commit (default: GitHub through gh)")
    mcp_parser.set_defaults(run=_mcp)

    ev = commands.add_parser(
        "evidence",
        help="create or verify the docs evidence of a qualification run",
        description="usl-docs-evidence/v1 says which journeys a qualified commit ran and what they captured.",
    )
    actions = ev.add_subparsers(dest="action", required=True, metavar="action")
    create = actions.add_parser("create", help="assemble the evidence from a run's journey records")
    create.add_argument("--records", required=True)
    create.add_argument("--repository", required=True)
    create.add_argument("--qualified-commit", required=True)
    create.add_argument("--workflow-run-id", type=int, required=True)
    create.add_argument("--run-url", required=True)
    create.add_argument("--qualified-tree", help="git tree the run tested; lets a production release recognise its evidence")
    create.add_argument("--committed-docs", default="docs/users")
    create.add_argument("--output", required=True)
    resolve = actions.add_parser(
        "resolve",
        help="find the evidence a release can carry: its own run, or the run that qualified the merged pull request's tree",
    )
    resolve.add_argument("--repository", required=True)
    resolve.add_argument("--commit", required=True)
    resolve.add_argument("--tree", required=True)
    resolve.add_argument("--workflow-run-id", type=int, required=True)
    resolve.add_argument("--output", required=True)
    recovery = actions.add_parser("recovery", help="evidence for a recovery release; it proves nothing")
    recovery.add_argument("--repository", required=True)
    recovery.add_argument("--commit", required=True)
    recovery.add_argument("--output", required=True)
    verify = actions.add_parser("verify", help="validate an evidence file, optionally against a commit and run")
    verify.add_argument("path")
    verify.add_argument("--qualified-commit")
    verify.add_argument("--qualified-tree")
    verify.add_argument("--workflow-run-id", type=int)
    ev.set_defaults(run=_evidence)
    return parser


def main(argv=None):
    arguments = build_parser().parse_args(argv)
    return arguments.run(arguments)
