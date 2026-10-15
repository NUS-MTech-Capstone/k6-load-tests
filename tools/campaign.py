#!/usr/bin/env python3
"""Opt-in bounded AWS campaign. Secrets never enter artifacts or command arguments."""
import argparse
import collections
import hashlib
import datetime as dt
import http.server
import json
import os
import math
from pathlib import Path
import secrets
import signal
import shutil
import subprocess
import threading
import time

from aws_support import Api, ACCOUNT, BASE, REGION, CONTEXT, aws, kube, items, utcnow, command

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ('proxy-dataplane', 'proxy-controlplane', 'session-manager', 'audit-service', 'sts')


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, indent=2) + '\n')
    temp.replace(path)


def source_hashes():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ('lib', 'scripts', 'tools') for p in (ROOT / folder).glob('*') if p.is_file()}


def parse_time(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00'))


def snapshot():
    result = []
    for ns in ('dendenmushi-ssh', 'dendenmushi-webapp', 'dendenmushi-agentic'):
        for p in kube('-n', ns, 'get', 'pods')['items']:
            cs = p.get('status', {}).get('containerStatuses', [])
            if p.get('status', {}).get('phase') == 'Succeeded':
                continue
            result.append({'namespace': ns, 'name': p['metadata']['name'], 'uid': p['metadata']['uid'],
                'ready': bool(cs) and all(c.get('ready', False) for c in cs),
                'containers': [{'name': c['name'], 'image': c['image'], 'digest': c.get('imageID'),
                    'restarts': c.get('restartCount', 0)} for c in cs]})
    return sorted(result, key=lambda p: (p['namespace'], p['name']))


def capacity_snapshot():
    data = json.loads(command(['kubectl', '--context', CONTEXT, '-n', 'dendenmushi-ssh',
        'exec', 'deploy/proxy-controlplane', '-c', 'proxy-controlplane', '--', 'wget', '-T', '10', '-qO-',
        '--header=x-api-version: 1', 'http://session-manager:8081/proxy-pods/available']))
    if not isinstance(data, list) or not data:
        raise RuntimeError('No fresh available dataplane capacity')
    return [{k: row[k] for k in ('podId', 'instanceId', 'status', 'activeSessions', 'maxSessions', 'lastHeartbeatAt')} for row in data]


def verify_capacity_release(active, created, timeout=120):
    started = time.monotonic()
    result = {'capacityReleased': False, 'remainingCampaignSessions': None, 'capacity': [], 'error': None}
    try:
        while True:
            capacities = capacity_snapshot()
            current = active()
            remaining = [s for s in current if s.get('sessionId') in created]
            result.update(capacity=capacities, remainingCampaignSessions=len(remaining),
                capacityReleased=not remaining and sum(p['activeSessions'] for p in capacities) <= len(current))
            if result['capacityReleased'] or time.monotonic() - started >= timeout:
                break
            time.sleep(min(10, max(0, timeout - (time.monotonic() - started))))
    except Exception as e:
        result['error'] = 'recovery_monitoring_failure:' + type(e).__name__
    result['recoverySeconds'] = time.monotonic() - started
    return result


def health_change(before, after):
    if any(not p['ready'] for p in after):
        return 'workload_not_ready'
    if before != after:
        return 'deployment_or_restart_changed'
    return None


def resource_queries(dashboard):
    queries = []
    for service in SERVICES:
        for resource in ('cpu', 'memory'):
            dims = {'ClusterName': 'dendenmushi-cluster-dev', 'Namespace': 'dendenmushi-ssh', 'PodName': service, 'ContainerName': service}
            queries.append({'Id': f'q{len(queries)}', 'Label': f'{service}_{resource}', 'MetricStat': {'Metric': {
                'Namespace': 'ContainerInsights', 'MetricName': f'container_{resource}_utilization_over_container_limit',
                'Dimensions': [{'Name': k, 'Value': v} for k, v in dims.items()]}, 'Period': 60, 'Stat': 'Maximum'}, 'ReturnData': True})
    target = aws('cloudwatch', 'list-metrics', '--namespace', 'DendenMushi/TargetHost', '--metric-name', 'cpu_usage_idle')['Metrics']
    target = [m for m in target if {d['Name']: d['Value'] for d in m['Dimensions']}.get('cpu') == 'cpu-total'
              and {d['Name']: d['Value'] for d in m['Dimensions']}.get('HostId') == 'host-dev-1']
    if len(target) != 1:
        raise RuntimeError('Target telemetry ambiguous or unavailable')
    queries.append({'Id': 'target_cpu', 'Label': 'target_cpu_idle', 'MetricStat': {'Metric': target[0], 'Period': 60, 'Stat': 'Minimum'}, 'ReturnData': True})
    instance = next(d['Value'] for d in target[0]['Dimensions'] if d['Name'] == 'InstanceId')
    memory = aws('cloudwatch', 'list-metrics', '--namespace', 'DendenMushi/TargetHost', '--metric-name', 'mem_used_percent')['Metrics']
    memory = [m for m in memory if {d['Name']: d['Value'] for d in m['Dimensions']}.get('InstanceId') == instance]
    if len(memory) != 1:
        raise RuntimeError('Target memory telemetry ambiguous or unavailable')
    queries.append({'Id': 'target_memory', 'Label': 'target_memory', 'MetricStat': {'Metric': memory[0], 'Period': 60, 'Stat': 'Maximum'}, 'ReturnData': True})
    widget = next(w for w in dashboard['widgets'] if w.get('properties', {}).get('title') == 'RDS PostgreSQL')
    metric = widget['properties']['metrics'][0]
    queries.append({'Id': 'rds_cpu', 'Label': 'rds_cpu', 'MetricStat': {'Metric': {'Namespace': metric[0], 'MetricName': metric[1],
        'Dimensions': [{'Name': metric[2], 'Value': metric[3]}]}, 'Period': 60, 'Stat': 'Maximum'}, 'ReturnData': True})
    return queries


def fetch_resources(queries):
    now = utcnow()
    return aws('cloudwatch', 'get-metric-data', '--metric-data-queries', json.dumps(queries),
               '--start-time', (now - dt.timedelta(minutes=10)).isoformat(), '--end-time', now.isoformat(), '--scan-by', 'TimestampDescending')


def resource_violation(data, now=None, expected_ids=None):
    now = now or utcnow()
    if not data.get('MetricDataResults'):
        return 'telemetry_unavailable:empty_response'
    rows = data.get('MetricDataResults', [])
    if expected_ids is not None and {r.get('Id') for r in rows} != set(expected_ids):
        return 'telemetry_unavailable:missing_or_duplicate_series'
    if len({r.get('Id') for r in rows}) != len(rows):
        return 'telemetry_unavailable:duplicate_series'
    for row in rows:
        if len(row.get('Values', [])) != len(row.get('Timestamps', [])) or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in row.get('Values', [])):
            return 'telemetry_unavailable:malformed_values'
        if row.get('StatusCode') != 'Complete' or not row.get('Timestamps') or (now - parse_time(row['Timestamps'][0])).total_seconds() > 180:
            return 'telemetry_unavailable:' + row.get('Label', row['Id'])
        values = row['Values'][:2]
        if row['Id'] == 'target_cpu':
            values = [100 - v for v in values]
        limit = 80 if row['Label'].endswith('_memory') else 85
        consecutive = len(row['Timestamps']) >= 2 and (parse_time(row['Timestamps'][0]) - parse_time(row['Timestamps'][1])).total_seconds() == 60
        if consecutive and len(values) == 2 and all(v > limit for v in values):
            return 'resource_limit:' + row['Label']
    return None


