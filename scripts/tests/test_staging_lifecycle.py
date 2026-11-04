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
    LIFECYCLE_OPERATION_LOCK,
    RELEASE_OPERATION_LOCK,
    idle_sweep,
    lease_staging,
    lifecycle_server,
    lifecycle_status,
    sleep_staging,
    touch_staging,
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
        for agent in ("", "python-httpx/0.27", "axios/1.6", "node-fetch/3", "okhttp/4"):
            self.assertFalse(lifecycle.wake_attempt_allowed(agent), agent)
        self.assertFalse(lifecycle.wake_attempt_allowed("Googlebot/2.1"))
        self.assertFalse(lifecycle.wake_attempt_allowed("Uptime-Kuma"))
        self.assertTrue(lifecycle.wake_attempt_allowed("Mozilla/5.0 (Macintosh) Safari"))


class PageTests(unittest.TestCase):
    def test_public_status_reveals_no_detail(self) -> None:
        state = active_state(state="failed", last_error={"message": "secret path /opt/x"})
        rendered = json.dumps(lifecycle.public_status(state, T0))
        self.assertNotIn("/opt", rendered)
        self.assertIn("failed", rendered)

    def test_sleeping_page_escapes_everything_it_shows(self) -> None:
        page = lifecycle.sleeping_page(
            {"state": "sleeping"}, hostname="<script>x</script>", person="<b>a@b</b>", notice="<i>n</i>",
        )
        for raw in ("<script>x", "<b>a@b</b>", "<i>n</i>"):
            self.assertNotIn(raw, page)
        self.assertIn("/__usl/wake", page)

    def test_the_page_offers_sign_in_to_visitors_and_wake_to_signed_in_people(self) -> None:
        anonymous = lifecycle.sleeping_page({"state": "sleeping"}, hostname="h")
        self.assertIn("/__usl/login", anonymous)
        self.assertNotIn('action="/__usl/wake"', anonymous)
        self.assertIn('action="/__usl/wake"', lifecycle.sleeping_page({"state": "sleeping"}, hostname="h", person="a@b"))


