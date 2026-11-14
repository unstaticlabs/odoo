"""Opt-in backup capture through the actual pinned GitOps Nginx gateway.

Only disposable public fixtures are used. No database, host port, external
network, provider credential or production mount enters this project.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

from operations.runtime import CommandRunner
from scripts.tests import test_backup_maintenance as fixture

GATEWAY_IMAGE = "nginx@sha256:b34848eff6db786b6b1282d3a9c3fd0b5563dfb6d261df4923378b419e0d24f0"
PYTHON_IMAGE = "python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df"
# Exact public gateway files qualified below; changing either requires a
# deliberate fixture-source update rather than silently testing a stand-in.
CONFIG_SHA256 = {
    "production": "025c3c7eed0266751831988a5ba6d60dc724d82182993a06417f9eea5ec3f790",
    "staging": "cd5b3d1af4f9a7dc7565216728506119236e075d9748f54983cb0bf32739552b",
}
WRITER = """import signal,sys,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
signal.signal(signal.SIGTERM,lambda *_:sys.exit(0))
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200);self.end_headers();self.wfile.write(b'{"status":"ready"}')
    do_POST=do_GET
    def log_message(self,*args):pass
threading.Thread(target=ThreadingHTTPServer(('0.0.0.0',8069),Handler).serve_forever,daemon=True).start()
ThreadingHTTPServer(('0.0.0.0',3000),Handler).serve_forever()
"""
PROBE = """import json,sys,urllib.error,urllib.request
request=urllib.request.Request(sys.argv[1],data=b'{}',headers={'Content-Type':'application/json'})
try:
    response=urllib.request.urlopen(request,timeout=5)
except urllib.error.HTTPError as error:
    response=error
