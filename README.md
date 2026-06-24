# meaco-exporter

Prometheus exporter and local control for the [Meaco Arete Two](https://www.meaco.com/)
dehumidifier, over the Tuya LAN protocol via [tinytuya](https://github.com/jasonacox/tinytuya).
No Tuya cloud round trip at runtime — the device is polled directly on the local
network.

The data-point (DPS) map is taken from the
[make-all/tuya-local](https://github.com/make-all/tuya-local) Home Assistant
integration's `meaco_aretetwo_dehumidifier.yaml` device definition.

## Metrics

Served on `:9096/metrics`.

| Metric | Source DPS | Meaning |
|---|---|---|
| `meaco_up` | — | 1 if the device was reachable on the last poll |
| `meaco_power_on` | 1 | 1 when switched on |
| `meaco_current_humidity_percent` | 16 | measured room humidity |
| `meaco_target_humidity_percent` | 2 | target humidity setpoint |
| `meaco_tank_full` | 19 (bit 0) | 1 when the water tank is full |
| `meaco_defrost` | 19 (bit 1) | 1 when defrosting |
| `meaco_moisture_alert` | 19 (bit 7) | 1 when the moisture sensor is triggered |
| `meaco_fault_code` | 19 | status bits outside the known flags |
| `meaco_child_lock` | 14 | 1 when the child lock is engaged |
| `meaco_timer_hours_remaining` | 18 | hours left on the active timer |
| `meaco_mode_info{mode=...}` | 4 | current mode (`manual`/`laundry`/`sleep`/`purify`) |

## Control

`POST /control` with a JSON body containing any subset of:

```json
{ "power": true, "target_humidity": 55, "mode": "laundry", "child_lock": false }
```

- `target_humidity` — integer in `[35, 70]`, multiples of `5`
- `mode` — one of `manual`, `laundry`, `sleep`, `purify`

Returns `{"ok": true, "dps": {...}}` with the device state after the write, or a
`4xx`/`5xx` JSON error.

## Configuration

| Variable | Default | |
|---|---|---|
| `MEACO_IP` | — | device IP (required) |
| `MEACO_DEVICE_ID` | — | Tuya device ID (required) |
| `MEACO_LOCAL_KEY` | — | Tuya local key (required) |
| `MEACO_VERSION` | `3.3` | Tuya protocol version |
| `PORT` | `9096` | HTTP port |
| `POLL_INTERVAL` | `30` | seconds between status polls |

## Bring-up

The local key is a device credential and lives only in a Kubernetes secret. To
verify connectivity without the key leaving the cluster, exec into the running
pod (which has the secret mounted as env) and run a one-shot probe:

```sh
kubectl -n lifestyle exec deploy/meaco-exporter -- python3 meaco_exporter.py --probe
```

This prints the raw DPS map, which is the quickest way to confirm the protocol
version and key are correct.
