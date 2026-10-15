#!/usr/bin/env python3
"""Export native CloudWatch chart images and underlying data for a fixed window."""
import argparse
import base64
import json
from pathlib import Path
import re
from aws_support import aws, REGION
from campaign import save, resource_queries

TITLES = ('Application Pod CPU', 'Application Pod Memory', 'Pod Restarts And Network',
          'ALB Traffic And Errors', 'ALB Target Health', 'RDS PostgreSQL', 'RDS I/O',
          'Incident Alert Queue', 'Target Host CPU And Memory', 'Target Host Disk And Swap')


def metric_queries(properties):
    previous = []
    queries = []
    for row in properties['metrics']:
        if isinstance(row[0], dict):
            expression = row[0]
            queries.append({'Id': expression.get('id', f'm{len(queries)}'), 'Expression': expression['expression'],
                            'Label': expression.get('label', ''), 'ReturnData': True, 'Period': 60})
            continue
        opts = row[-1] if isinstance(row[-1], dict) else {}
        raw = row[:-1] if isinstance(row[-1], dict) else row
        expanded = [previous[i] if v == '.' else v for i, v in enumerate(raw)]
        previous = expanded
        queries.append({'Id': opts.get('id', f'm{len(queries)}'), 'Label': opts.get('label', expanded[1]), 'ReturnData': True,
                        'MetricStat': {'Metric': {'Namespace': expanded[0], 'MetricName': expanded[1],
                            'Dimensions': [{'Name': expanded[i], 'Value': expanded[i+1]} for i in range(2, len(expanded), 2)]},
                            'Period': 60, 'Stat': opts.get('stat', properties.get('stat', 'Average'))}})
    return queries


def export(directory, start, end):
    directory.mkdir(parents=True, exist_ok=True)
    dashboard = json.loads(aws('cloudwatch', 'get-dashboard', '--dashboard-name', 'dendenmushi-dev-application-observability')['DashboardBody'])
    save(directory / 'dashboard-source.json', dashboard)
    manifest = []
    for widget in dashboard['widgets']:
        props = widget.get('properties', {})
        title = props.get('title')
        if title not in TITLES: continue
        slug = re.sub('[^a-z0-9]+', '-', title.lower()).strip('-')
        props = {**props, 'width': 1400, 'height': 600, 'start': start, 'end': end, 'period': 60, 'timezone': '+0800', 'region': REGION}
        queries = metric_queries(props)
        data = aws('cloudwatch', 'get-metric-data', '--metric-data-queries', json.dumps(queries), '--start-time', start, '--end-time', end, '--scan-by', 'TimestampAscending')
        save(directory / (slug + '-data.json'), data)
        save(directory / (slug + '-widget.json'), props)
        result = aws('cloudwatch', 'get-metric-widget-image', '--metric-widget', json.dumps(props), '--output-format', 'png')
        (directory / (slug + '.png')).write_bytes(base64.b64decode(result['MetricWidgetImage']))
        series = data.get('MetricDataResults', [])
        manifest.append({'title': title, 'image': slug + '.png', 'start': start, 'end': end,
                         'nonemptySeries': sum(bool(x.get('Values')) for x in series),
                         'emptySeries': [x.get('Label', x['Id']) for x in series if not x.get('Values')]})
        print('Exported', title, flush=True)
    save(directory / 'manifest.json', manifest)
    # Preserve the existing dashboard verbatim, then supplement its missing or
    # node-relative series with exact published dimensions. No dashboard writes.
    queries = resource_queries(dashboard)
    targets = aws('cloudwatch', 'list-metrics', '--namespace', 'DendenMushi/TargetHost')['Metrics']
    save(directory / 'published-target-dimensions.json', targets)
    for resource in ('mem_used_percent', 'disk_used_percent', 'swap_used_percent'):
        matches = [m for m in targets if m['MetricName'] == resource
                   and {d['Name']: d['Value'] for d in m['Dimensions']}.get('HostId') == 'host-dev-1']
        for metric in matches:
            queries.append({'Id': f'target{len(queries)}', 'Label': resource, 'ReturnData': True,
                            'MetricStat': {'Metric': metric, 'Period': 60, 'Stat': 'Maximum'}})
    for slug, selected in (
        ('ssh-container-cpu-of-limit', [q for q in queries if q['Label'].endswith('_cpu') and q['Id'] != 'rds_cpu']),
        ('ssh-container-memory-of-limit', [q for q in queries if q['Label'].endswith('_memory')]),
        ('target-published-dimensions', [q for q in queries if q['Id'].startswith('target')]),
    ):
        rows = []
        for q in selected:
            metric = q['MetricStat']['Metric']
            rows.append([metric['Namespace'], metric['MetricName'],
                         *[v for d in metric['Dimensions'] for v in (d['Name'], d['Value'])],
                         {'id': q['Id'], 'label': q['Label'], 'stat': q['MetricStat']['Stat']}])
        props = {'title': slug.replace('-', ' ').title(), 'metrics': rows, 'width': 1400, 'height': 600,
                 'start': start, 'end': end, 'period': 60, 'region': REGION, 'timezone': '+0800',
                 'view': 'timeSeries', 'yAxis': {'left': {'min': 0, 'max': 100}}}
        data = aws('cloudwatch', 'get-metric-data', '--metric-data-queries', json.dumps(selected), '--start-time', start, '--end-time', end, '--scan-by', 'TimestampAscending')
        save(directory / (slug + '-data.json'), data); save(directory / (slug + '-widget.json'), props)
        result = aws('cloudwatch', 'get-metric-widget-image', '--metric-widget', json.dumps(props), '--output-format', 'png')
        (directory / (slug + '.png')).write_bytes(base64.b64decode(result['MetricWidgetImage']))
        manifest.append({'title': props['title'], 'image': slug + '.png', 'start': start, 'end': end,
                         'supplement': 'Exact published dimensions; original dashboard unchanged',
                         'emptySeries': [r['Label'] for r in data.get('MetricDataResults', []) if not r.get('Values')]})
    save(directory / 'manifest.json', manifest)
    return manifest


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--output', required=True); p.add_argument('--start', required=True); p.add_argument('--end', required=True)
    a = p.parse_args(); export(Path(a.output), a.start, a.end)
