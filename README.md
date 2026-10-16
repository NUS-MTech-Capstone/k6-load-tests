# DenDenMushi Load Tests

k6 workloads and a guarded campaign runner for API reads, interactive SSH,
session lifecycle and start/close traffic.

## Completed API measurements

Three five-minute API-read runs on 7 October 2026 used ten virtual users with
one-second iteration pacing. Together they recorded **37,856 successful HTTP
requests, zero HTTP failures and 185 successful availability probes**.

| Workload | Requests | HTTP p50 | HTTP p95 | HTTP p99 |
| --- | ---: | ---: | ---: | ---: |
| Agentic reads | 16,248 | 13.52 ms | 34.11 ms | 78.46 ms |
| Audit reads 1 | 10,808 | 21.07 ms | 46.75 ms | 101.84 ms |
| Audit reads 2 | 10,800 | 20.82 ms | 45.52 ms | 117.91 ms |

These figures measure API reads. The supplied workspace evidence is at
`capstone/documentation/performance-evidence/2026-10-07/`, outside this Git
repository.

## Run

Requires Python 3.10+, k6, AWS CLI and kubectl. AWS runs use the `capstone`
profile, normal Cognito/ALB authentication and the public service origin.
SSH runs additionally require a dedicated approved change request for the host.

```bash
make test
make inspect
python3 tools/campaign.py --reads-only \
  --output ../documentation/performance-evidence/YYYY-MM-DD/api-campaign
python3 tools/campaign.py --ssh-only --cr APPROVED_CR_ID \
  --output ../documentation/performance-evidence/YYYY-MM-DD/ssh-campaign
```

Use a clean committed harness and a fresh output directory. The runner checks
the AWS account, Kubernetes context and five minutes of baseline telemetry.
On macOS it can authenticate using the `DenDenMushi E2E Cognito` Keychain entry;
alternatively supply `DDM_ALB_COOKIE_HEADER` through the process environment.
Credentials remain in memory. Change requests and approvals are supplied by the
operator.

## Workload profiles

| Profile | Configured workload |
| --- | --- |
| API reads | Agentic and audit reads, ten VUs and five-minute runs. |
| Terminal capacity | One SSH PTY per VU; 60-second ramp and five-minute plateau. |
| Terminal soak | Same PTY traffic with a 20-minute plateau. |
| Lifecycle | Five VUs exercising echo, resize, detach/rejoin, observe and close. |
| Start/close | Five VUs with a global limit of two session starts per second. |

The SSH runner supports levels 5, 10, 20, 30 and 40, constrained by the
configured capacity, existing sessions and a ten-slot reserve. It repeats
selected levels and runs soak at the highest passing level. These are harness
settings; run artifacts record the measured concurrency and outcome.

Every requested SSH session must return its first exact echo before the common
plateau starts. Probes send a unique marker every two seconds, allow 1.5 seconds
for echo, and clear the line with Ctrl-U without executing a shell command.
Fragmented output is supported; event/error packets cannot complete a probe.
Measurements separate session-start API time, SSH readiness and PTY echo time.

Lifecycle checks cover new echo after resize, retained scrollback and echo after
rejoin, observer write rejection and server termination. Start/close profiles
measure session creation and closure separately from PTY traffic.

## Acceptance and safeguards

- SSH thresholds: sustained requested concurrency, echo p95 below 200 ms,
  session-start API p95 below two seconds, connection/probe success at least
  99%, HTTP failures below 1%, and zero campaign sessions after cleanup.
- API-read thresholds: p50/p95 below one second, checks above 99% and HTTP
  failures below 1%.
- Authentication failures, unexpected disconnects, deployment/readiness changes,
  stale required telemetry or failed cleanup stop the campaign.
- Rolling guards stop on HTTP failures above 5%, probe failures above 1%,
  echo p95 above one second, or sustained resource thresholds: 85% CPU and
  80% memory, using container limits and target/RDS metrics as applicable.
- The runner closes only positively attributed campaign sessions, verifies
  released capacity and applies a bounded recovery cooldown.

Reports include p50/p95/p99, sample counts, threshold outcomes, timestamps,
resource measurements, source/image identities and cleanup results. Read
`result.json` alongside each summary for its completion and exit status.
Artifacts exclude credentials, headers, response bodies and terminal streams.
Keep HTTP debug logging disabled.

## Local tools and CI

CI runs local fixtures, including fragmentation, timeouts, readiness, interruption
and cleanup checks. Loopback integration tests require local socket access.

The Makefile also exposes standalone `smoke`, `load`, `stress`, `agentic` and
`audit` profiles. Use the campaign runner for AWS evaluation so its rate limits,
resource guards and cleanup reconciliation apply.

`tools/export_cloudwatch.py` exports time-bounded resource series and charts.
`tools/build_evidence.py` derives comparable run tables from retained summaries.
The protocol fixture records its Proxy Dataplane source revision.