class FakeHost:
    """A just-enough host: files, a lock directory, container states, curl."""

    def __init__(self, target, running: bool = True, health: str = "200") -> None:
        self.target = target
        self.files: dict[str, str] = {}
        self.dirs: set[str] = set()
        services = sorted(set(target.value["services"].values()))
        self.containers = {name: "running" if running else "exited" for name in services}
        self.commands: list[list[str]] = []
        self.locks: set[str] = set()
        self.busy_briefly: set[str] = set()
        self.health = health
        self.stop_order: list[list[str]] = []

    def completed(self, command, out="", code=0):
        return subprocess.CompletedProcess(command, code, out, "")

    @contextmanager
    def advisory_lock(self, path: str, timeout: float = 30.0, wait: float = 0.0):
        if path in self.locks and wait > 0 and path in self.busy_briefly:
            self.locks.discard(path)  # the other holder finished within the wait
            self.busy_briefly.discard(path)
        if path in self.locks:
            raise StackRuntimeError(f"another operation owns advisory lock: {path}")
        self.locks.add(path)
        try:
            yield
        finally:
            self.locks.discard(path)

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
        host.locks.add(RELEASE_OPERATION_LOCK)
        self.assertEqual(sleep_staging(self.target, host, reason="idle", now=lambda: at(0))["status"], "deferred")
        host.locks.clear()
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
        self.assertEqual(host.locks, set())

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
        self.assertIn("did not become healthy", failed["last_error"]["message"])
        # A later wake runs again from `failed`.
        host.health = "200"
        again = wake_staging(self.target, host, reason="manual:me", now=lambda: at(5))
        self.assertEqual(again["status"], "woken")
        self.assertEqual(self.state(host)["state"], "active")
        self.assertIsNone(self.state(host)["last_error"])

    def test_a_held_runtime_operation_defers_sleep_and_sweep_without_stopping_anything(self) -> None:
        host = FakeHost(self.target)
        host.files[self.path] = lifecycle.dumps(active_state(last_ci_at=lifecycle.stamp(T0 - timedelta(days=2))))
        host.dirs.add(f"{self.target.value['state_directory']}/operation.lock")
        self.assertEqual(sleep_staging(self.target, host, reason="idle", now=lambda: at(0))["status"], "deferred")
        self.assertEqual(idle_sweep(self.target, host, now=lambda: at(0))["status"], "deferred")
        self.assertEqual(host.stop_order, [])

    def test_wake_starts_nothing_while_a_release_left_work_unfinished(self) -> None:
        host = FakeHost(self.target, running=False)
        host.files[self.path] = lifecycle.dumps(active_state(state="sleeping"))
        host.files[GATEWAY_MAINTENANCE_MARKER] = "{}"
        result = wake_staging(self.target, host, reason="manual:me", now=lambda: at(0))
        self.assertEqual(result["status"], "deferred")
        self.assertIn("recover the release first", result["detail"])
        self.assertFalse([c for c in host.commands if c[:2] == ["docker", "compose"]])
        # The launcher is about to resume that very work, so it may wake.
        # Owning the release lock is not permission to resume: production and
        # MCP releases hold it too, and must not start a cohort left stopped.
        held = wake_staging(self.target, host, reason="release:x", release_lock_held=True, now=lambda: at(0))
        self.assertEqual(held["status"], "deferred")
        result = wake_staging(
            self.target, host, reason="release:x", release_lock_held=True, may_resume=True, now=lambda: at(0),
        )
        self.assertEqual(result["status"], "woken")

    def test_lifecycle_operations_announce_themselves_to_the_launcher(self) -> None:
        host = FakeHost(self.target)
        host.files[self.path] = lifecycle.dumps(active_state(last_ci_at=lifecycle.stamp(T0 - timedelta(days=2))))
        held_while_running: list[set[str]] = []
        held_while_running_up: list[set[str]] = []
        original = host.run

        def spy(command, **kw):
            if command[:2] == ["docker", "compose"] and "stop" in command:
                held_while_running.append(set(host.locks))
            if command[:2] == ["docker", "compose"] and "up" in command:
                held_while_running_up.append(set(host.locks))
            return original(command, **kw)

        host.run = spy
        sleep_staging(self.target, host, reason="idle", now=lambda: at(0))
        self.assertTrue(held_while_running)
        self.assertGreaterEqual(held_while_running[0], {LIFECYCLE_OPERATION_LOCK, RELEASE_OPERATION_LOCK})
        # A release that already owns the lock must not take the announcement
        # while it works: the launcher would then probe it as a lifecycle operation.
        held_while_running.clear()
        host.containers = {name: "exited" for name in host.containers}
        host.files[self.path] = lifecycle.dumps(active_state(state="sleeping"))
        host.locks.add(RELEASE_OPERATION_LOCK)
        woken = wake_staging(
            self.target, host, reason="release:x", release_lock_held=True, may_resume=True, now=lambda: at(1),
        )
        self.assertEqual(woken["status"], "woken")
        self.assertTrue(held_while_running_up)
        self.assertNotIn(LIFECYCLE_OPERATION_LOCK, held_while_running_up[0])

    def test_sleep_defers_while_another_lifecycle_operation_runs(self) -> None:
        host = FakeHost(self.target)
        host.files[self.path] = lifecycle.dumps(active_state(last_ci_at=lifecycle.stamp(T0 - timedelta(days=2))))
        host.locks.add(LIFECYCLE_OPERATION_LOCK)
        self.assertEqual(sleep_staging(self.target, host, reason="idle", now=lambda: at(0))["status"], "deferred")
        self.assertEqual(host.stop_order, [])

    def test_touch_records_activity_and_never_starts_a_container(self) -> None:
        host = FakeHost(self.target, running=False)
        host.files[self.path] = lifecycle.dumps(active_state(last_ci_at=lifecycle.stamp(T0)))
        result = touch_staging(self.target, host, reason="release:done", now=lambda: at(50))
        self.assertEqual(result["status"], "touched")
        self.assertEqual(self.state(host)["last_ci_at"], lifecycle.stamp(at(50)))
        self.assertFalse([c for c in host.commands if c[:2] == ["docker", "compose"]])
        host.files[self.path] = lifecycle.dumps(active_state(state="failed"))
        self.assertEqual(touch_staging(self.target, host, reason="release:done", now=lambda: at(60))["status"], "ignored")
        self.assertEqual(self.state(host)["state"], "failed")

    def test_a_lease_cannot_resurrect_a_staging_that_is_going_to_sleep(self) -> None:
        host = FakeHost(self.target)
        host.files[self.path] = lifecycle.dumps(active_state(state="stopping"))
        with self.assertRaisesRegex(StackRuntimeError, "stopping"):
            lease_staging(self.target, host, minutes=30, holder="manual:me", now=lambda: at(0))
        self.assertEqual(self.state(host)["state"], "stopping")

    def test_a_lease_works_before_staging_ever_slept(self) -> None:
        host = FakeHost(self.target)
        granted = lease_staging(self.target, host, minutes=30, holder="manual:me", now=lambda: at(0))
        self.assertEqual(granted["status"], "leased")

    def test_writers_never_overwrite_each_other_with_a_stale_copy(self) -> None:
        # The sweep read the state; the launcher then touched it; the sweep's own
        # write must start from the touched state, not from what it read.
        host = FakeHost(self.target)
        host.files[self.path] = lifecycle.dumps(active_state(last_ci_at=lifecycle.stamp(T0)))
        original = host.run

        def touching_during_log_read(command, **kw):
            if command[:2] == ["docker", "logs"]:
                touch_staging(self.target, host, reason="release:late", now=lambda: at(59))
                return subprocess.CompletedProcess(
                    command, 0, log(30, "POST", "/web/dataset/call_kw/a/b", 200) + "\n", "",
                )
            return original(command, **kw)

        host.run = touching_during_log_read
        idle_sweep(self.target, host, now=lambda: at(61))
        state = self.state(host)
        self.assertEqual(state["last_ci_at"], lifecycle.stamp(at(59)))
        self.assertTrue(state["last_interactive_at"].startswith("2026-11-04T12:30"))
        self.assertEqual(state["state"], "active")

    def test_an_interrupted_transition_is_recorded_not_left_forever(self) -> None:
        host = FakeHost(self.target, running=False)
        host.files[self.path] = lifecycle.dumps(active_state(state="waking", since=lifecycle.stamp(T0)))
        self.assertEqual(idle_sweep(self.target, host, now=lambda: at(5))["state"], "waking")
        result = idle_sweep(self.target, host, now=lambda: at(60))
        self.assertEqual(result["state"], "failed")
        self.assertEqual(self.state(host)["last_error"]["type"], "Interrupted")

    def test_a_brief_overlap_on_the_state_lock_does_not_fail_a_wake(self) -> None:
        host = FakeHost(self.target, running=False)
        host.files[self.path] = lifecycle.dumps(active_state(state="sleeping"))
        state_lock = f"{self.target.value['state_directory']}/lifecycle.lock"
        host.locks.add(state_lock)
        host.busy_briefly.add(state_lock)  # the sweep is writing, and lets go within the wait
        result = wake_staging(
            self.target, host, reason="release:x", release_lock_held=True, now=lambda: at(0),
        )
        self.assertEqual(result["status"], "woken")
        self.assertEqual(self.state(host)["state"], "active")

    def test_a_lease_granted_while_sleeping_starts_wins_over_the_sleep(self) -> None:
        host = FakeHost(self.target)
        host.files[self.path] = lifecycle.dumps(active_state(last_ci_at=lifecycle.stamp(T0)))
        original = host.run
        done = []

        def lease_arrives(command, **kw):
            if command[:2] == ["docker", "compose"] and not done:
                done.append(1)
            return original(command, **kw)

        # The lease lands between the check and the "stopping" write.
        real = __import__("operations.stack", fromlist=["_update_lifecycle"])._update_lifecycle
        calls = []

        def racing(target, runner, change, **kw):
            calls.append(1)
            if len(calls) == 1:
                lease_staging(target, runner, minutes=30, holder="manual:me", now=lambda: at(61))
            return real(target, runner, change, **kw)

        with mock.patch("operations.stack._update_lifecycle", racing):
            result = sleep_staging(self.target, host, reason="idle", now=lambda: at(61))
        self.assertEqual(result["status"], "deferred")
        self.assertEqual(host.stop_order, [])
        self.assertEqual(self.state(host)["state"], "active")

    def test_wake_inside_a_release_does_not_deadlock_on_its_own_lock(self) -> None:
        host = FakeHost(self.target, running=False)
        host.files[self.path] = lifecycle.dumps(active_state(state="sleeping"))
        host.locks.add(RELEASE_OPERATION_LOCK)  # the launcher holds it
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