class Coordinator:
    def __init__(self, api, directory, ceiling, expected=0, seconds=0, ramp=0):
        self.api, self.directory, self.ceiling = api, directory, ceiling
        self.token = secrets.token_hex(24)
        self.lock = threading.Lock()
        self.stop = None
        self.expected, self.seconds = expected, seconds
        self.ready = set()
        self.not_before = time.time() + ramp
        self.plateau_start = None
        self.plateau_end = None
        self.plateau_broken = False
        self.probe_counts = collections.Counter()
        self.next_start = 0
        self.reserved = 0
        self.created = set()
        self.closed = set()
        self.connected = set()
        self.events = collections.deque()
        self.max_connected = 0
        self.http_samples = collections.deque()
        self.journal = directory / 'events.jsonl'

    def event(self, data):
        safe_keys = {'kind', 'sessionId', 'vu', 'ok', 'latency', 'phase', 'unexpected', 'status', 'reason'}
        row = {k: v for k, v in data.items() if k in safe_keys}
        row['timestamp'] = utcnow().isoformat()
        if row.get('kind') == 'opened': self.connected.add(row['sessionId'])
        if row.get('kind') == 'ready':
            self.ready.add(row['sessionId'])
            if self.expected and len(self.ready & self.connected) == self.expected and self.plateau_start is None:
                self.plateau_start = max(time.time() + 1, self.not_before)
                self.plateau_end = self.plateau_start + self.seconds
        if row.get('kind') == 'probe' and row.get('phase') == 'steady':
            self.probe_counts[row.get('vu')] += 1
        if row.get('kind') == 'disconnected' and self.plateau_start is not None and time.time() < self.plateau_end:
            self.plateau_broken = True
        if row.get('kind') == 'disconnected': self.connected.discard(row['sessionId'])
        self.max_connected = max(self.max_connected, len(self.connected))
        row['connected'] = len(self.connected)
        self.events.append((time.monotonic(), row))
        while self.events and self.events[0][0] < time.monotonic() - 60: self.events.popleft()
        if row.get('kind') in ('authorization_failure', 'start_failure', 'ws_error') or row.get('unexpected'):
            self.stop = row['kind']
        with self.journal.open('a') as f: f.write(json.dumps(row) + '\n')

    def state(self):
        return {'plateauStartMs': None if self.plateau_start is None else self.plateau_start * 1000,
                'plateauEndMs': None if self.plateau_end is None else self.plateau_end * 1000, 'stop': self.stop}

    def plateau_result(self):
        if not self.expected:
            return None
        minimum = max(1, int(self.seconds / 2 * .98))
        complete = (self.plateau_end is not None and time.time() >= self.plateau_end and
                    not self.plateau_broken and len(self.ready) == self.expected and
                    len(self.probe_counts) == self.expected and all(n >= minimum for n in self.probe_counts.values()))
        return {'complete': complete, 'requested': self.expected, 'ready': len(self.ready),
                'startEpoch': self.plateau_start, 'endEpoch': self.plateau_end,
                'broken': self.plateau_broken, 'minimumProbesPerSession': minimum,
                'probeCounts': dict(self.probe_counts)}

    def permit(self):
        with self.lock:
            if self.stop or self.reserved >= self.ceiling: return False
            wait = max(0, self.next_start - time.monotonic())
            self.next_start = max(time.monotonic(), self.next_start) + .5
            self.reserved += 1
        time.sleep(wait)
        return not self.stop

    def cleanup(self):
        failed = []
        for sid in sorted(self.created - self.closed):
            status, _ = self.api.request(f'/sessions/{sid}/close', 'POST', {})
            if 200 <= status < 300: self.closed.add(sid)
            else: failed.append(sid)
        save(self.directory / 'cleanup.json', {'created': sorted(self.created), 'closed': sorted(self.closed), 'closeFailures': failed})
        return not failed

    def guard(self):
        with self.lock:
            probes = [x for _, x in self.events if x.get('kind') == 'probe']
            if len(probes) >= 20:
                if sum(not p['ok'] for p in probes) / len(probes) > .01: return 'probe_failure_rate'
                latencies = sorted(p['latency'] for p in probes if p['ok'])
                if latencies and latencies[min(len(latencies)-1, int(len(latencies)*.95))] > 1000: return 'echo_p95_above_1s'
            while self.http_samples and self.http_samples[0][0] < time.monotonic() - 60: self.http_samples.popleft()
            if len(self.http_samples) >= 20 and sum(x[1] for x in self.http_samples) / len(self.http_samples) > .05: return 'http_failure_rate'
        return self.stop

    def server(self):
        owner = self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                # Read-only, credential-free progress page on loopback.
                data = {'created': len(owner.created), 'closed': len(owner.closed), 'connected': len(owner.connected),
                        'run': owner.directory.name, 'peakConnected': owner.max_connected,
                        'remoteRequestsLastMinute': len(owner.http_samples),
                        'stopReason': owner.stop, 'timestamp': utcnow().isoformat()}
                page = '<html><meta http-equiv="refresh" content="5"><title>k6 campaign progress</title><body style="font:24px system-ui;padding:48px"><h1>DenDenMushi k6 campaign</h1><pre>' + json.dumps(data, indent=2) + '</pre><p>Guarded ceiling: 40 sessions. Session creation: at most 2/s.</p></body></html>'
                self.send_response(200); self.send_header('Content-Type', 'text/html'); self.end_headers(); self.wfile.write(page.encode())
            def do_POST(self):
                if self.headers.get('Authorization') != 'Bearer ' + owner.token:
                    self.send_response(403); self.end_headers(); return
                length = int(self.headers.get('Content-Length', 0))
                if length > 2048:
                    self.send_response(413); self.end_headers(); return
                data = json.loads(self.rfile.read(length) or '{}')
                ok = True
                if self.path == '/permit': ok = owner.permit()
                else:
                    with owner.lock:
                        if self.path == '/state': pass
                        elif self.path == '/registered':
                            owner.created.add(data['sessionId'])
                            owner.event({'kind': 'registered', 'sessionId': data['sessionId']})
                        elif self.path == '/closed':
                            if data.get('ok'):
                                owner.closed.add(data['sessionId']); owner.reserved = max(0, owner.reserved - 1)
                            else: owner.stop = 'cleanup_failure'
                            owner.event({'kind': 'closed', **data})
                        elif self.path == '/event': owner.event(data)
                        else: ok = False
                self.send_response(200 if ok else 409); self.end_headers(); self.wfile.write(json.dumps(owner.state()).encode())
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server


