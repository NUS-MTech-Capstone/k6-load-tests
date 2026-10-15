import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { finalizeReport } from '../tools/finalize-report.mjs';
import { EchoProbe, PACKET, validWorkload, summaryFailed, rateUnit } from '../lib/echo.mjs';
const packet = (type, s) => Uint8Array.from([type, ...Array.from(s, c => c.charCodeAt(0))]).buffer;

test('only exact output marker across fragments completes a probe', () => {
  const p = new EchoProbe(); p.begin('unique_1', 100);
  assert.equal(p.receive(packet(PACKET.event, 'unique_1'), 110), null);
  assert.equal(p.receive(packet(PACKET.error, 'unique_1'), 110), null);
  assert.equal(p.receive(packet(PACKET.output, 'banner old_marker uni'), 120), null);
  assert.deepEqual(p.receive(packet(PACKET.output, 'que_1'), 130), { ok: true, latency: 30 });
  assert.equal(p.receive(packet(PACKET.output, 'unique_1'), 140), null);
});
test('timeouts are failures and stale output does not satisfy next probe', () => {
  const p = new EchoProbe(100); p.begin('old', 0);
  assert.deepEqual(p.expire(100), { ok: false, reason: 'timeout' });
  p.begin('new', 200); assert.equal(p.receive(packet(PACKET.output, 'old'), 210), null);
  assert.deepEqual(p.receive(packet(PACKET.output, 'new'), 310), { ok: false, reason: 'timeout' });
});
test('protocol failures and disconnects do not become timeout or latency samples', () => {
  for (const reason of ['protocol_error', 'disconnect']) {
    const p = new EchoProbe(); p.begin('marker', 0);
    assert.deepEqual(p.fail(reason), {ok:false, reason});
    assert.equal(p.expire(2000), null);
    assert.equal(p.receive(packet(PACKET.output, 'marker'), 2001), null);
  }
});
test('matching memory is bounded and overlapping probes rejected', () => {
  const p = new EchoProbe(); p.begin('marker', 0);
  p.receive(packet(PACKET.output, 'x'.repeat(20000)), 1);
  assert.equal(p.buffer.length, 4096); assert.throws(() => p.begin('next', 2));
});
test('workload is integral and capped at forty', () => {
  for (const n of [-1, 0, 1.5, 41, NaN, Infinity]) assert.equal(validWorkload(n), false);
  assert.equal(validWorkload(1), true); assert.equal(validWorkload(40), true);
});
test('threshold failures cannot produce a passing summary', () => {
  assert.equal(summaryFailed({ metrics: { latency: { thresholds: { 'p(95)<200': { ok: false } } } } }), true);
  assert.equal(summaryFailed({ metrics: { latency: { thresholds: { 'p(95)<200': { ok: true } } } } }), false);
  assert.equal(rateUnit({ type: 'counter' }), '/s'); assert.equal(rateUnit({ type: 'rate' }), '%');
});
test('packet constants agree with the dataplane contract', () => {
  const go = fs.readFileSync(new URL('./fixtures/dataplane-packet.go', import.meta.url), 'utf8');
  for (const [key, name] of Object.entries({ input: 'Input', output: 'Output', error: 'Error', event: 'SessionEvent', resize: 'Resize', detach: 'ClientClose', terminate: 'ClientEndSession' })) {
    const match = go.match(new RegExp(`${name}\\s+Type = (0x[0-9a-f]+)`));
    assert.ok(match); assert.equal(PACKET[key], Number(match[1]));
  }
});

test('artifact wrapper overrides passing thresholds on nonzero exit or interruption', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ddm-report-'));
  try {
    fs.writeFileSync(path.join(dir, 'summary.json'), JSON.stringify({metrics:{}}));
    fs.writeFileSync(path.join(dir, 'report.html'), '<span class="status passed">THRESHOLDS PASSED</span>');
    assert.equal(finalizeReport(dir, 1).status, 'FAILED');
    assert.ok(!fs.readFileSync(path.join(dir, 'report.html'), 'utf8').includes('PASSED'));
    assert.equal(finalizeReport(dir, 130, true).status, 'ABORTED');
  } finally { fs.rmSync(dir, {recursive:true}); }
});
