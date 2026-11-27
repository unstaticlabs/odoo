"""Check a new browser session against the isolated, restored Odoo server."""
import json
import time

import requests
from lxml import html


def wait_for_listener(origin):
    for attempt in range(40):
        try:
            requests.get(f"{origin}/web/health", timeout=3).raise_for_status()
            return
        except requests.ConnectionError:
            if attempt == 39:
                raise
            time.sleep(0.5)


http = "http://127.0.0.1:18069"
evented = "http://127.0.0.1:18072"
for origin in (http, evented):
    wait_for_listener(origin)

login = requests.get(f"{http}/web/login", allow_redirects=False, timeout=10)
fields = html.fromstring(login.content).xpath('//form[@action="/web/login"]//input/@name')
listing = requests.post(f"{http}/web/database/list", timeout=10, json={
    "jsonrpc": "2.0", "method": "call", "params": {}, "id": 1,
}).json()
headers = {
    "Connection": "Upgrade", "Upgrade": "websocket", "Origin": http,
    "Sec-WebSocket-Version": "13", "Sec-WebSocket-Key": "MDEyMzQ1Njc4OWFiY2RlZg==",
}
with requests.get(f"{evented}/websocket", headers=headers, stream=True, timeout=10) as socket:
    print(json.dumps([login.status_code, {"login", "password"} <= set(fields), socket.status_code,
                      listing.get("error", {}).get("data", {}).get("name")]))
