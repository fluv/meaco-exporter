# CLAUDE.md — meaco-exporter

What it is and how to use it lives in the README. This file is only the things
that aren't obvious from the code and the ways of working that are easy to get
wrong.

## Surprising things

- **The protocol version isn't in the Tuya console.** This unit is `3.5`
  (`tinytuya scan` reports it); the code default is `3.3`. A wrong version *or* a
  bad local key both surface identically as tinytuya error `914` in the logs.
- **A connection timeout in the logs — as opposed to `914` — means the unit is
  switched off at the wall, not that anything is broken.** A powered-down Tuya
  device makes port `6668` show as `filtered`. Check it's plugged in before
  chasing a connectivity bug. (`914` is the opposite signal: it connected, but
  the key or version is wrong.)
- **The local key can rotate on a device firmware update.** A deployment that
  was working and then starts timing out after an update needs the key
  re-fetched, not a code change.
- **This model has no temperature and no fan-speed data point.** Mode covers fan
  behaviour; room temperature is the Awair sensors' job. Don't add metrics for
  data the device doesn't expose.
- **The DPS map is borrowed, not reverse-engineered.** It comes from
  make-all/tuya-local's `meaco_aretetwo` device definition. If a data point
  behaves unexpectedly, check upstream there before assuming the device is odd.

## Ways of working

- **The local key is a device credential.** It lives only in the Kubernetes
  secret — never in a commit, PR, issue, or chat.
- **Bring-up is read from the pod logs, not `exec`.** The `claude` service
  account has no `exec`/`scale`/`patch` in `lifestyle` — only `get`/`logs` (see
  fluv/kube#673). Confirm a connection by watching the logs for the `914`
  warnings to stop; success is silent. Read live state with the `meaco-status`
  script. The `--probe` flag exists but needs `exec`, so it's a human-only path.
- **Never nest the two locks.** One guards device I/O, the other the cached
  state; every path takes one and releases it before taking the other. Nesting
  them is the one thing that *would* create a deadlock — a cold review once
  flagged exactly that, having misread the sequential blocks as nested. Keep
  them sequential and there is no lock order to get wrong.
- **Bump the version in `pyproject.toml` for any source or Docker change.** The
  DeepSeek reviewer blocks merge otherwise.
- Reviews come from the DeepSeek bot; this is one of the DS-reviewed repos where
  bot approval is sufficient to merge.

## Related

`fluv/claude#240` (parent feature), `fluv/claude#279` (Tuya local investigation).
