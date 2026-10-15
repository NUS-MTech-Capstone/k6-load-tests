# DenDenMushi k6 Load Tests

## Guarded AWS evaluation

The A01/A07/R13 evaluation targets **40 concurrent interactive SSH sessions**
on the existing dev deployment. This is an acceptance target, not a measured
result. The deployment's configured limit remains 50; the campaign reserves
ten slots and accounts for existing sessions. Historical API-only evidence remains in `../documentation/performance-evidence/2026-10-07/`. New SSH evidence is stored under its actual campaign date.

`tools/campaign.py` runs sustained terminal traffic separately from API reads.
It requires Python 3.10+, k6, AWS CLI, kubectl, access to the existing capstone
AWS profile, and a current approved change request scoped to `host-dev-1`.
The Python runner uses only the standard library. AWS account and Kubernetes
context checks are explicit; it does not modify STS or infrastructure.

```bash
make test
make inspect
python3 tools/campaign.py --ssh-only --cr APPROVED_CR_ID \
  --output ../documentation/performance-evidence/YYYY-MM-DD/ssh-campaign

# Read-only profiles do not need SSH change authority.
python3 tools/campaign.py --reads-only \
  --output ../documentation/performance-evidence/YYYY-MM-DD/api-campaign
```

On macOS, the opt-in runner can authenticate through the existing Cognito
hosted login with the dedicated `DenDenMushi E2E Cognito` Keychain entry. It
checks the destination, keeps the password and ALB cookie in process memory,
and never writes them to arguments or evidence. An existing
`DDM_ALB_COOKIE_HEADER` can be supplied instead. It never creates or approves
change requests, changes permissions, or bypasses an expired access window.

The local coordinator binds only to loopback and uses a random per-run
credential for mutations. It caps session starts globally at two per second,
tracks campaign session IDs for cleanup, and provides a credential-free live
progress page. Only the dedicated campaign CR may be used: uncertain start
responses are checked against that CR after abort. Unattributed session IDs stop
the campaign for review; they are never automatically closed.

### Workloads and acceptance

| Profile | Measurement |
| --- | --- |
| `terminal_capacity.js` | One PTY per VU; 60-second staggered ramp, five-minute plateau; levels 5, 10, 20, 30, 40. |
| `terminal_soak.js` | Same traffic, with `K6_MEASURE_SECONDS=1200` supplied by the campaign. |
| Lifecycle | Five VUs, three minutes, three repetitions. |
| Start stress | Five VUs, two minutes plus 30-second ramps, two starts/second maximum. |
| Agentic and audit reads | Ten VUs, five minutes, three repetitions each, run separately. |

The campaign repeats 20 sessions and the highest passing level twice more,
then soaks at the highest passing level. A failed level is retained and stops
escalation. Runs receive a minimum one-minute cooldown; resource recovery is
bounded to five minutes.

Echo probes send a unique printable marker every two seconds, then clear the
input line with Ctrl-U; they never submit it as a shell command. Only an exact
match in terminal output completes a probe. Fragmentation is supported,
events/errors are excluded, and the transient buffer is capped at 4 KiB.
The 1.5-second timeout produces a failed sample, never a fast success.
This measures end-to-end transport/PTY echo, not browser rendering latency or
heavy command-execution throughput.

Acceptance: steady echo p95 <200 ms, session-start API p95 <2 s, connection/probe success ≥99%, remote HTTP failures <1%, no unexpected disconnections or protocol errors, and zero campaign sessions after cleanup. API-read profiles separately require p50/p95 <1 s. k6
coordinator traffic is excluded from remote HTTP thresholds. Reports include
p50/p95/p99, counts, failed thresholds, and the process exit status.

### Stopping and evidence

The runner stops on authorization failure, unexpected disconnects, workload
readiness/restart/revision changes, failed cleanup, >5% rolling HTTP failures,
>1% probe failures, or rolling echo p95 >1 s. Resource checks stop after two
consecutive one-minute samples above 85% CPU or 80% memory relative to the
critical containers' limits, or 85% CPU on the target/RDS and 80% target memory. Missing required
telemetry older than three minutes stops the run.

