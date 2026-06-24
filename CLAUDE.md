# CLAUDE.md — meaco-exporter

Prometheus exporter and local control for a single Meaco Arete Two dehumidifier,
spoken to over the Tuya LAN protocol via `tinytuya`. No Tuya cloud at runtime.
Deployed to the `lifestyle` namespace of the homelab k3s cluster, pinned to the
Pi node so it can reach the device on the home LAN.

## Architecture

Single module, `meaco_exporter.py`, no framework:

- A **poller thread** calls `tinytuya` `status()` every `POLL_INTERVAL` seconds
  and caches the raw DPS dict.
- A **stdlib `HTTPServer`** serves `GET /metrics` (renders the cache as
  Prometheus text — hand-rolled, no `prometheus_client` dependency) and
  `POST /control` (validates and applies writes).
- `--probe` does a single synchronous `status()` read and prints the DPS, for
  bring-up.

Layout mirrors `fluv/lgtv-exporter`; the deploy mirrors `fluv/kube`'s
`lifestyle/lgtv-exporter.yaml`.

### Lock discipline

Two locks: `_device_lock` guards all `tinytuya` socket I/O (the library is not
safe for concurrent use); `_state_lock` guards the cached DPS dict and flags.

**They are never held simultaneously.** Every path takes one, releases it, then
takes the other — sequential `with` blocks, never nested. This is deliberate and
load-bearing: it means there is no lock-ordering relationship to get wrong, so no
deadlock is possible between the poller and the control handler. Keep it that
way. (A cold review once flagged a nested-lock deadlock here — it had misread the
sequential blocks. Don't nest them and the question never arises.)

## DPS map (Meaco Arete Two)

Taken from [make-all/tuya-local](https://github.com/make-all/tuya-local)'s
`custom_components/tuya_local/devices/meaco_aretetwo_dehumidifier.yaml`. This is
the device-specific knowledge that makes the whole thing work — preserve the
provenance.

| DPS | Name | Type | Notes |
|---|---|---|---|
| 1 | switch | bool | power on/off |
| 2 | humidity | int | target setpoint, 35–70, step 5 |
| 4 | mode | str | `manual` / `laundry` / `sleep` / `purify` |
| 14 | lock | bool | child lock |
| 16 | current_humidity | int | measured room humidity (the reading) |
| 17 | timer | str | off-timer |
| 18 | time_remaining | int | hours left on active timer |
| 19 | status | bitfield | bit0 tank full, bit1 defrost, bit7 moisture |
| 101 | on_timer | str | on-timer |

This model exposes **no temperature** and **no separate fan-speed** DPS — mode
covers fan behaviour, and room temperature comes from the Awair sensors. Don't
invent metrics for them.

## Configuration

All via environment (see the module docstring for the full list). Required:
`MEACO_IP`, `MEACO_DEVICE_ID`, `MEACO_LOCAL_KEY`. `MEACO_VERSION` defaults to
`3.3`.

The **device ID and local key live only in a Kubernetes secret**
(`meaco-local-key` in `lifestyle`, keys `device-id` / `local-key`). The local key
is a device credential — never commit it, never print it, never paste it into a
PR/issue/chat. Only the LAN IP goes in the deploy manifest as plaintext.

## Build & CI

`pyproject.toml` carries the version; the `Build and push` workflow builds a
multi-arch image to `ghcr.io/fluv/meaco-exporter` and cuts a release when the
version is new. **Bump the version in `pyproject.toml` for any source/Docker
change** (the DeepSeek reviewer enforces this).

## Bring-up

The protocol version is not knowable from the Tuya console — confirm it
empirically. Once deployed, exec the probe (the key stays in the cluster):

```sh
kubectl -n lifestyle exec deploy/meaco-exporter -- python3 meaco_exporter.py --probe
```

- A clean DPS dump → version and key are correct; map the keys against the table.
- A timeout → the device isn't answering on `6668`. Most likely it's **switched
  off at the wall** (port shows `filtered` when the unit is unpowered). Some Tuya
  firmware updates also disable local control entirely; if the unit is on and
  6668 is still filtered, local control may be gone and the cloud API is the
  fallback.
- A decode error → wrong `MEACO_VERSION`; try `3.4` then `3.5`.

Local keys can rotate on device firmware update — if a previously-working
deployment starts timing out after an update, re-fetch the key.

## Related

`fluv/claude#240` (parent feature), `fluv/claude#279` (Tuya local investigation).
