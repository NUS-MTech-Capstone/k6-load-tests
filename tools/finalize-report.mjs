import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { summaryFailed } from '../lib/echo.mjs';

export function finalizeReport(directory, exitCode, interrupted = false) {
  const file = join(directory, 'summary.json');
  let summary;
  try { summary = JSON.parse(readFileSync(file, 'utf8')); } catch { /* Missing/invalid summary fails closed. */ }
  const status = interrupted ? 'ABORTED' : exitCode === 0 && summary && !summaryFailed(summary) ? 'PASSED' : 'FAILED';
  const result = { status, exitCode, interrupted };
  writeFileSync(join(directory, 'result.json'), JSON.stringify(result, null, 2));
  const report = join(directory, 'report.html');
  if (existsSync(report)) {
    let content = readFileSync(report, 'utf8').replace('>THRESHOLDS PASSED<', `>${status}<`);
    if (status !== 'PASSED') content = content.replace('>PASSED<', `>${status}<`).replace('status passed', 'status failed');
    writeFileSync(report, content);
  }
  return result;
}