print(json.dumps({'status':response.code,'retry_after':response.headers.get('Retry-After'),'body':response.read(4096).decode()}))
"""


class NativeHost(fixture.BackupHost):
    native_gateway = True

    def run(self, command, *, check=True, input_text=None):
        self.commands.append(command)
        if command[:2] == ["docker", "compose"]:
            if "stop" in command:
                self.events.append("stop")
            elif "up" in command:
                self.events.append("resume")
        return CommandRunner().run(command, check=check, input_text=input_text)


@unittest.skipUnless(os.environ.get("USL_BACKUP_GATEWAY_QA") == "1", "native gateway QA is opt-in")
class BackupGatewayDockerTests(unittest.TestCase):
    def test_real_gateway_before_during_after_and_capture_failure(self):
        config_root = Path(os.environ["USL_BACKUP_GATEWAY_CONFIG_ROOT"])
        for image in (GATEWAY_IMAGE, PYTHON_IMAGE):
            if subprocess.run(["docker", "image", "inspect", image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
                subprocess.run(["docker", "pull", image], check=True, stdout=subprocess.DEVNULL)
        for target, failure, borrowed in (("production", False, False), ("staging", False, False), ("production", True, False), ("production", False, True)):
            with self.subTest(target=target, capture_failure=failure, borrowed_marker=borrowed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                source = config_root / f"usl-odoo-{target}" / "gateway.conf"
                payload = source.read_bytes()
                self.assertEqual(hashlib.sha256(payload).hexdigest(), CONFIG_SHA256[target])
                config = root / "gateway.conf"; config.write_bytes(payload)
                host = NativeHost(root)
                host.marker = root / "gateway" / target / "maintenance"
                host.marker.parent.mkdir(parents=True, exist_ok=True)
                project = "usl-backup-gateway-" + uuid.uuid4().hex[:12]
                compose = root / "compose.json"; env = root / ".env"; env.touch()
                host.identity = {
                    "project": project, "working_directory": str(root),
                    "environment_file": str(env), "compose_files": [str(compose)],
                }
                common = {"read_only": True, "cap_drop": ["ALL"], "pids_limit": 48, "cpus": 0.25}
                compose.write_text(json.dumps({"services": {
                    "writer": {
                        **common, "image": PYTHON_IMAGE, "entrypoint": ["python", "-c", WRITER], "mem_limit": "64m",
                        "networks": {"default": {"aliases": ["odoo-mcp-app", "odoo-mcp-staging-app", "odoo-staging-app"]}},
                        "healthcheck": {"test": ["CMD", "python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:3000/readyz',timeout=2)"], "interval": "1s", "timeout": "3s", "retries": 20},
                    },
                    "gateway": {
                        **common, "image": GATEWAY_IMAGE, "user": "101:101", "mem_limit": "32m", "tmpfs": ["/tmp:size=16m,mode=1777"],
                        "command": ["nginx", "-c", "/etc/nginx/usl-gateway.conf", "-g", "daemon off;"],
                        "volumes": [f"{config}:/etc/nginx/usl-gateway.conf:ro", f"{host.marker.parent}:/run/usl-gateway:ro"],
                        "healthcheck": {"test": ["CMD", "nginx", "-t", "-c", "/etc/nginx/usl-gateway.conf"], "interval": "1s", "timeout": "3s", "retries": 20},
                    },
                }}))
                base = ["docker", "compose", "--project-name", project, "--file", str(compose)]
                def probe():
                    command = ["docker", "run", "--rm", "--network", project + "_default", "--read-only", "--cap-drop", "ALL", "--memory", "48m", "--pids-limit", "32", "--entrypoint", "python", PYTHON_IMAGE, "-c", PROBE, "http://gateway:3000/mcp" if target == "production" else "http://gateway:8069/web/health"]
                    return json.loads(subprocess.check_output(command, text=True))
                observations = []
                def during():
                    response = probe(); observations.append(response)
                    self.assertEqual(response["status"], 503, response)
                    self.assertEqual(response["retry_after"], "60")
                    if target == "production":
                        self.assertEqual(json.loads(response["body"])["error"], "maintenance")
                    else:
                        self.assertIn("Staging is being refreshed", response["body"])
                host.on_capture = during
                try:
                    subprocess.run([*base, "up", "--detach", "--wait"], check=True, stdout=subprocess.DEVNULL)
                    self.assertEqual(probe()["status"], 200)
                    preserved = b"synthetic-existing-release-maintenance\n"
                    if borrowed:
                        host.marker.write_bytes(preserved)
                        self.assertEqual(probe()["status"], 503)
                    case = fixture.BackupMaintenanceTests()
                    if failure:
                        with self.assertRaisesRegex(RuntimeError, "synthetic capture failure"):
                            case.execute(host, target_name=target, fail_capture=True)
                    else:
                        self.assertEqual(case.execute(host, target_name=target, quiesced=borrowed), 0)
                    if borrowed:
                        self.assertEqual(host.marker.read_bytes(), preserved)
                        self.assertEqual(probe()["status"], 503)
                        subprocess.run([*base, "up", "--detach", "--wait", "writer"], check=True, stdout=subprocess.DEVNULL)
                        self.assertEqual(probe()["status"], 503)
                        host.marker.unlink()  # Exact synthetic fixture, owned by this test.
                    else:
                        self.assertFalse(host.marker.exists())
                    self.assertEqual(probe()["status"], 200)
                    self.assertEqual(len(observations), 1)
                except Exception:
                    logs = subprocess.run([*base, "logs", "--no-color", "--tail", "12"], capture_output=True, text=True)
                    print(logs.stdout + logs.stderr)
                    raise
                finally:
                    subprocess.run([*base, "down", "--volumes", "--remove-orphans"], check=True, stdout=subprocess.DEVNULL)
                    remaining = subprocess.check_output(["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=" + project, "--format", "{{.ID}}"], text=True).strip()
                    self.assertFalse(remaining, "owned containers remain")
                    networks = subprocess.check_output(["docker", "network", "ls", "--filter", "label=com.docker.compose.project=" + project, "--format", "{{.ID}}"], text=True).strip()
                    self.assertFalse(networks, "owned networks remain")


if __name__ == "__main__":
    unittest.main()