class CleanupWhileAsleepTests(unittest.TestCase):
    def inventory(self, name: str, state: str, service: str) -> dict:
        from operations.stack import _cleanup_inventory
        target = load_target(name, ROOT / "operations/targets-host")
        labels = {"com.docker.compose.project.config_files": (
            f"{target.value['state_directory']}/generations/gcandidate/compose.generation.json"
        )}

        class Runner:
            def run(self, command, check=True, **_kw):
                if command[:2] == ["docker", "inspect"]:
                    return subprocess.CompletedProcess(command, 0, json.dumps(labels), "")
                if command[0] == "cat":
                    return subprocess.CompletedProcess(command, 1, "", "")
                return subprocess.CompletedProcess(command, 0, "", "")

        current = {"volumes": {}, "generation": "gactive", "containers": [
            {"ID": "c1", "State": state, "Service": service},
        ]}
        with mock.patch("operations.stack._cleanup_workspaces", return_value=[]), mock.patch(
            "operations.stack._cleanup_captures", return_value={},
        ):
            return _cleanup_inventory(target, Runner(), current)

    def test_a_sleeping_staging_still_protects_what_its_stopped_cohort_needs(self) -> None:
        inventory = self.inventory("staging", "exited", "odoo-staging")
        self.assertIn("gcandidate", inventory["protected_generations"])
        self.assertIn("usl-odoo-staging-main-gcandidate-recovery", inventory["protected_networks"])

    def test_exited_one_shots_do_not_protect_anything_and_production_is_unchanged(self) -> None:
        self.assertNotIn("gcandidate", self.inventory("staging", "exited", "odoo-upgrade")["protected_generations"])
        self.assertNotIn("gcandidate", self.inventory("production", "exited", "odoo")["protected_generations"])
        self.assertIn("gcandidate", self.inventory("production", "running", "odoo")["protected_generations"])


