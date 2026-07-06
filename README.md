# DenDenMushi k6 Load Tests

Focused Grafana k6 tests for the key DenDenMushi application paths. The suite
targets the AWS dev application first and defaults to:

```text
https://api.ddm.keiyam.me
```

These tests preserve the production boundaries: session control goes through
Proxy Control Plane, live terminal traffic connects directly to Proxy
Dataplane over WebSocket, and Agentic AI tests only read safe operator APIs.

## Scope

Included in v1:

- health and readiness checks
- SSH session start, owner WebSocket attach, resize/input, detach, rejoin,
  observe, and close
- safe session list and rejoin-candidate reads
- Agentic AI health, alerts, incidents, and incident timeline reads
- session audit and metadata audit facade reads
- session-start stress testing with cleanup

Not included:

- Cognito password or browser login automation
- Cognito user administration load
- broad CRUD/API fuzzing
- incident approval, rejection, or remediation load by default
- Terraform, Kubernetes, or GitOps operations
- any brokered terminal traffic path

## Requirements

- [Grafana k6](https://grafana.com/docs/k6/latest/set-up/install-k6/)
- An active ALB-authenticated browser session for the target environment
- A host/change-request pair that is valid for your authenticated user

## Configuration

Copy the example and fill in local values:

```bash
cp .env.example .env
```

Required for AWS dev:

```bash
export BASE_URL=https://api.ddm.keiyam.me
export DDM_ALB_COOKIE_HEADER='Cookie: AWSELBAuthSessionCookie-0=...; AWSELBAuthSessionCookie-1=...'
export HOST_ID='...'
export CR_ID='...'
```

Optional tuning:

```bash
export K6_VUS=5
export K6_DURATION=2m
export K6_RAMP_UP=30s
export K6_RAMP_DOWN=30s
export K6_SESSION_HOLD_SECONDS=2
export K6_WS_COMMAND='printf ddm-k6\n'
```

`DDM_ALB_COOKIE_HEADER` may be either the full `Cookie: ...` header line or only
the cookie value. For uncommon environments that require extra static headers,
set `DDM_AUTH_HEADERS_JSON` to a JSON object.

Do not commit real cookies, bearer tokens, HAR exports, or result files. The
`.gitignore` excludes the usual local secret and output files.

## Running

The `Makefile` assumes environment variables are already loaded:

```bash
set -a
source .env
set +a

make smoke
make load
make stress
make agentic
make audit
```

Equivalent direct commands:

```bash
k6 run scripts/smoke.js
k6 run scripts/session_lifecycle.js
k6 run scripts/stress_session_start.js
k6 run scripts/agentic_ai_reads.js
k6 run scripts/audit_reads.js
```

## Profiles

| Target | Script | Purpose |
| --- | --- | --- |
| `smoke` | `scripts/smoke.js` | One authenticated end-to-end check: health, start, WebSocket attach, close. |
| `load` | `scripts/session_lifecycle.js` | Repeated SSH lifecycle coverage: owner attach, detach, rejoin, observe, close. |
| `stress` | `scripts/stress_session_start.js` | Ramped concurrent session start and immediate close cleanup. |
| `agentic` | `scripts/agentic_ai_reads.js` | Read-only Agentic AI dashboard/API load. |
| `audit` | `scripts/audit_reads.js` | Read-only audit facade load with bounded list sizes. |

## Safety Notes

- The WebSocket tests send only bounded terminal protocol packets.
- The default terminal input is `printf ddm-k6\n`.
- Session close runs in `finally` blocks so capacity is released even after a
  failed check.
- Agentic AI scripts do not call approve, reject, remediation, automation run,
  Kubernetes, Terraform, or shell-command endpoints.
- The suite does not store or generate Cognito credentials.

## Thresholds

Smoke defaults:

- all checks pass
- no HTTP request failures
- p95 HTTP latency below 1 second
- WebSocket upgrade succeeds

Load defaults:

- check rate above 99%
- HTTP failure rate below 1%
- p95 session start latency below 2 seconds
- WebSocket connection success above 99%

Stress defaults:

- check rate above 95%
- HTTP failure rate below 5%
- p95 session start latency below 5 seconds
- fewer than five close failures

Tune `K6_VUS`, `K6_DURATION`, `K6_RAMP_UP`, and `K6_RAMP_DOWN` conservatively
for AWS dev. These scripts exercise real sessions and consume dataplane
capacity.
