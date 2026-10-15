import { summaryFailed } from './echo.mjs';

function artifactPath(filename) {
  const dir = String(__ENV.K6_ARTIFACT_DIR || '').replace(/\/+$/, '');
  if (!dir) {
    return '';
  }
  return `${dir}/${filename}`;
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function formatNumber(value) {
  if (value === undefined || value === null || Number.isNaN(value)) {
    return '-';
  }
  if (!Number.isFinite(value)) {
    return String(value);
  }
  if (Math.abs(value) >= 100) {
    return value.toFixed(2);
  }
  if (Math.abs(value) >= 1) {
    return value.toFixed(3);
  }
  return value.toFixed(4);
}

function formatMetric(metric) {
  if (!metric || !metric.values) {
    return '-';
  }

  const values = metric.values;
  if (metric.type === 'rate' && values.rate !== undefined) {
    return `${formatNumber(values.rate * 100)}%`;
  }
  if (metric.type === 'counter') return `${formatNumber(values.count)} total; ${formatNumber(values.rate)} /s`;
  if (metric.type === 'trend') return `p50=${formatNumber(values['p(50)'] ?? values.med)} p95=${formatNumber(values['p(95)'])} p99=${formatNumber(values['p(99)'])}`;
  if (values.count !== undefined && values.rate === undefined && values.value === undefined) {
    return formatNumber(values.count);
  }
  if (values.value !== undefined) {
    return formatNumber(values.value);
  }
  if (values.avg !== undefined) {
    return `avg=${formatNumber(values.avg)} p95=${formatNumber(values['p(95)'])}`;
  }
  return Object.keys(values)
    .sort()
    .map((key) => `${key}=${formatNumber(values[key])}`)
    .join(' ');
}

function metricRows(metrics) {
  return Object.keys(metrics || {})
    .sort()
    .map((name) => {
      const metric = metrics[name];
      const values = metric.values || {};
      return {
        name,
        type: metric.type || '',
        contains: metric.contains || '',
        summary: formatMetric(metric),
        raw: Object.keys(values)
          .sort()
          .map((key) => `${key}: ${formatNumber(values[key])}`)
          .join('\n'),
      };
    });
}

function thresholdRows(metrics) {
  const rows = [];
  for (const name of Object.keys(metrics || {}).sort()) {
    const metric = metrics[name];
    const thresholds = metric.thresholds || {};
    for (const expression of Object.keys(thresholds).sort()) {
      rows.push({
        metric: name,
        expression,
        ok: thresholds[expression].ok,
      });
    }
  }
  return rows;
}

function buildTextSummary(data) {
  const lines = [];
  const profile = __ENV.K6_REPORT_NAME || 'k6 run';
  const failed = summaryFailed(data);
  lines.push('');
  lines.push(`DenDenMushi ${profile} summary`);
  lines.push(`Threshold status: ${failed ? 'FAILED' : 'PASSED'} (process outcome is recorded by the runner)`);
  lines.push('');
  lines.push('Metrics:');

  const rows = metricRows(data.metrics);
  const width = rows.reduce((max, row) => Math.max(max, row.name.length), 0);
  for (const row of rows) {
    lines.push(`  ${row.name.padEnd(width)}  ${row.summary}`);
  }

  const thresholds = thresholdRows(data.metrics);
  if (thresholds.length > 0) {
    lines.push('');
    lines.push('Thresholds:');
    for (const threshold of thresholds) {
      const marker = threshold.ok ? 'PASS' : 'FAIL';
      lines.push(`  ${marker} ${threshold.metric} ${threshold.expression}`);
    }
  }

  lines.push('');
  return `${lines.join('\n')}\n`;
}

function buildHtmlSummary(data) {
  const profile = __ENV.K6_REPORT_NAME || 'k6 run';
  const generatedAt = new Date().toISOString();
  const rows = metricRows(data.metrics);
  const thresholds = thresholdRows(data.metrics);
  const failed = summaryFailed(data);
  const status = failed ? 'FAILED' : 'THRESHOLDS PASSED';
  const statusClass = failed ? 'failed' : 'passed';

  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${escapeHtml(profile)} k6 report</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #1f2937; background: #f8fafc; }
    main { max-width: 1120px; margin: 0 auto; }
    h1 { margin-bottom: 4px; font-size: 28px; }
    .meta { color: #64748b; margin-top: 0; }
    .status { display: inline-block; padding: 4px 10px; border-radius: 6px; font-weight: 700; letter-spacing: .02em; }
    .passed { color: #166534; background: #dcfce7; }
    .failed { color: #991b1b; background: #fee2e2; }
    section { margin-top: 28px; }
    table { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #dbe3ee; }
    th, td { padding: 10px 12px; border-bottom: 1px solid #e5e7eb; text-align: left; vertical-align: top; }
    th { background: #eef2f7; font-size: 13px; text-transform: uppercase; color: #475569; }
    tr:last-child td { border-bottom: 0; }
    code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    pre { margin: 0; white-space: pre-wrap; font-size: 12px; color: #334155; }
  </style>
</head>
<body>
<main>
  <h1>${escapeHtml(profile)} k6 report</h1>
  <p class="meta">Generated ${escapeHtml(generatedAt)}</p>
  <p><span class="status ${statusClass}">${status}</span></p>

  <section>
    <h2>Thresholds</h2>
    <table>
      <thead><tr><th>Status</th><th>Metric</th><th>Expression</th></tr></thead>
      <tbody>
        ${thresholds.length > 0 ? thresholds.map((row) => `<tr><td>${row.ok ? 'PASS' : 'FAIL'}</td><td><code>${escapeHtml(row.metric)}</code></td><td><code>${escapeHtml(row.expression)}</code></td></tr>`).join('\n        ') : '<tr><td colspan="3">No thresholds configured.</td></tr>'}
      </tbody>
    </table>
  </section>

  <section>
    <h2>Metrics</h2>
    <table>
      <thead><tr><th>Metric</th><th>Type</th><th>Contains</th><th>Summary</th><th>Values</th></tr></thead>
      <tbody>
        ${rows.map((row) => `<tr><td><code>${escapeHtml(row.name)}</code></td><td>${escapeHtml(row.type)}</td><td>${escapeHtml(row.contains)}</td><td>${escapeHtml(row.summary)}</td><td><pre>${escapeHtml(row.raw)}</pre></td></tr>`).join('\n        ')}
      </tbody>
    </table>
  </section>
</main>
</body>
</html>
`;
}

export function handleSummary(data) {
  const outputs = {
    stdout: buildTextSummary(data),
  };

  const summaryPath = artifactPath('summary.json');
  const reportPath = artifactPath('report.html');
  if (summaryPath) {
    outputs[summaryPath] = JSON.stringify(data, null, 2);
  }
  if (reportPath) {
    outputs[reportPath] = buildHtmlSummary(data);
  }

  return outputs;
}