def follow_metrics(path, coordinator, finished):
    while not path.exists() and not finished.is_set(): time.sleep(.1)
    if not path.exists(): return
    with path.open() as f:
        while not finished.is_set():
            line = f.readline()
            if not line:
                time.sleep(.1); continue
            try: row = json.loads(line)
            except ValueError: continue
            if row.get('type') == 'Point' and row.get('metric') == 'http_req_failed' and row['data'].get('tags', {}).get('surface') == 'api':
                with coordinator.lock: coordinator.http_samples.append((time.monotonic(), row['data']['value']))


def availability_probes(cookie, directory, coordinator, finished):
    api = Api(); api.cookie = cookie
    next_tick = time.monotonic()
    while not finished.is_set():
        start = time.monotonic(); status, body = api.request('/ddm/auth/me')
        row = {'timestamp': utcnow().isoformat(), 'status': status,
               'ok': status == 200 and isinstance(body, dict),
               'latencyMs': (time.monotonic() - start) * 1000, 'clientConnected': len(coordinator.connected)}
        with (directory / 'availability.jsonl').open('a') as f: f.write(json.dumps(row) + '\n')
        if not row['ok']: coordinator.stop = 'authenticated_probe_failed'
        next_tick += 5
        finished.wait(max(0, next_tick - time.monotonic()))


