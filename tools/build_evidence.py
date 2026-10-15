#!/usr/bin/env python3
"""Derive presentation data from saved k6 summaries; never contact AWS."""
import argparse
import datetime as dt
import html
import json
from pathlib import Path
import statistics


def build(root):
    status = json.loads((root / 'evaluation-status.json').read_text()) if (root / 'evaluation-status.json').exists() else {'status': 'REVIEW', 'summary': 'Review each run outcome and the campaign manifests before claiming acceptance.'}
    rows = []
    for p in sorted(root.glob('*/*/run.json')):
        run = json.loads(p.read_text())
        if not (p.parent / 'summary.json').exists(): continue
        metrics = json.loads((p.parent / 'summary.json').read_text())['metrics']
        duration = metrics.get('http_req_duration{surface:api}', metrics.get('http_req_duration', {})).get('values', {})
        probes = [json.loads(x) for x in (p.parent / 'availability.jsonl').read_text().splitlines()] if (p.parent / 'availability.jsonl').exists() else []
        times = [dt.datetime.fromisoformat(x['timestamp']) for x in probes]
        gaps = [(b-a).total_seconds() for a,b in zip(times, times[1:])]
        row = {'path': str(p.parent.relative_to(root)), 'name': run['name'], 'script': run['script'],
               'status': run.get('status', 'RUNNING'), 'vus': run['vus'], 'measureSeconds': run['measureSeconds'],
               'startedAt': run['startedAt'], 'endedAt': run.get('endedAt'),
               'http': {k: duration.get(k) for k in ('p(50)', 'p(95)', 'p(99)', 'count')},
               'requestsPerSecond': metrics.get('ddm_api_requests', metrics.get('http_reqs', {})).get('values', {}).get('rate'),
               'httpFailureRate': metrics.get('http_req_failed{surface:api}', {}).get('values', {}).get('rate'),
               'peakTerminalConnections': run.get('maxClientConnected'),
               'availability': {'count': len(probes), 'successful': sum(x['ok'] for x in probes),
                   'firstAt': probes[0]['timestamp'] if probes else None, 'lastAt': probes[-1]['timestamp'] if probes else None,
                   'gapMinSeconds': min(gaps) if gaps else None, 'gapMaxSeconds': max(gaps) if gaps else None},
               'cleanup': json.loads((p.parent / 'recovery.json').read_text()).get('remainingCampaignSessions') if (p.parent / 'recovery.json').exists() else None}
        for metric in ('ddm_terminal_echo_ms{phase:steady}', 'ddm_session_start_duration', 'ddm_terminal_probe_success{phase:steady}', 'ddm_terminal_probe_timeouts', 'ddm_terminal_probe_disconnects', 'ddm_ws_unexpected_disconnects'):
            if metric in metrics: row[metric] = metrics[metric]['values']
        rows.append(row)
    result = {'generatedAt': dt.datetime.now(dt.timezone.utc).isoformat(), 'runs': rows,
              'stops': [dict(path=str(p.relative_to(root)), **json.loads(p.read_text())) for p in sorted(root.glob('*/blocked.json'))]}
    (root/'results.json').write_text(json.dumps(result, indent=2)+'\n')
    table = ''
    for r in rows:
        v = r['http']
        fmt = lambda x: f'{x:.2f}' if isinstance(x,(float,int)) else '—'
        table += '<tr>'+''.join(f'<td>{html.escape(str(x))}</td>' for x in (
            r['path'], r['status'], r['vus'], v['count'], fmt(v['p(50)']),fmt(v['p(95)']),fmt(v['p(99)']),fmt(r['requestsPerSecond']),r['cleanup']))+'</tr>'
    stops = ''.join(f'<li>{html.escape(s["timestamp"])}: {html.escape(s["reason"])}</li>' for s in result['stops'])
    page = '''<!doctype html><html><meta charset="utf-8"><title>DenDenMushi performance evidence</title><style>
body{font:18px system-ui;background:#f2f5f8;color:#142c43;margin:40px}main{max-width:1440px;margin:auto}h1{font-size:36px;margin-bottom:8px}.note{background:#fff3cd;padding:18px;border-left:5px solid #b27d00}table{width:100%;border-collapse:collapse;background:white;margin:24px 0}td,th{padding:12px;text-align:left;border-bottom:1px solid #dce2e8}th{font-size:14px;background:#e4edf5}td:first-child{font-size:14px}a{color:#175c91}small{color:#54697a}</style><main>
<h1>DenDenMushi · AWS performance evaluation</h1><small>'''+html.escape(root.name)+''' · AWS dev · Singapore</small>
<p class="note"><b>'''+html.escape(status['status'])+''' evaluation.</b> '''+html.escape(status['summary'])+'''</p>
<h2>Measured runs</h2><p>Latency in milliseconds; request throughput in requests/second. Each row links to a separate workload and repetition in results.json.</p>
<table><tr><th>Run</th><th>Outcome</th><th>VUs</th><th>HTTP samples</th><th>p50 ms</th><th>p95 ms</th><th>p99 ms</th><th>req/s</th><th>Remaining sessions</th></tr>'''+table+'''</table><h2>Recorded stops</h2><ul>'''+stops+'''</ul><p>Missing CloudWatch series are retained as missing. Resource guard percentages use container limits. Availability results describe the recorded probe window only.</p><p><a href="cloudwatch/api-initial/ssh-container-cpu-of-limit.png">Container CPU / limits</a> · <a href="cloudwatch/api-initial/ssh-container-memory-of-limit.png">Container memory / limits</a> · <a href="cloudwatch/api-initial/target-published-dimensions.png">Target resources</a></p></main></html>'''
    (root/'index.html').write_text(page)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('directory'); a=p.parse_args(); x=build(Path(a.directory)); print(f'Derived {len(x["runs"])} run rows')
