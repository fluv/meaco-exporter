#!/usr/bin/env python3
"""Prometheus exporter and local control for the Meaco Arete Two dehumidifier.

Talks to the device over the Tuya LAN protocol via tinytuya — no cloud round
trip. A background thread polls device status on an interval and caches it; the
HTTP server renders that cache as Prometheus metrics on /metrics and accepts
control commands on POST /control.

The DPS (data-point) map is specific to the Meaco Arete Two and is taken from
the make-all/tuya-local Home Assistant integration's device definition
(custom_components/tuya_local/devices/meaco_aretetwo_dehumidifier.yaml):

  1   switch            bool    power on/off
  2   humidity          int     target humidity setpoint (35-70, step 5)
  4   mode              str     manual | laundry | sleep | purify
  14  lock              bool    child lock
  16  current_humidity  int     measured room humidity (%)
  17  timer             str     off-timer
  18  time_remaining    int     hours remaining on active timer
  19  status            bitfield  bit0=tank full, bit1=defrost, bit7=moisture
  101 on_timer          str     on-timer

Environment variables:
  MEACO_IP          device IP address (required)
  MEACO_DEVICE_ID   Tuya device ID (required)
  MEACO_LOCAL_KEY   Tuya local key (required)
  MEACO_VERSION     Tuya protocol version, e.g. 3.3 / 3.4 / 3.5 (default: 3.3)
  PORT              HTTP port for /metrics and /control (default: 9096)
  POLL_INTERVAL     seconds between status polls (default: 30)

Run with --probe to perform a single status read and print it, then exit. Use
this from `kubectl exec` for bring-up — it reads the same env (and therefore the
key from the mounted secret) so the key never leaves the cluster.
"""

import json
import logging
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Lock
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger(__name__)

PORT = int(os.environ.get("PORT", "9096"))
IP = os.environ.get("MEACO_IP", "")
DEVICE_ID = os.environ.get("MEACO_DEVICE_ID", "")
LOCAL_KEY = os.environ.get("MEACO_LOCAL_KEY", "")
VERSION = float(os.environ.get("MEACO_VERSION", "3.3"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))

# DPS indices (Meaco Arete Two).
DPS_POWER = "1"
DPS_TARGET_HUMIDITY = "2"
DPS_MODE = "4"
DPS_CHILD_LOCK = "14"
DPS_CURRENT_HUMIDITY = "16"
DPS_TIME_REMAINING = "18"
DPS_STATUS = "19"

# Status bitfield (DPS 19).
STATUS_TANK_FULL = 0x01
STATUS_DEFROST = 0x02
STATUS_MOISTURE = 0x80

MODES = ("manual", "laundry", "sleep", "purify")
HUMIDITY_MIN = 35
HUMIDITY_MAX = 70
HUMIDITY_STEP = 5

# tinytuya is not safe for concurrent socket use; serialise all device I/O.
_device_lock = Lock()
_device: Any = None

_state_lock = Lock()
_dps: dict[str, Any] = {}
_reachable = False
_last_poll = 0.0


def _make_device() -> Any:
    import tinytuya

    dev = tinytuya.Device(DEVICE_ID, IP, LOCAL_KEY, version=VERSION)
    dev.set_socketPersistent(True)
    dev.set_socketTimeout(5)
    return dev


def _read_status() -> dict[str, Any]:
    """Read DPS from the device. Caller must hold _device_lock."""
    global _device
    if _device is None:
        _device = _make_device()
    status = _device.status()
    if not isinstance(status, dict) or "dps" not in status:
        raise RuntimeError(f"unexpected status payload: {status!r}")
    return status["dps"]


def _poll_once() -> None:
    global _reachable, _last_poll, _device
    try:
        with _device_lock:
            dps = _read_status()
    except Exception as e:
        log.warning("poll failed: %s", e)
        # Two separate, sequential critical sections — never nested. No code
        # path in this module holds _device_lock and _state_lock at the same
        # time, so there is no lock-ordering hazard between the poller and the
        # control handler.
        with _state_lock:
            _reachable = False
        with _device_lock:  # force a fresh connection on the next poll
            _device = None
        return
    with _state_lock:
        _dps.clear()
        _dps.update(dps)
        _reachable = True
        _last_poll = time.time()


def _poller() -> None:
    while True:
        _poll_once()
        time.sleep(POLL_INTERVAL)


