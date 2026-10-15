"""Real k6 against a local protocol fixture; no AWS access or credentials."""
import base64
import hashlib
import http.server
import json
import os
from pathlib import Path
import struct
import subprocess
import signal
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from campaign import Coordinator, finalize_status


class Fixture(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *args): pass
    def send_json(self, body, status=200):
        b = json.dumps(body).encode(); self.send_response(status)
        self.send_header('Content-Length', str(len(b))); self.send_header('Content-Type', 'application/json')
        self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if self.path == '/sessions/start':
            self.send_json({'sessionId': 'fixture', 'sessionToken': 'local-fixture', 'websocketUrl': f'ws://127.0.0.1:{self.server.server_port}/sessions/fixture/ws'})
        elif self.path.endswith('/rejoin') or self.path.endswith('/observe'):
            self.send_json({'sessionId':'fixture','sessionToken':'observer' if self.path.endswith('/observe') else 'owner','websocketUrl':f'ws://127.0.0.1:{self.server.server_port}/sessions/fixture/ws'})
        elif self.path.endswith('/close'):
            self.server.close_calls += 1
            self.send_json({}, 500 if self.server.fail_close else 200)
        else: self.send_json({})
    def do_GET(self):
        if self.headers.get('Upgrade', '').lower() != 'websocket': return self.send_json({})
        key = self.headers['Sec-WebSocket-Key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
        self.send_response(101); self.send_header('Upgrade', 'websocket'); self.send_header('Connection', 'Upgrade')
        self.send_header('Sec-WebSocket-Accept', base64.b64encode(hashlib.sha1(key.encode()).digest()).decode()); self.end_headers()
        def send(payload):
            self.wfile.write(bytes([0x82, len(payload)]) + payload); self.wfile.flush()
        send(b'\x07event is not echo')
        if getattr(self.server,'scrollback',b''):send(b'\x01'+self.server.scrollback[-100:])
        while True:
            head = self.rfile.read(2)
            if len(head) != 2: return
            size = head[1] & 127
            if size == 126: size = struct.unpack('!H', self.rfile.read(2))[0]
            if size == 127: size = struct.unpack('!Q', self.rfile.read(8))[0]
            mask = self.rfile.read(4) if head[1] & 128 else None
            body = self.rfile.read(size)
            if mask: body = bytes(b ^ mask[i % 4] for i, b in enumerate(body))
            if head[0] & 15 == 8: return
            if body[:1] in (b'\x12',b'\x18'):return
            if body[:1] == b'\x00' and self.headers.get('Authorization') == 'Bearer observer':
                send(b'\x02attachment is not the active session writer');continue
            if body[:1] == b'\x00' and body[1:2] != b'\x15':
                self.server.scrollback=(getattr(self.server,'scrollback',b'')+body[1:])[-100:]
                # Split the actual echo across frames to exercise the real k6 binding.
                midpoint = len(body) // 2
                send(b'\x01' + body[1:midpoint]); send(b'\x01' + body[midpoint:])


class K6Integration(unittest.TestCase):
    def exercise(self, fail_close):
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
        server.fail_close = fail_close; server.close_calls = 0
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as d:
                env = {**os.environ, 'BASE_URL': f'http://127.0.0.1:{server.server_port}', 'HOST_ID': 'fixture', 'CR_ID': 'fixture',
                       'K6_VUS': '1', 'K6_RAMP_SECONDS': '0', 'K6_MEASURE_SECONDS': '7', 'K6_ARTIFACT_DIR': d}
                env.pop('K6_COORDINATOR_URL', None)
                p = subprocess.run(['k6', 'run', 'scripts/terminal_capacity.js'], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
                summary = json.loads((Path(d) / 'summary.json').read_text())
                self.assertEqual(p.returncode == 0, not fail_close, p.stderr[-2000:])
                self.assertEqual(server.close_calls, 1)
                self.assertEqual(summary['metrics']['ddm_terminal_probe_success']['values']['rate'], 1)
                self.assertGreaterEqual(summary['metrics']['ddm_terminal_echo_ms']['values']['count'], 2)
                if fail_close: self.assertIn('>FAILED<', (Path(d) / 'report.html').read_text())
        finally: server.shutdown(); server.server_close()
    def test_real_k6_fragmented_echo_and_cleanup(self): self.exercise(False)
    def test_real_k6_failed_cleanup_fails_report(self): self.exercise(True)

    def test_real_lifecycle_rejoin_observer_rejection_and_termination(self):
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Fixture)
        server.fail_close=False;server.close_calls=0
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as d:
                env={**os.environ,'BASE_URL':f'http://127.0.0.1:{server.server_port}','HOST_ID':'fixture','CR_ID':'fixture','K6_ARTIFACT_DIR':d}
                env.pop('K6_COORDINATOR_URL',None)
                proc=subprocess.run(['k6','run','scripts/smoke.js'],cwd=ROOT,env=env,capture_output=True,text=True,timeout=30)
                self.assertEqual(proc.returncode,0,proc.stdout[-3000:]+proc.stderr[-1000:])
                summary=json.loads((Path(d)/'summary.json').read_text())
                self.assertEqual(summary['metrics']['ddm_lifecycle_success']['values']['rate'],1)
                self.assertEqual(server.close_calls,1)
        finally:server.shutdown();server.server_close()

    def test_real_coordinator_starts_plateau_only_after_echo(self):
        fixture=http.server.ThreadingHTTPServer(('127.0.0.1',0),Fixture)
        fixture.fail_close=False;fixture.close_calls=0
        threading.Thread(target=fixture.serve_forever,daemon=True).start()
        class Api:
            def request(self,*args):return 200,{}
        try:
            with tempfile.TemporaryDirectory() as d:
                c=Coordinator(Api(),Path(d),1,expected=1,seconds=6);server=c.server()
                try:
                    env={**os.environ,'BASE_URL':f'http://127.0.0.1:{fixture.server_port}','HOST_ID':'fixture','CR_ID':'fixture',
                         'K6_VUS':'1','K6_RAMP_SECONDS':'0','K6_MEASURE_SECONDS':'6','K6_ARTIFACT_DIR':d,
                         'K6_COORDINATOR_URL':f'http://127.0.0.1:{server.server_port}','K6_COORDINATOR_TOKEN':c.token}
                    proc=subprocess.run(['k6','run','scripts/terminal_capacity.js'],cwd=ROOT,env=env,capture_output=True,text=True,timeout=20)
                    self.assertEqual(proc.returncode,0,proc.stdout[-2000:]+proc.stderr[-1000:])
                    self.assertTrue(c.plateau_result()['complete'],c.plateau_result())
                    self.assertEqual(c.created,c.closed)
                finally:server.shutdown();server.server_close()
        finally:fixture.shutdown();fixture.server_close()

    def test_interruption_reconciles_campaign_sessions(self):
        fixture = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
        fixture.fail_close = False; fixture.close_calls = 0
        threading.Thread(target=fixture.serve_forever, daemon=True).start()
        class Api:
            def request(self, path, method, body):
                request = urllib.request.Request(f'http://127.0.0.1:{fixture.server_port}{path}', b'{}', method=method)
                with urllib.request.urlopen(request) as response: return response.status, {}
        try:
            with tempfile.TemporaryDirectory() as d:
                coordinator = Coordinator(Api(), Path(d), 1); server = coordinator.server()
                env = {**os.environ, 'BASE_URL': f'http://127.0.0.1:{fixture.server_port}', 'HOST_ID': 'fixture', 'CR_ID': 'fixture',
                       'K6_VUS': '1', 'K6_RAMP_SECONDS': '0', 'K6_MEASURE_SECONDS': '60', 'K6_ARTIFACT_DIR': d,
                       'K6_COORDINATOR_URL': f'http://127.0.0.1:{server.server_port}', 'K6_COORDINATOR_TOKEN': coordinator.token}
                proc = subprocess.Popen(['k6', 'run', 'scripts/terminal_capacity.js'], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    deadline = time.monotonic()+10
                    while not coordinator.connected and proc.poll() is None and time.monotonic()<deadline: time.sleep(.05)
                    self.assertEqual(coordinator.created, {'fixture'})
                    proc.send_signal(signal.SIGINT); code = proc.wait(timeout=10)
                    coordinator.stop = 'interrupted'; self.assertTrue(coordinator.cleanup())
                    self.assertEqual(coordinator.created, coordinator.closed)
                    self.assertEqual(finalize_status(Path(d), code, 'interrupted')['status'], 'ABORTED')
                finally:
                    if proc.poll() is None: proc.kill(); proc.wait()
                    server.shutdown(); server.server_close()
        finally: fixture.shutdown(); fixture.server_close()


if __name__ == '__main__': unittest.main()
