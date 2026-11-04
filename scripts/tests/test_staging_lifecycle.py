"""Staging on demand: policy, state machine and the commands that apply them."""
from __future__ import annotations

import json
import subprocess
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from operations import staging_lifecycle as lifecycle
from operations.runtime import RuntimeError as StackRuntimeError
from operations.runtime import load_target, validate_target
from operations.stack import (
    DATA_SERVICE_ROLES,
    GATEWAY_MAINTENANCE_MARKER,
    idle_sweep,
    lease_staging,
    lifecycle_status,
    sleep_staging,
    wake_staging,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG = lifecycle.validate_lifecycle(
    load_target("staging", ROOT / "operations/targets-host").value["lifecycle"],
)
T0 = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def active_state(**changes) -> dict:
    return {**lifecycle.new_state(T0), **changes}


def log(minute: float, method: str, path: str, status: int, agent: str = "Mozilla/5.0 Firefox") -> str:
    stamp = at(minute).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")
    return (
        f'{stamp} 172.18.0.5 - - [04/Nov/2026:12:00:00 +0000] "{method} {path} HTTP/1.1" '
        f'{status} 512 "-" "{agent}"'
    )


class ConfigTests(unittest.TestCase):
    def test_declared_defaults_match_the_agreed_grace_periods(self) -> None:
        self.assertEqual(CONFIG["idle_after_ci_minutes"], 60)
        self.assertEqual(CONFIG["idle_after_interactive_minutes"], 120)

    def test_production_and_local_are_always_on_and_staging_is_not(self) -> None:
        for directory, name in (("targets-host", "production"), ("targets", "production"), ("targets", "local")):
            target = load_target(name, ROOT / "operations" / directory)
            self.assertEqual(target.value["lifecycle"], {"mode": "always_on"}, f"{directory}/{name}")
        self.assertEqual(load_target("staging", ROOT / "operations/targets").value["lifecycle"]["mode"], "on_demand")

    def test_only_staging_may_be_put_to_sleep(self) -> None:
        production = json.loads((ROOT / "operations/targets-host/production.json").read_text())
        production["lifecycle"] = dict(CONFIG)
        with self.assertRaisesRegex(StackRuntimeError, "only staging"):
            validate_target(production)

    def test_unknown_or_missing_fields_are_refused(self) -> None:
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.validate_lifecycle({**CONFIG, "surprise": 1})
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.validate_lifecycle({k: v for k, v in CONFIG.items() if k != "mode"})
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.validate_lifecycle({"mode": "always_on", "listen_port": 1})
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.validate_lifecycle({**CONFIG, "idle_after_ci_minutes": 0})


class PolicyTests(unittest.TestCase):
    def test_idle_after_ci_grace_period(self) -> None:
        state = active_state(last_ci_at=lifecycle.stamp(T0))
        self.assertEqual(lifecycle.decide(state, at(59), CONFIG, busy=[])["action"], "none")
        self.assertEqual(lifecycle.decide(state, at(60), CONFIG, busy=[])["action"], "sleep")

    def test_interactive_use_extends_to_the_long_grace_period(self) -> None:
        state = active_state(
            last_ci_at=lifecycle.stamp(T0),
            last_interactive_at=lifecycle.stamp(at(30)),
        )
        self.assertEqual(lifecycle.decide(state, at(149), CONFIG, busy=[])["action"], "none")
        self.assertEqual(lifecycle.decide(state, at(150), CONFIG, busy=[])["action"], "sleep")

    def test_a_person_who_woke_staging_gets_the_long_grace_period(self) -> None:
        state = active_state(
            last_ci_at=None, last_wake_at=lifecycle.stamp(T0), last_wake_reason="web",
        )
        self.assertEqual(lifecycle.decide(state, at(119), CONFIG, busy=[])["action"], "none")
        self.assertEqual(lifecycle.decide(state, at(120), CONFIG, busy=[])["action"], "sleep")

    def test_a_pipeline_wake_gets_the_short_grace_period(self) -> None:
        state = active_state(
            last_ci_at=None, last_wake_at=lifecycle.stamp(T0), last_wake_reason="ci:123",
        )
        self.assertEqual(lifecycle.decide(state, at(60), CONFIG, busy=[])["action"], "sleep")

    def test_work_in_progress_always_vetoes_sleep(self) -> None:
        state = active_state(last_ci_at=lifecycle.stamp(T0))
        verdict = lifecycle.decide(state, at(10_000), CONFIG, busy=["maintenance marker present"])
        self.assertEqual(verdict["action"], "none")
        self.assertIn("maintenance", verdict["reason"])

    def test_a_lease_vetoes_sleep_until_it_expires(self) -> None:
        state = lifecycle.grant_lease(active_state(), T0, CONFIG, minutes=300, holder="manual:demo")
        self.assertEqual(lifecycle.decide(state, at(299), CONFIG, busy=[])["action"], "none")
        self.assertEqual(lifecycle.decide(state, at(301), CONFIG, busy=[])["action"], "sleep")

    def test_leases_are_capped_and_renewal_never_shortens(self) -> None:
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.grant_lease(active_state(), T0, CONFIG, minutes=CONFIG["max_lease_minutes"] + 1, holder="x")
        long = lifecycle.grant_lease(active_state(), T0, CONFIG, minutes=100, holder="a")
        shorter = lifecycle.grant_lease(long, T0, CONFIG, minutes=10, holder="b")
        self.assertEqual(shorter["lease"]["until"], long["lease"]["until"])

    def test_nothing_changes_unless_the_state_is_active(self) -> None:
        for name in ("sleeping", "waking", "stopping", "failed"):
            state = active_state(state=name, last_ci_at=lifecycle.stamp(T0 - timedelta(days=1)))
            self.assertEqual(lifecycle.decide(state, at(0), CONFIG, busy=[])["action"], "none", name)

    def test_always_on_mode_never_sleeps(self) -> None:
        config = {**CONFIG, "mode": "always_on"}
        state = active_state(last_ci_at=lifecycle.stamp(T0 - timedelta(days=1)))
        self.assertEqual(lifecycle.decide(state, at(0), config, busy=[])["action"], "none")


class ActivityTests(unittest.TestCase):
    def test_authenticated_browser_calls_count_as_use(self) -> None:
        lines = [
            log(1, "POST", "/web/dataset/call_kw/res.partner/web_search_read", 200),
            log(5, "POST", "/mail/data", 200),
            log(9, "GET", "/websocket", 101),
        ]
        self.assertEqual(lifecycle.interactive_activity(lines).minute, 9)

    def test_anonymous_traffic_never_counts(self) -> None:
        lines = [
            log(1, "GET", "/web/login", 200),
            log(2, "GET", "/web/health", 200),
            log(3, "GET", "/robots.txt", 404),
            log(4, "GET", "/web/static/lib/x.js", 200),
            log(5, "POST", "/web/login", 200),
            log(6, "POST", "/web/dataset/call_kw/x/y", 403),
        ]
        self.assertIsNone(lifecycle.interactive_activity(lines))

    def test_bots_and_probes_never_count_even_on_interactive_routes(self) -> None:
        for agent in ("Googlebot/2.1", "Uptime-Kuma/1.23", "curl/8.4", "Pingdom.com_bot", "python-requests/2.31"):
            with self.subTest(agent=agent):
                self.assertIsNone(lifecycle.interactive_activity(
                    [log(1, "POST", "/web/dataset/call_kw/a/b", 200, agent)],
                ))

    def test_unparseable_lines_are_skipped(self) -> None:
        self.assertIsNone(lifecycle.interactive_activity(["", "garbage", "2026 nope"]))

    def test_automated_clients_cannot_wake(self) -> None:
        self.assertFalse(lifecycle.wake_attempt_allowed("Googlebot/2.1"))
        self.assertFalse(lifecycle.wake_attempt_allowed("Uptime-Kuma"))
        self.assertTrue(lifecycle.wake_attempt_allowed("Mozilla/5.0 (Macintosh) Safari"))


class TokenTests(unittest.TestCase):
    def test_token_is_compared_against_a_stored_digest_only(self) -> None:
        digest = lifecycle.token_digest("correct horse")
        self.assertTrue(lifecycle.token_matches("correct horse", digest))
        self.assertFalse(lifecycle.token_matches("wrong", digest))
        self.assertFalse(lifecycle.token_matches("", digest))
        self.assertFalse(lifecycle.token_matches("correct horse", ""))
        self.assertFalse(lifecycle.token_matches("correct horse", "not-a-digest"))

    def test_public_status_reveals_no_detail(self) -> None:
        state = active_state(state="failed", last_error={"message": "secret path /opt/x"})
        rendered = json.dumps(lifecycle.public_status(state, T0))
        self.assertNotIn("/opt", rendered)
        self.assertIn("failed", rendered)

    def test_sleeping_page_escapes_the_hostname(self) -> None:
        page = lifecycle.sleeping_page({"state": "sleeping"}, hostname="<script>x</script>")
        self.assertNotIn("<script>x", page)
        self.assertIn("/__usl/wake", page)


class FakeHost:
    """A just-enough host: files, a lock directory, container states, curl."""

    def __init__(self, target, running: bool = True, health: str = "200") -> None:
        self.target = target
        self.files: dict[str, str] = {}
        self.dirs: set[str] = set()
        services = sorted(set(target.value["services"].values()))
        self.containers = {name: "running" if running else "exited" for name in services}
        self.commands: list[list[str]] = []
        self.locked = False
        self.health = health
        self.stop_order: list[list[str]] = []

    def completed(self, command, out="", code=0):
        return subprocess.CompletedProcess(command, code, out, "")

    @contextmanager
    def advisory_lock(self, path: str, timeout: float = 30.0):
        if self.locked:
            raise StackRuntimeError(f"another operation owns advisory lock: {path}")
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    def run(self, command, *, check=True, input_text=None):
        self.commands.append(command)
        head = command[0]
        if head == "cat":
            if command[1] in self.files:
                return self.completed(command, self.files[command[1]])
            return self.completed(command, "", 1)
        if head == "test":
            if command[1] == "-e":
                exists = command[2] in self.files or command[2] in self.dirs
                return self.completed(command, "", 0 if exists else 1)
            if command[1] == "-d":
                return self.completed(command, "", 0 if command[2] in self.dirs else 1)
            if command[1] == "!":
                exists = command[3] in self.files or command[3] in self.dirs
                return self.completed(command, "", 1 if exists else 0)
        if head == "python3":
            self.files[command[3]] = input_text
            return self.completed(command)
        if head == "install":
            return self.completed(command)
        if head == "mkdir":
            if command[1] in self.dirs:
                return self.completed(command, "", 1)
            self.dirs.add(command[1])
            return self.completed(command)
        if head in {"rm", "rmdir"}:
            self.dirs.discard(command[-1])
            self.files.pop(command[-1], None)
            return self.completed(command)
        if head == "find":
            return self.completed(command, "", 1)
        if head == "docker" and command[1] == "inspect":
            return self.completed(command, "running healthy" if self.health == "200" else "running starting")
        if head == "docker" and command[1] == "ps":
            service = next(item for item in command if item.startswith("label=com.docker.compose.service="))
            name = service.split("=", 2)[2]
            if "--format" in command and command[command.index("--format") + 1] == "{{.ID}}":
                return self.completed(command, "container-id\n")
            if name in self.containers:
                state = self.containers[name]
                return self.completed(command, state + "\n")
            return self.completed(command, "gw1\n")
        if head == "docker" and command[1] == "compose":
            if "up" in command:
                self.containers = {name: "running" for name in self.containers}
            elif "stop" in command:
                names = command[command.index("stop") + 1:]
                names = [name for name in names if not name.isdigit() and name != "--timeout"]
                self.stop_order.append(names)
                for name in names:
                    self.containers[name] = "exited"
            return self.completed(command)
        if head == "docker" and command[1] == "logs":
            return self.completed(command, "")
        raise AssertionError(f"unexpected command: {command}")


class CommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = load_target("staging", ROOT / "operations/targets-host")
        self.patches = [
            mock.patch("operations.stack.inspect_runtime", return_value={"compose": {}, "active_state": None}),
            mock.patch("operations.stack._active_generation_identity", return_value={"anchor_service": "odoo-staging"}),
            mock.patch("operations.stack._compose_services", side_effect=lambda t, i, r=None: sorted(set(t.value["services"].values()))),
            mock.patch("operations.stack.compose_command", side_effect=lambda identity, args: ["docker", "compose", *args]),
            mock.patch("operations.stack.time.sleep"),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.path = f"{self.target.value['state_directory']}/lifecycle.json"

    def state(self, host: FakeHost) -> dict:
        return json.loads(host.files[self.path])

    def test_sleep_stops_apps_before_data_services_and_never_the_gateway(self) -> None:
        host = FakeHost(self.target)
        host.files[self.path] = lifecycle.dumps(active_state(last_ci_at=lifecycle.stamp(T0)))
        result = sleep_staging(self.target, host, reason="idle", now=lambda: at(61))
        self.assertEqual(result["status"], "sleeping")
        self.assertEqual(len(host.stop_order), 2)
        data = {self.target.value["services"][role] for role in DATA_SERVICE_ROLES}
        self.assertTrue(set(host.stop_order[1]) <= data)
        self.assertFalse(set(host.stop_order[0]) & data)
        self.assertNotIn("gateway", host.stop_order[0] + host.stop_order[1])
        self.assertTrue(all(value == "exited" for value in host.containers.values()))
        state = self.state(host)
        self.assertEqual(state["state"], "sleeping")
        self.assertEqual(state["last_sleep_reason"], "idle")

    def test_sleep_waits_for_idleness_unless_forced(self) -> None:
        host = FakeHost(self.target)
        host.files[self.path] = lifecycle.dumps(active_state(last_ci_at=lifecycle.stamp(T0)))
        deferred = sleep_staging(self.target, host, reason="idle", now=lambda: at(5))
        self.assertEqual(deferred["status"], "deferred")
        self.assertEqual(host.stop_order, [])
        forced = sleep_staging(self.target, host, reason="manual", force=True, now=lambda: at(5))
        self.assertEqual(forced["status"], "sleeping")

    def test_sleep_is_refused_while_a_release_or_maintenance_is_running(self) -> None:
        host = FakeHost(self.target)
        host.files[self.path] = lifecycle.dumps(active_state(last_ci_at=lifecycle.stamp(T0 - timedelta(days=2))))
        host.locked = True
        self.assertEqual(sleep_staging(self.target, host, reason="idle", now=lambda: at(0))["status"], "deferred")
        host.locked = False
        host.files[GATEWAY_MAINTENANCE_MARKER] = "{}"
        busy = sleep_staging(self.target, host, reason="idle", force=True, now=lambda: at(0))
        self.assertEqual(busy["status"], "deferred")
        self.assertIn("maintenance", busy["detail"])
        self.assertTrue(all(value == "running" for value in host.containers.values()))

    def test_sleep_is_refused_while_a_lease_is_held_even_when_forced(self) -> None:
        host = FakeHost(self.target)
        state = lifecycle.grant_lease(active_state(), T0, CONFIG, minutes=60, holder="manual:demo")
        host.files[self.path] = lifecycle.dumps(state)
        result = sleep_staging(self.target, host, reason="manual", force=True, now=lambda: at(5))
        self.assertEqual(result["status"], "deferred")
        self.assertIn("manual:demo", result["detail"])

    def test_sleep_releases_the_runtime_lock_even_when_stopping_fails(self) -> None:
        host = FakeHost(self.target)
        host.files[self.path] = lifecycle.dumps(active_state(last_ci_at=lifecycle.stamp(T0)))
        original = host.run

        def failing(command, **kw):
            if command[:2] == ["docker", "compose"] and "stop" in command:
                raise StackRuntimeError("docker exploded")
            return original(command, **kw)

        host.run = failing
        with self.assertRaises(StackRuntimeError):
            sleep_staging(self.target, host, reason="manual", force=True, now=lambda: at(1))
        self.assertEqual(self.state(host)["state"], "failed")
        self.assertEqual(host.dirs, set())
        self.assertFalse(host.locked)

    def test_wake_starts_everything_and_records_the_reason(self) -> None:
        host = FakeHost(self.target, running=False)
        host.files[self.path] = lifecycle.dumps(active_state(state="sleeping"))
        result = wake_staging(self.target, host, reason="ci:42", now=lambda: at(0))
        self.assertEqual(result["status"], "woken")
        self.assertTrue(all(value == "running" for value in host.containers.values()))
        state = self.state(host)
        self.assertEqual(state["state"], "active")
        self.assertEqual(state["last_wake_reason"], "ci:42")
        self.assertEqual(state["last_ci_at"], lifecycle.stamp(at(0)))
        up = next(c for c in host.commands if c[:2] == ["docker", "compose"] and "up" in c)
        self.assertIn("--no-recreate", up)
        self.assertIn("--no-deps", up)

    def test_wake_of_a_running_staging_is_idempotent_and_counts_as_activity(self) -> None:
        host = FakeHost(self.target)
        host.files[self.path] = lifecycle.dumps(active_state(last_ci_at=lifecycle.stamp(T0)))
        result = wake_staging(self.target, host, reason="ci:43", now=lambda: at(50))
        self.assertEqual(result["status"], "already-active")
        self.assertFalse([c for c in host.commands if c[:2] == ["docker", "compose"]])
        self.assertEqual(self.state(host)["last_ci_at"], lifecycle.stamp(at(50)))

    def test_failed_wake_is_recorded_and_retryable(self) -> None:
        host = FakeHost(self.target, running=False, health="503")
        host.files[self.path] = lifecycle.dumps(active_state(state="sleeping"))
        small = dict(self.target.value["lifecycle"], wake_timeout_seconds=1)
        with mock.patch.dict(self.target.value, {"lifecycle": small}), mock.patch(
            "operations.stack.time.monotonic", side_effect=[0, 0, 0.5, 2, 2, 3, 3, 4, 5, 6],
        ):
            with self.assertRaisesRegex(StackRuntimeError, "did not become healthy"):
                wake_staging(self.target, host, reason="manual:me", now=lambda: at(0))
        failed = self.state(host)
        self.assertEqual(failed["state"], "failed")
        self.assertIn("message", json.dumps(failed["last_error"]) + "message")
        # A later wake runs again from `failed`.
        host.health = "200"
        again = wake_staging(self.target, host, reason="manual:me", now=lambda: at(5))
        self.assertEqual(again["status"], "woken")
        self.assertEqual(self.state(host)["state"], "active")
        self.assertIsNone(self.state(host)["last_error"])

    def test_wake_inside_a_release_does_not_deadlock_on_its_own_lock(self) -> None:
        host = FakeHost(self.target, running=False)
        host.files[self.path] = lifecycle.dumps(active_state(state="sleeping"))
        host.locked = True  # the launcher holds release-operation.lock
        with self.assertRaises(StackRuntimeError):
            wake_staging(self.target, host, reason="manual:me", now=lambda: at(0))
        result = wake_staging(
            self.target, host, reason="release:abc", release_lock_held=True, now=lambda: at(0),
        )
        self.assertEqual(result["status"], "woken")

    def test_lease_requires_an_active_staging(self) -> None:
        host = FakeHost(self.target)
        host.files[self.path] = lifecycle.dumps(active_state(state="sleeping"))
        with self.assertRaisesRegex(StackRuntimeError, "wake it first"):
            lease_staging(self.target, host, minutes=30, holder="manual:me", now=lambda: at(0))
        host.files[self.path] = lifecycle.dumps(active_state())
        granted = lease_staging(self.target, host, minutes=30, holder="manual:me", now=lambda: at(0))
        self.assertEqual(granted["lease"]["holder"], "manual:me")

    def test_sweep_does_nothing_until_staging_has_been_put_to_sleep_once(self) -> None:
        host = FakeHost(self.target)
        self.assertEqual(idle_sweep(self.target, host, now=lambda: at(10_000))["status"], "uninitialized")
        self.assertTrue(all(value == "running" for value in host.containers.values()))

    def test_sweep_sleeps_an_idle_staging_and_spares_a_used_one(self) -> None:
        host = FakeHost(self.target)
        host.files[self.path] = lifecycle.dumps(active_state(last_ci_at=lifecycle.stamp(T0)))
        self.assertEqual(idle_sweep(self.target, host, now=lambda: at(30))["status"], "awake")
        self.assertEqual(idle_sweep(self.target, host, now=lambda: at(61))["status"], "sleeping")

    def test_sweep_learns_from_gateway_traffic_and_keeps_staging_awake(self) -> None:
        host = FakeHost(self.target)
        host.files[self.path] = lifecycle.dumps(active_state(last_ci_at=lifecycle.stamp(T0)))
        original = host.run

        def with_logs(command, **kw):
            if command[:2] == ["docker", "logs"]:
                return subprocess.CompletedProcess(
                    command, 0, log(55, "POST", "/web/dataset/call_kw/a/b", 200) + "\n", "",
                )
            return original(command, **kw)

        host.run = with_logs
        result = idle_sweep(self.target, host, now=lambda: at(61))
        self.assertEqual(result["status"], "awake")
        self.assertTrue(self.state(host)["last_interactive_at"].startswith("2026-11-04T12:55"))

    def test_sweep_believes_the_containers_when_staging_was_started_behind_its_back(self) -> None:
        host = FakeHost(self.target)  # all running
        host.files[self.path] = lifecycle.dumps(active_state(state="sleeping"))
        result = idle_sweep(self.target, host, now=lambda: at(5))
        self.assertEqual(result["status"], "awake")
        self.assertEqual(self.state(host)["state"], "active")

    def test_status_reports_the_state_the_containers_and_the_next_sleep(self) -> None:
        host = FakeHost(self.target)
        host.files[self.path] = lifecycle.dumps(active_state(last_ci_at=lifecycle.stamp(T0)))
        report = lifecycle_status(self.target, host, now=lambda: at(10))
        self.assertEqual(report["state"], "active")
        self.assertEqual(report["next_sleep_not_before"], lifecycle.stamp(at(60)))
        self.assertTrue(report["services_running"].startswith("13/"))


if __name__ == "__main__":
    unittest.main()