def _render() -> bytes:
    with _state_lock:
        dps = dict(_dps)
        reachable = _reachable

    lines: list[str] = []

    def gauge(name: str, help_text: str, value: Any, **labels: str) -> None:
        if value is None:
            return
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lbl = ""
        populated = {k: v for k, v in labels.items() if v}
        if populated:
            parts = ",".join(f'{k}="{v}"' for k, v in populated.items())
            lbl = "{" + parts + "}"
        lines.append(f"{name}{lbl} {value}")

    gauge("meaco_up", "1 if the exporter reached the device on the last poll", int(reachable))

    if not reachable:
        return ("\n".join(lines) + "\n").encode()

    power = dps.get(DPS_POWER)
    if power is not None:
        gauge("meaco_power_on", "1 when the dehumidifier is switched on", int(bool(power)))

    gauge(
        "meaco_current_humidity_percent",
        "Measured room relative humidity (%)",
        dps.get(DPS_CURRENT_HUMIDITY),
    )
    gauge(
        "meaco_target_humidity_percent",
        "Target humidity setpoint (%)",
        dps.get(DPS_TARGET_HUMIDITY),
    )

    status = dps.get(DPS_STATUS)
    if isinstance(status, int):
        gauge("meaco_tank_full", "1 when the water tank is full", int(bool(status & STATUS_TANK_FULL)))
        gauge("meaco_defrost", "1 when the unit is in defrost", int(bool(status & STATUS_DEFROST)))
        gauge("meaco_moisture_alert", "1 when the moisture sensor is triggered", int(bool(status & STATUS_MOISTURE)))
        # Any bits outside the known set indicate a fault condition.
        fault = status & ~(STATUS_TANK_FULL | STATUS_DEFROST | STATUS_MOISTURE)
        gauge("meaco_fault_code", "Raw status bits outside the known tank/defrost/moisture flags", fault)

    lock = dps.get(DPS_CHILD_LOCK)
    if lock is not None:
        gauge("meaco_child_lock", "1 when the child lock is engaged", int(bool(lock)))

    remaining = dps.get(DPS_TIME_REMAINING)
    if isinstance(remaining, (int, float)):
        gauge("meaco_timer_hours_remaining", "Hours remaining on the active timer", remaining)

    mode = dps.get(DPS_MODE)
    if mode:
        lines.append("# HELP meaco_mode_info Current operating mode (always 1; mode is the label)")
        lines.append("# TYPE meaco_mode_info gauge")
        lines.append(f'meaco_mode_info{{mode="{mode}"}} 1')

    return ("\n".join(lines) + "\n").encode()


def _apply_control(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and apply a control payload. Returns the device DPS after the write."""
    commands: list[tuple[str, Any]] = []

    if "power" in payload:
        commands.append((DPS_POWER, bool(payload["power"])))

    if "target_humidity" in payload:
        h = payload["target_humidity"]
        if not isinstance(h, int) or h < HUMIDITY_MIN or h > HUMIDITY_MAX or h % HUMIDITY_STEP:
            raise ValueError(
                f"target_humidity must be an integer in [{HUMIDITY_MIN},{HUMIDITY_MAX}] "
                f"in steps of {HUMIDITY_STEP}"
            )
        commands.append((DPS_TARGET_HUMIDITY, h))

    if "mode" in payload:
        m = payload["mode"]
        if m not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        commands.append((DPS_MODE, m))

    if "child_lock" in payload:
        commands.append((DPS_CHILD_LOCK, bool(payload["child_lock"])))

    if not commands:
        raise ValueError("no recognised control keys (power, target_humidity, mode, child_lock)")

    with _device_lock:
        global _device
        if _device is None:
            _device = _make_device()
        for dps_id, value in commands:
            result = _device.set_value(dps_id, value)
            if isinstance(result, dict) and result.get("Error"):
                raise RuntimeError(f"set DPS {dps_id}={value} failed: {result}")
        dps = _read_status()

    # Refresh the cache so /metrics reflects the change immediately.
    with _state_lock:
        _dps.clear()
        _dps.update(dps)
    return dps


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path not in ("/", "/metrics"):
            self.send_response(404)
            self.end_headers()
            return
        body = _render()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/control":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("body must be a JSON object")
            dps = _apply_control(payload)
        except ValueError as e:
            self._json(400, {"error": str(e)})
            return
        except Exception as e:
            log.warning("control failed: %s", e)
            self._json(502, {"error": str(e)})
            return
        self._json(200, {"ok": True, "dps": dps})

    def _json(self, code: int, obj: dict[str, Any]) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: Any) -> None:
        pass


def _probe() -> None:
    with _device_lock:
        dps = _read_status()
    print(json.dumps(dps, indent=2, sort_keys=True))


def _require_env() -> None:
    missing = [n for n, v in (("MEACO_IP", IP), ("MEACO_DEVICE_ID", DEVICE_ID), ("MEACO_LOCAL_KEY", LOCAL_KEY)) if not v]
    if missing:
        log.error("missing required environment: %s", ", ".join(missing))
        sys.exit(1)


if __name__ == "__main__":
    _require_env()

    if "--probe" in sys.argv:
        _probe()
        sys.exit(0)

    threading.Thread(target=_poller, daemon=True).start()
    server = HTTPServer(("", PORT), _Handler)
    log.info("meaco-exporter listening on :%d (device %s, protocol %s)", PORT, IP, VERSION)
    server.serve_forever()
