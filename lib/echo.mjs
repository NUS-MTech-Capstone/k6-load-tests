export const PACKET = Object.freeze({ input: 0x00, output: 0x01, error: 0x02, event: 0x07, resize: 0x10, detach: 0x12, terminate: 0x18 });

// The parser retains only a small, transient matching buffer, never a recording.
export class EchoProbe {
  constructor(timeoutMs = 1500) { this.timeoutMs = timeoutMs; this.pending = null; this.buffer = ''; }
  begin(marker, now) {
    if (this.pending) throw new Error('Only one echo probe may be in flight');
    this.pending = { marker, started: now }; this.buffer = '';
  }
  receive(packet, now) {
    const bytes = new Uint8Array(packet);
    if (!this.pending || bytes[0] !== PACKET.output) return null;
    for (let i = 1; i < bytes.length; i++) this.buffer += String.fromCharCode(bytes[i]);
    const matched = this.buffer.includes(this.pending.marker);
    this.buffer = this.buffer.slice(-4096);
    if (!matched) return null;
    const latency = now - this.pending.started;
    this.pending = null; this.buffer = '';
    return latency <= this.timeoutMs ? { ok: true, latency } : { ok: false, reason: 'timeout' };
  }
  expire(now) {
    if (!this.pending || now - this.pending.started < this.timeoutMs) return null;
    this.pending = null; this.buffer = '';
    return { ok: false, reason: 'timeout' };
  }
  fail(reason) {
    if (!this.pending) return null;
    this.pending = null; this.buffer = '';
    return { ok: false, reason };
  }
}

export function validWorkload(vus) {
  return Number.isInteger(vus) && vus >= 1 && vus <= 40;
}

export function summaryFailed(data) {
  return Boolean(data.state?.isFailed) || Object.values(data.metrics || {}).some(m =>
    Object.values(m.thresholds || {}).some(t => t.ok === false));
}

export function rateUnit(metric) { return metric.type === 'rate' ? '%' : '/s'; }