def finalize_status(directory, exit_code, abort=None):
    summary = json.loads((directory / 'summary.json').read_text()) if (directory / 'summary.json').exists() else None
    failed = [] if summary is None else [f'{name}: {expr}' for name, metric in summary.get('metrics', {}).items()
        for expr, threshold in metric.get('thresholds', {}).items() if not threshold.get('ok', False)]
    status = 'ABORTED' if abort else ('PASSED' if exit_code == 0 and summary and not failed else 'FAILED')
    result = {'status': status, 'exitCode': exit_code, 'abortReason': abort, 'failedThresholds': failed}
    save(directory / 'result.json', result)
    report = directory / 'report.html'
    if report.exists():
        content = report.read_text().replace('>THRESHOLDS PASSED<', f'>{status}<')
        if status != 'PASSED': content = content.replace('>PASSED<', f'>{status}<').replace('status passed', 'status failed')
        report.write_text(content)
    return result


class Campaign:
    def __init__(self, args):
        self.args = args
        self.dir = Path(args.output).resolve(); self.dir.mkdir(parents=True, exist_ok=False)
        self.api = Api(); self.api.login()
        if aws('sts', 'get-caller-identity')['Account'] != ACCOUNT: raise RuntimeError('Wrong AWS account')
        self.dashboard = json.loads(aws('cloudwatch', 'get-dashboard', '--dashboard-name', 'dendenmushi-dev-application-observability')['DashboardBody'])
        save(self.dir / 'dashboard.json', self.dashboard)
        self.queries = resource_queries(self.dashboard)
        save(self.dir / 'resource-queries.json', self.queries)
        self.runs = []
        self.initial = snapshot(); save(self.dir / 'deployment-before.json', self.initial)
        if any(not p['ready'] for p in self.initial): raise RuntimeError('Unhealthy baseline')
        self.record = {'startedAt': utcnow().isoformat(), 'account': ACCOUNT, 'region': REGION, 'origin': BASE, 'hostId': args.host,
            'crId': args.cr, 'ceiling': 40, 'generator': {'platform': os.uname().sysname, 'machine': os.uname().machine,
            'k6': command(['k6', 'version']).strip()}, 'sourceHashes': source_hashes(), 'revisions': {}, 'runs': self.runs}
        self.runner_source = {str(p.relative_to(ROOT)): p.read_bytes() for p in (ROOT / 'tools').glob('*.py')}
        self.record['revisions']['k6-load-tests'] = {
            'sha': command(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).strip(),
            'dirty': bool(command(['git', '-C', str(ROOT), 'status', '--porcelain']))}
        if self.record['revisions']['k6-load-tests']['dirty']:
            raise RuntimeError('Campaign requires a clean committed harness')
        applications = kube('-n', 'argocd', 'get', 'applications')
        self.record['gitops'] = [{'name': a['metadata']['name'], 'revision': a.get('status', {}).get('sync', {}).get('revision'),
            'sync': a.get('status', {}).get('sync', {}).get('status'), 'health': a.get('status', {}).get('health', {}).get('status')}
            for a in applications['items']]
        save(self.dir / 'workload-config.json', [
            {'namespace': ns, 'deployments': [{'name': d['metadata']['name'], 'replicas': d['spec']['replicas'],
             'containers': [{'name': c['name'], 'image': c['image'], 'resources': c.get('resources', {})}
                for c in d['spec']['template']['spec']['containers']]} for d in kube('-n', ns, 'get', 'deployments')['items']]}
            for ns in ('dendenmushi-ssh', 'dendenmushi-webapp', 'dendenmushi-agentic')])
        save(self.dir / 'campaign.json', self.record)
        for relative in self.record['sourceHashes']:
            destination = self.dir / 'source' / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)

    def access(self, duration):
        cr = self.api.get('/ddm/change-requests/' + self.args.cr)
        now = utcnow()
        if cr.get('status') != 'Approved' or not cr.get('vmIds') or not (parse_time(cr['implementationStartAt']) <= now < parse_time(cr['implementationEndAt']) - dt.timedelta(seconds=duration + 120)):
            raise RuntimeError('Approved scoped change window does not cover this run')
        scoped = items(self.api.get('/ddm/change-requests/accessible'))
        if not any(r.get('id') == self.args.cr and any(v.get('hostname') == self.args.host for v in r.get('vms', [])) for r in scoped):
            raise RuntimeError('Host absent from accessible approved scope')
        return cr

    def active(self):
        data = self.api.get('/sessions?status=ACTIVE&limit=100')
        rows = items(data)
        if len(rows) >= 100:
            raise RuntimeError('Session inventory may be truncated')
        return rows

    def run(self, name, script, vus, seconds, ramp=0):
        is_read = script in ('agentic_ai_reads', 'audit_reads')
        ramp = 30 if script == 'stress_session_start' else ramp
        ramp_down = 30 if script == 'stress_session_start' else 0
        if not is_read:
            self.access(seconds + ramp + ramp_down)
            target_metric = next(q['MetricStat']['Metric'] for q in self.queries if q['Id'] == 'target_cpu')
            instance = next(d['Value'] for d in target_metric['Dimensions'] if d['Name'] == 'InstanceId')
            target = aws('ec2', 'describe-instance-status', '--instance-ids', instance, '--include-all-instances')['InstanceStatuses']
            if len(target) != 1 or any(target[0].get(key, {}).get('Status') != 'ok' for key in ('InstanceStatus', 'SystemStatus')):
                raise RuntimeError('Target EC2 health checks are not healthy')
        baseline = snapshot()
        violation = health_change(self.initial, baseline)
        if violation: raise RuntimeError(violation)
        config = kube('-n', 'dendenmushi-ssh', 'get', 'configmap', 'dendenmushi-config')
        limit = int(config['data']['MAX_SESSIONS'])
        active = self.active()
        if not is_read and any(s.get('crId') == self.args.cr for s in active):
            raise RuntimeError('Dedicated campaign CR already has active sessions; refusing ambiguous cleanup ownership')
        capacity = min(40, limit - 10 - len(active))
        if capacity < vus and script not in ('agentic_ai_reads', 'audit_reads'):
            raise RuntimeError('Insufficient capacity after reserving ten slots')
        metrics = fetch_resources(self.queries)
        violation = resource_violation(metrics, expected_ids=[q['Id'] for q in self.queries])
        if violation: raise RuntimeError(violation)
        directory = self.dir / name; directory.mkdir()
        # Save the loaded runner and exact k6 modules for this repetition.
        for folder in ('lib', 'scripts'):
            for source in (ROOT / folder).glob('*'):
                if source.is_file():
                    dest = directory / 'source' / source.relative_to(ROOT)
                    dest.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, dest)
        for relative, content in self.runner_source.items():
            dest = directory / 'source' / relative
            dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(content)
        save(directory / 'resources-before.json', metrics)
        save(directory / 'deployment-before.json', baseline)
        save(directory / 'capacity-before.json', capacity_snapshot())
        coordinator = Coordinator(self.api, directory, max(1, capacity),
            vus if script in ('terminal_capacity', 'terminal_soak') else 0, seconds, ramp)
        server = coordinator.server()
        record = {'name': name, 'script': script, 'vus': vus, 'measureSeconds': seconds, 'rampSeconds': ramp,
            'sourceHashes': source_hashes(), 'executableCeiling': capacity,
            'stages': [{'name': 'ramp', 'durationSeconds': ramp}, {'name': 'measurement', 'durationSeconds': seconds}, {'name': 'ramp-down', 'durationSeconds': ramp_down}],
            'startedAt': utcnow().isoformat(), 'baselineActiveSessions': len(active)}
        save(directory / 'run.json', record)
        env = {**os.environ, 'BASE_URL': BASE, 'DDM_ALB_COOKIE_HEADER': coordinator.api.cookie, 'HOST_ID': self.args.host, 'CR_ID': self.args.cr,
            'K6_WS_COMMAND': '\x15ddm-k6-smoke\x15',
            'K6_VUS': str(vus), 'K6_DURATION': f'{seconds}s', 'K6_RAMP_UP': '30s', 'K6_RAMP_DOWN': '30s',
            'K6_MEASURE_SECONDS': str(seconds), 'K6_RAMP_SECONDS': str(ramp), 'K6_RUN_NONCE': secrets.token_hex(3),
            'K6_ARTIFACT_DIR': str(directory), 'K6_REPORT_NAME': name, 'K6_COORDINATOR_URL': f'http://127.0.0.1:{server.server_port}',
            'K6_COORDINATOR_TOKEN': coordinator.token, 'K6_SYSTEM_TAGS': 'status,method,name,group,check,error_code,scenario,expected_response',
            'K6_SUMMARY_TREND_STATS': 'avg,min,med,max,p(50),p(95),p(99),count'}
        env.pop('DDM_AUTH_HEADERS_JSON', None)
        env.pop('K6_HTTP_DEBUG', None)
        # Do not persist environment, cookies, request headers, or response bodies.
        print(f'START {name}: {vus} VUs, {ramp}s ramp + {seconds}s measurement; progress http://127.0.0.1:{server.server_port}', flush=True)
        finished = threading.Event(); abort = None
        with (directory / 'k6.log').open('w') as log:
            proc = subprocess.Popen(['k6', 'run', '--out', f'json={directory / "metrics.jsonl"}', f'scripts/{script}.js'],
                cwd=ROOT, env=env, stdout=log, stderr=log)
            threading.Thread(target=follow_metrics, args=(directory / 'metrics.jsonl', coordinator, finished), daemon=True).start()
            probe_thread = threading.Thread(target=availability_probes, args=(self.api.cookie, directory, coordinator, finished), daemon=True)
            probe_thread.start()
            def stop_signal(signum, frame):
                coordinator.stop = 'interrupted'
            old = {s: signal.signal(s, stop_signal) for s in (signal.SIGINT, signal.SIGTERM)}
            next_resources = 0
            try:
                deadline = time.monotonic() + ramp + seconds + ramp_down + 120
                while proc.poll() is None:
                    tick = time.monotonic()
                    if tick >= next_resources:
                        after = snapshot(); abort = abort or health_change(baseline, after)
                        m = fetch_resources(self.queries)
                        with (directory / 'resources.jsonl').open('a') as f: f.write(json.dumps({'timestamp': utcnow().isoformat(), 'data': m}) + '\n')
                        abort = abort or resource_violation(m, expected_ids=[q['Id'] for q in self.queries])
                        current = self.active()
                        owned = [s for s in current if s.get('sessionId') in coordinator.created]
                        with (directory / 'inventory.jsonl').open('a') as f: f.write(json.dumps({'timestamp': utcnow().isoformat(), 'allActive': len(current), 'campaignActive': len(owned)}) + '\n')
                        if len(current) > limit - 10: abort = 'capacity_reserve_consumed'
                        generator = command(['ps', '-p', str(proc.pid), '-o', 'pcpu=,rss=']).strip().split() if proc.poll() is None else []
                        with (directory / 'generator.jsonl').open('a') as f:
                            f.write(json.dumps({'timestamp': utcnow().isoformat(), 'cpuPercentOneCore': float(generator[0]) if generator else None, 'rssKiB': int(generator[1]) if generator else None, 'logicalCPUs': os.cpu_count()}) + '\n')
                        next_resources = tick + 30
                    abort = abort or coordinator.guard()
                    if time.monotonic() > deadline: abort = abort or 'run_deadline'
                    if abort:
                        coordinator.stop = abort; proc.send_signal(signal.SIGINT); break
                    time.sleep(max(0, 5 - (time.monotonic() - tick)))
                try: exit_code = proc.wait(timeout=30)
                except subprocess.TimeoutExpired: proc.kill(); exit_code = proc.wait()
            except Exception as e:
                abort = 'monitoring_failure:' + type(e).__name__
                proc.send_signal(signal.SIGINT)
                try: exit_code = proc.wait(timeout=30)
                except subprocess.TimeoutExpired: proc.kill(); exit_code = proc.wait()
            finally:
                for s, previous in old.items(): signal.signal(s, previous)
                finished.set()
                probe_thread.join(timeout=20)
                cleanup_ok = coordinator.cleanup()
                server.shutdown()
        if not cleanup_ok: abort = abort or 'cleanup_failure'
        abort = abort or health_change(baseline, snapshot())
        # Unknown IDs cannot be proved to belong to this process. Surface them
        # for review rather than closing another client's session on a shared CR.
        leftovers = [] if is_read else [s for s in self.active() if s.get('crId') == self.args.cr]
        unknown = [s.get('sessionId') for s in leftovers if s.get('sessionId') not in coordinator.created]
        if unknown: abort = abort or 'unattributed_sessions_require_review'
        recovery = verify_capacity_release(self.active, coordinator.created)
        recovery.update(unattributedSessionIds=unknown)
        save(directory / 'recovery.json', recovery)
        if recovery['remainingCampaignSessions']: abort = abort or 'sessions_remain'
        if not recovery['capacityReleased']: abort = abort or recovery['error'] or 'capacity_not_released'
        plateau = coordinator.plateau_result()
        if plateau and not plateau['complete']:
            abort = abort or 'incomplete_concurrency_plateau'
        record.update(endedAt=utcnow().isoformat(), maxClientConnected=coordinator.max_connected, plateau=plateau)
        record.update(finalize_status(directory, exit_code, abort))
        save(directory / 'run.json', record); self.runs.append(record); save(self.dir / 'campaign.json', self.record)
        print(f'END {name}: {record["status"]}, peak connections={record["maxClientConnected"]}', flush=True)
        if record['status'] == 'ABORTED':
            raise RuntimeError(record['abortReason'])
        only_latency = all(x.startswith(('ddm_terminal_echo_ms', 'ddm_session_start_duration', 'http_req_duration')) for x in record['failedThresholds'])
        if record['status'] != 'PASSED' and not (record['exitCode'] == 99 and record['failedThresholds'] and only_latency):
            raise RuntimeError('Run failed: ' + name)
        # One-minute minimum cooldown; fail closed if no recovery within five minutes.
        for attempt in range(17):
            time.sleep(60 if attempt == 0 else 15)
            resources = fetch_resources(self.queries)
            reason = resource_violation(resources, expected_ids=[q['Id'] for q in self.queries]) or health_change(self.initial, snapshot())
            with (directory / 'cooldown.jsonl').open('a') as f:
                f.write(json.dumps({'timestamp': utcnow().isoformat(), 'reason': reason, 'data': resources}) + '\n')
            if not reason: return record['status'] == 'PASSED'
        raise RuntimeError('Resources did not recover within five minutes')

    def baseline(self):
        self.access(300 + 120)
        for index in range(11):
            resources = fetch_resources(self.queries)
            reason = resource_violation(resources, expected_ids=[q['Id'] for q in self.queries]) or health_change(self.initial, snapshot())
            with (self.dir / 'baseline.jsonl').open('a') as f:
                f.write(json.dumps({'timestamp': utcnow().isoformat(), 'reason': reason, 'data': resources}) + '\n')
            if reason: raise RuntimeError(reason)
            if index < 10: time.sleep(30)

    def execute(self):
        if self.args.reads_only:
            for script in (self.args.read_profiles or ('agentic_ai_reads', 'audit_reads')):
                for rep in range(self.args.first_repetition, self.args.first_repetition + self.args.repetitions):
                    if not self.run(f'{script}-r{rep}', script, 10, 300): return
            return
        self.baseline()
        if not self.run('00-smoke', 'smoke', 1, 15): return
        if self.args.smoke_only: return
        config = kube('-n', 'dendenmushi-ssh', 'get', 'configmap', 'dendenmushi-config')
        ceiling = max(0, min(40, int(config['data']['MAX_SESSIONS']) - 10 - len(self.active())))
        self.record['executableCeiling'] = ceiling
        if ceiling == 0: raise RuntimeError('No capacity available after reserving ten slots')
        levels = sorted(set([x for x in (5, 10, 20, 30, 40) if x <= ceiling] + [ceiling]))
        highest = 0
        for level in levels:
            if not self.run(f'capacity-{level}-r1', 'terminal_capacity', level, 300, 60): break
            highest = level
        if highest:
            for level in sorted(set([min(20, highest), highest])):
                for rep in (2, 3):
                    if not self.run(f'capacity-{level}-r{rep}', 'terminal_capacity', level, 300, 60): return
            if not self.run(f'soak-{highest}', 'terminal_soak', highest, 1200, 60): return
        for rep in (1, 2, 3):
            if not self.run(f'lifecycle-r{rep}', 'session_lifecycle', 5, 180): return
        if not self.run('start-stress', 'stress_session_start', 5, 120): return
        if self.args.ssh_only:
            self.record['plannedCompleted'] = True
            return
        for script in ('agentic_ai_reads', 'audit_reads'):
            for rep in (1, 2, 3):
                if not self.run(f'{script}-r{rep}', script, 10, 300): return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cr', default=''); parser.add_argument('--host', default='host-dev-1')
    parser.add_argument('--output', required=True); parser.add_argument('--smoke-only', action='store_true')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--reads-only', action='store_true')
    mode.add_argument('--ssh-only', action='store_true')
    parser.add_argument('--read-profiles', nargs='+', choices=('agentic_ai_reads', 'audit_reads'))
    parser.add_argument('--repetitions', type=int, choices=(1, 2, 3), default=3)
    parser.add_argument('--first-repetition', type=int, choices=(1, 2, 3), default=1)
    args = parser.parse_args()
    if not args.cr and not args.reads_only: parser.error('--cr is required for SSH workloads')
    if Path(args.output).exists(): parser.error('Output directory must not already exist')
    campaign = None
    try:
        campaign = Campaign(args)
        campaign.execute()
    except Exception as e:
        # Exceptions from our own checks contain no remote body or credentials.
        reason = str(e) if isinstance(e, RuntimeError) else type(e).__name__
        save(Path(args.output) / 'blocked.json', {'timestamp': utcnow().isoformat(), 'reason': reason})
        print('Campaign stopped:', reason, flush=True)
        return 1
    finally:
        if campaign:
            campaign.record['endedAt'] = utcnow().isoformat()
            save(campaign.dir / 'campaign.json', campaign.record)
    return 0 if campaign and campaign.runs and all(r['status'] == 'PASSED' for r in campaign.runs) else 1


if __name__ == '__main__':
    raise SystemExit(main())