class DaemonHttpTests(unittest.TestCase):
    """The wake page and endpoints, over a real socket, against a fake Pocket ID."""

    def setUp(self) -> None:
        import http.client
        import threading
        from http.server import ThreadingHTTPServer
        from scripts.tests.test_staging_wake_oidc import FakeProvider
        self.http = http.client
        target = load_target("staging", ROOT / "operations/targets-host")
        self.target = target
        self.host = FakeHost(target)
        self.state_path = f"{target.value['state_directory']}/lifecycle.json"
        config = target.value["lifecycle"]
        environment = mock.patch.dict(
            "os.environ", {"POCKET_ID_CLIENT_ID": "client-1", "POCKET_ID_CLIENT_SECRET": "s3cret"},
        )
        environment.start()
        self.addCleanup(environment.stop)
        import time as _time
        self.provider = FakeProvider(_time.time, config["oidc_issuer"])
        self.woken: list[str] = []
        real_init = ThreadingHTTPServer.__init__

        def bind_ephemeral(server, address, handler, *a, **kw):
            real_init(server, ("127.0.0.1", 0), handler, *a, **kw)

        with mock.patch.object(ThreadingHTTPServer, "__init__", bind_ephemeral):
            self.server = lifecycle_server(target, self.host, self.woken.append, http=self.provider)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def set_state(self, name: str) -> None:
        self.host.files[self.state_path] = lifecycle.dumps(active_state(state=name))

    def request(self, method: str, path: str, *, headers: dict | None = None, body: bytes | None = None):
        connection = self.http.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(method, path, body=body, headers={"User-Agent": "Mozilla/5.0 Firefox", **(headers or {})})
        response = connection.getresponse()
        data = response.read().decode()
        result = (response.status, data, response.getheaders())
        connection.close()
        return result

    def cookie_from(self, headers, name: str) -> str:
        for key, value in headers:
            if key.lower() == "set-cookie" and value.startswith(name + "="):
                return value.split(";", 1)[0]
        raise AssertionError(f"no {name} cookie in {headers}")

    def sign_in(self, groups=("c_suite",)) -> str:
        """Run the whole browser flow; returns the session cookie."""
        self.provider.userinfo = {**self.provider.userinfo, "groups": list(groups)}
        status, _, headers = self.request("GET", "/__usl/login")
        self.assertEqual(status, 302)
        location = dict(headers)["Location"]
        import urllib.parse
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(location).query))
        self.provider.nonce = query["nonce"]
        login = self.cookie_from(headers, "usl_wake_login")
        status, _, headers = self.request(
            "GET", f"/__usl/callback?state={query['state']}&code=abc", headers={"Cookie": login},
        )
        return self.cookie_from(headers, "usl_wake_session") if status == 302 else ""

    def test_a_sleeping_staging_serves_the_sign_in_page_and_healthy_probes(self) -> None:
        self.set_state("sleeping")
        status, body, _ = self.request("GET", "/some/odoo/path")
        self.assertEqual(status, 503)
        self.assertIn("/__usl/login", body)
        self.assertNotIn('action="/__usl/wake"', body)
        self.assertEqual(self.request("POST", "/web/dataset/call_kw")[0], 503)
        self.assertEqual(self.request("GET", "/web/health")[:2], (200, '{"status": "sleeping"}'))
        self.assertEqual(
            json.loads(self.request("GET", "/__usl/status")[1]),
            {"state": "sleeping", "since": lifecycle.stamp(T0)},
        )

    def test_an_active_staging_that_does_not_answer_is_a_502_not_a_healthy_sleep(self) -> None:
        self.set_state("active")
        self.assertEqual(self.request("GET", "/web/health")[0], 502)
        self.assertEqual(self.request("GET", "/")[0], 502)

    def test_a_failed_staging_keeps_the_sign_in_so_people_can_retry(self) -> None:
        self.set_state("failed")
        status, body, _ = self.request("GET", "/")
        self.assertEqual(status, 502)
        self.assertIn("/__usl/login", body)
        self.assertEqual(self.request("GET", "/web/health")[0], 502)

    def test_a_member_of_the_group_signs_in_and_wakes_staging_under_their_own_name(self) -> None:
        self.set_state("sleeping")
        session = self.sign_in()
        self.assertTrue(session)
        status, body, _ = self.request("GET", "/", headers={"Cookie": session})
        self.assertIn("valentin@example.test", body)
        self.assertIn('action="/__usl/wake"', body)
        status, _, _ = self.request("POST", "/__usl/wake", headers={"Cookie": session})
        self.assertEqual(status, 202)
        self.assertEqual(self.woken, ["web:valentin@example.test"])

    def test_the_session_cookie_is_locked_down_and_the_login_state_is_cleared(self) -> None:
        self.set_state("sleeping")
        self.provider.userinfo = {**self.provider.userinfo, "groups": ["c_suite"]}
        _, _, headers = self.request("GET", "/__usl/login")
        login_cookie = dict((k, v) for k, v in headers if k == "Set-Cookie")["Set-Cookie"]
        for flag in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/__usl/"):
            self.assertIn(flag, login_cookie)
        import urllib.parse
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(dict(headers)["Location"]).query))
        self.provider.nonce = query["nonce"]
        _, _, headers = self.request(
            "GET", f"/__usl/callback?state={query['state']}&code=abc",
            headers={"Cookie": login_cookie.split(";", 1)[0]},
        )
        cookies = [v for k, v in headers if k == "Set-Cookie"]
        session = next(c for c in cookies if c.startswith("usl_wake_session="))
        for flag in ("HttpOnly", "Secure", "SameSite=Strict"):
            self.assertIn(flag, session)
        self.assertTrue(any(c.startswith("usl_wake_login=;") and "Max-Age=0" in c for c in cookies))

    def test_someone_outside_the_group_cannot_sign_in_and_so_cannot_wake(self) -> None:
        self.set_state("sleeping")
        self.assertEqual(self.sign_in(groups=("accountants",)), "")
        self.assertEqual(self.woken, [])

    def test_waking_needs_a_session_a_human_agent_and_the_right_origin(self) -> None:
        self.set_state("sleeping")
        session = self.sign_in()
        self.assertEqual(self.request("POST", "/__usl/wake")[0], 401)
        self.assertEqual(self.request("POST", "/__usl/wake", headers={"Cookie": session + "x"})[0], 401)
        self.assertEqual(self.request(
            "POST", "/__usl/wake", headers={"Cookie": session, "User-Agent": "Googlebot/2.1"})[0], 403)
        self.assertEqual(self.request(
            "POST", "/__usl/wake", headers={"Cookie": session, "Origin": "https://evil.example.test"})[0], 403)
        self.assertEqual(self.woken, [])
        origin = self.target.value["endpoints"]["odoo"].rstrip("/")
        self.assertEqual(self.request("POST", "/__usl/wake", headers={"Cookie": session, "Origin": origin})[0], 202)

    def test_a_callback_without_the_login_cookie_or_with_a_forged_state_is_refused(self) -> None:
        self.set_state("sleeping")
        status, body, headers = self.request("GET", "/__usl/callback?state=x&code=y")
        self.assertEqual(status, 503)
        self.assertIn("expired", body)
        self.assertFalse([v for k, v in headers if k == "Set-Cookie" and v.startswith("usl_wake_session=")])

    def test_automated_clients_cannot_even_start_a_sign_in(self) -> None:
        self.set_state("sleeping")
        status, _, headers = self.request("GET", "/__usl/login", headers={"User-Agent": "Googlebot/2.1"})
        self.assertEqual(status, 503)
        self.assertNotIn("Location", dict(headers))

    def test_without_a_provisioned_client_nothing_can_wake(self) -> None:
        self.set_state("sleeping")
        environment = mock.patch.dict("os.environ", {"POCKET_ID_CLIENT_ID": "", "POCKET_ID_CLIENT_SECRET": ""})
        environment.start()
        self.addCleanup(environment.stop)
        status, body, _ = self.request("GET", "/")
        self.assertEqual(status, 503)
        self.assertIn("not set up", body)
        self.assertEqual(self.request("GET", "/__usl/login")[0], 503)
        self.assertEqual(self.request("POST", "/__usl/wake")[0], 401)


if __name__ == "__main__":
    unittest.main()
