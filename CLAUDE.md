# CLAUDE.md — meaco-exporter

What it is and how to use it lives in the README. This file is only the things
that aren't obvious from the code and the ways of working that are easy to get
wrong.

## Surprising things

- **The protocol version isn't in the Tuya console.** Confirm it empirically
  with `--probe`. Default is `3.3`; a decode error means try `3.4` then `3.5`.
- **A probe timeout almost always means the unit is switched off at the wall,
  not that anything is broken.** A powered-down Tuya device makes port `6668`
  show as `filtered`. It's a dehumidifier — it's off for most of the year. Don't
  go chasing a connectivity bug before checking it's plugged in and on.
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
  secret — never in a commit, PR, issue, or chat. Bring-up validation runs
  *inside* the running pod (`--probe`) specifically so the key never leaves the
  cluster.
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