Artifacts include `campaign.json`, deployment snapshots, per-run manifests,
`summary.json`, `metrics.jsonl`, HTML reports, availability probes, resource
samples, session inventory, sanitized lifecycle events, and cleanup results.
Do not enable HTTP debug logging. Reports must be read together with
`result.json`: aborted or nonzero-exit runs cannot be treated as passing.
Session content, response bodies, authentication headers, and credentials are
excluded from artifacts. Metrics are timestamped; the report labels gaps and
distinguishes a tested ceiling from actual saturation or long-term uptime.

Local integration tests open loopback sockets. If the managed sandbox denies
socket binding, run those tests outside that sandbox without changing them.

## Standalone profiles

The Makefile retains `smoke`, `load`, `stress`, `agentic` and `audit` shortcuts.
These direct k6 invocations do not activate the global creation limiter,
resource monitoring or interruption reconciliation. Use the campaign runner
for the agreed AWS evaluation. The legacy `*-report` targets provide a local
summary and optional macOS desktop capture; they are not the guarded campaign.

Core configuration uses `BASE_URL`, `DDM_ALB_COOKIE_HEADER`, `HOST_ID` and
`CR_ID`. Set credentials through the invoking process environment without
putting them in shell history. Do not enable HTTP debug output or commit
`.env`, cookies, HAR exports or response bodies. `K6_VUS` accepts integral
values 1–40; duration and ramp settings remain bounded by the campaign.

API iterations sleep one second. Terminal-capacity probes never execute
commands. The campaign sets a harmless printable, cleared marker for
smoke/lifecycle profiles (without submitting a command) and exercises resize, detach, rejoin, observer attach
and close. Start stress creates and closes sessions without opening a PTY.
Agentic and audit profiles only read existing records; they never approve,
reject, remediate or change infrastructure.

`tools/export_cloudwatch.py --output DIRECTORY --start UTC --end UTC` exports
the existing dashboard plus supplemental charts using verified published
metric dimensions. Container limit charts are separate from the dashboard's
node-relative pod percentages. Original missing series remain visible.
`tools/build_evidence.py EVIDENCE_DIRECTORY` derives comparable run rows from
raw summaries, including p50/p95/p99, sample counts, throughput, cleanup and
exact availability observation windows. It does not infer a terminal capacity
result from API-read load.

## SSH-only measurement controls

Use `--ssh-only` to exclude the historical API-read repetitions. The runner requires a clean committed harness, a new output directory, normal authenticated access, five minutes of fresh baseline telemetry, and an approved dedicated change request. CI runs only local fixtures and never signs into AWS.

Each capacity run waits for every requested SSH session's first exact echo, then starts a common timed plateau. The recorded `plateau` includes readiness, start/end times, per-session probe counts and premature disconnections. Insufficient concurrency, missing samples or a shortened plateau cannot pass. `ddm_ssh_ready_ms` measures WebSocket/SSH setup through the first scheduled echo; it includes probe scheduling and is distinct from session-start API timing and per-probe terminal echo timing.

Lifecycle runs verify fresh echo after resize, retained scrollback plus new echo after rejoin, explicit observer write rejection without echo, and server termination followed by idempotent close. No arbitrary shell command runs. Health/authentication/telemetry/cleanup failures stop the whole campaign. Only ordinary latency SLO failures may proceed to lower-level repetitions after verified cooldown; the failed run remains visible. A 40-session pass is a tested ceiling, not proof of the saturation point or browser rendering performance.

The protocol fixture records its deployed source pin. Source snapshots and the harness commit identify the generator; runtime digests and the Argo revision identify the application. Local sibling checkout SHAs are never substituted for deployed source. Resource series must contain all expected IDs with valid fresh values. Target memory is guarded alongside CPU. Generator CPU/RSS is recorded without process arguments or environment.
