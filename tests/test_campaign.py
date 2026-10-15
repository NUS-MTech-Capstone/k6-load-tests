import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from campaign import Coordinator, finalize_status, health_change, resource_violation


class FakeApi:
    def __init__(self): self.closed = []
    def request(self, path, method, body):
        self.closed.append(path); return 200, {}


class CampaignTests(unittest.TestCase):
    def test_aborted_and_nonzero_results_override_success(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / 'summary.json').write_text(json.dumps({'metrics': {}}))
            (p / 'report.html').write_text('<span class="status passed">PASSED</span>')
            self.assertEqual(finalize_status(p, 99)['status'], 'FAILED')
            self.assertNotIn('>PASSED<', (p / 'report.html').read_text())
            self.assertEqual(finalize_status(p, 0, 'interrupted')['status'], 'ABORTED')

    def test_cleanup_only_closes_registered_sessions(self):
        with tempfile.TemporaryDirectory() as d:
            api = FakeApi(); c = Coordinator(api, Path(d), 2)
            c.created = {'test1', 'test2'}; c.closed = {'test1'}; c.stop = 'interrupted'
            self.assertTrue(c.cleanup()); self.assertEqual(api.closed, ['/sessions/test2/close'])
            self.assertTrue(c.cleanup()); self.assertEqual(len(api.closed), 1)

    def test_global_session_ceiling_and_stop(self):
        with tempfile.TemporaryDirectory() as d, patch('campaign.time.sleep'):
            c = Coordinator(FakeApi(), Path(d), 2)
            self.assertTrue(c.permit()); self.assertTrue(c.permit()); self.assertFalse(c.permit())
            self.assertEqual(c.reserved, 2)
            c.stop = 'interrupted'; self.assertFalse(c.permit())

    def test_creation_permits_are_spaced_half_a_second(self):
        with tempfile.TemporaryDirectory() as d, patch('campaign.time.monotonic', return_value=100), patch('campaign.time.sleep') as sleep:
            c = Coordinator(FakeApi(), Path(d), 3)
            self.assertTrue(c.permit()); self.assertTrue(c.permit()); self.assertTrue(c.permit())
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [0, .5, 1.0])

    def test_resource_threshold_requires_two_fresh_samples(self):
        now = dt.datetime.now(dt.timezone.utc)
        row = {'Id': 'q0', 'Label': 'proxy-dataplane_memory', 'StatusCode': 'Complete',
               'Timestamps': [now.isoformat(), (now-dt.timedelta(minutes=1)).isoformat()], 'Values': [81, 82]}
        self.assertEqual(resource_violation({'MetricDataResults': [row]}, now), 'resource_limit:proxy-dataplane_memory')
        row['Values'] = [81, 50]
        self.assertIsNone(resource_violation({'MetricDataResults': [row]}, now))
        row['Timestamps'] = [(now-dt.timedelta(minutes=4)).isoformat()]
        self.assertTrue(resource_violation({'MetricDataResults': [row]}, now).startswith('telemetry_unavailable'))

    def test_revision_or_readiness_change_invalidates_run(self):
        before = [{'ready': True, 'uid': 'one'}]
        self.assertIsNone(health_change(before, before))
        self.assertEqual(health_change(before, [{'ready': True, 'uid': 'two'}]), 'deployment_or_restart_changed')
        self.assertEqual(health_change(before, [{'ready': False, 'uid': 'one'}]), 'workload_not_ready')


if __name__ == '__main__': unittest.main()

class MeasurementGuards(unittest.TestCase):
    def test_barrier_requires_every_session_and_full_plateau(self):
        with tempfile.TemporaryDirectory() as d, patch('campaign.time.time', return_value=100):
            c = Coordinator(FakeApi(), Path(d), 2, expected=2, seconds=6)
            c.event({'kind':'opened','sessionId':'a'}); c.event({'kind':'ready','sessionId':'a'})
            self.assertIsNone(c.plateau_start)
            c.event({'kind':'opened','sessionId':'b'}); c.event({'kind':'ready','sessionId':'b'})
            self.assertEqual(c.plateau_start,101)
            self.assertFalse(c.plateau_result()['complete'])
            c.probe_counts.update({1:3,2:3})
            with patch('campaign.time.time', return_value=107): self.assertTrue(c.plateau_result()['complete'])
            c.event({'kind':'disconnected','sessionId':'a'})
            with patch('campaign.time.time', return_value=107): self.assertFalse(c.plateau_result()['complete'])

    def test_missing_probes_fail_even_with_all_websockets(self):
        with tempfile.TemporaryDirectory() as d:
            c=Coordinator(FakeApi(),Path(d),2,expected=2,seconds=6)
            c.ready={'a','b'};c.plateau_start=0;c.plateau_end=1;c.probe_counts.update({1:3})
            self.assertFalse(c.plateau_result()['complete'])

    def test_missing_series_and_nonfinite_values_fail_closed(self):
        now=dt.datetime.now(dt.timezone.utc)
        row={'Id':'cpu','Label':'cpu','StatusCode':'Complete','Timestamps':[now.isoformat()],'Values':[10]}
        self.assertIn('missing',resource_violation({'MetricDataResults':[row]},now,['cpu','memory']))
        row['Values']=[float('nan')]
        self.assertIn('malformed',resource_violation({'MetricDataResults':[row]},now,['cpu']))

    def test_target_memory_uses_memory_guard(self):
        now=dt.datetime.now(dt.timezone.utc)
        row={'Id':'target_memory','Label':'target_memory','StatusCode':'Complete',
            'Timestamps':[now.isoformat(),(now-dt.timedelta(minutes=1)).isoformat()],'Values':[81,81]}
        self.assertEqual(resource_violation({'MetricDataResults':[row]},now),'resource_limit:target_memory')

    def test_authentication_and_disconnect_stop_new_starts(self):
        for event in ({'kind':'authorization_failure'},{'kind':'disconnected','sessionId':'a','unexpected':True}):
            with tempfile.TemporaryDirectory() as d:
                c=Coordinator(FakeApi(),Path(d),2);c.event(event)
                self.assertIsNotNone(c.guard());self.assertFalse(c.permit())

    def test_probe_errors_are_not_fast_successes(self):
        with tempfile.TemporaryDirectory() as d:
            c=Coordinator(FakeApi(),Path(d),2)
            for i in range(20):c.event({'kind':'probe','ok':i!=0,'latency':10 if i else None})
            self.assertEqual(c.guard(),'probe_failure_rate')

    def test_ssh_selection_excludes_api_read_campaigns(self):
        from campaign import Campaign
        from types import SimpleNamespace
        c=Campaign.__new__(Campaign);c.args=SimpleNamespace(reads_only=False,ssh_only=True,smoke_only=False)
        c.record={}; calls=[];c.run=lambda name,script,*args: calls.append((name,script)) or True
        c.baseline=lambda:None;c.active=lambda:[]
        with patch('campaign.kube',return_value={'data':{'MAX_SESSIONS':'50'}}):c.execute()
        self.assertEqual(len(calls),15)
        self.assertNotIn('agentic_ai_reads',[x[1] for x in calls])
        self.assertNotIn('audit_reads',[x[1] for x in calls])
        self.assertTrue(c.record['plannedCompleted'])

    def test_aborted_discovery_never_continues_to_other_runs(self):
        from campaign import Campaign
        from types import SimpleNamespace
        c=Campaign.__new__(Campaign);c.args=SimpleNamespace(reads_only=False,ssh_only=True,smoke_only=False)
        c.record={};calls=[];c.baseline=lambda:None;c.active=lambda:[]
        def run(name,*args):
            calls.append(name)
            if name.startswith('capacity'):raise RuntimeError('telemetry_unavailable')
            return True
        c.run=run
        with patch('campaign.kube',return_value={'data':{'MAX_SESSIONS':'50'}}),self.assertRaises(RuntimeError):c.execute()
        self.assertEqual(calls,['00-smoke','capacity-5-r1'])


class RecoveryEvidence(unittest.TestCase):
    def test_release_allows_unrelated_active_sessions(self):
        from campaign import verify_capacity_release
        with patch('campaign.capacity_snapshot', return_value=[{'activeSessions': 1}]):
            result = verify_capacity_release(lambda: [{'sessionId': 'other'}], {'campaign'}, timeout=0)
        self.assertTrue(result['capacityReleased'])
        self.assertEqual(result['remainingCampaignSessions'], 0)

    def test_stale_accounting_and_monitor_failure_are_explicit(self):
        from campaign import verify_capacity_release
        with patch('campaign.capacity_snapshot', return_value=[{'activeSessions': 1}]):
            result = verify_capacity_release(lambda: [], {'campaign'}, timeout=0)
        self.assertFalse(result['capacityReleased'])
        with patch('campaign.capacity_snapshot', side_effect=RuntimeError('no capacity')):
            result = verify_capacity_release(lambda: [], {'campaign'}, timeout=0)
        self.assertEqual(result['error'], 'recovery_monitoring_failure:RuntimeError')
        self.assertFalse(result['capacityReleased'])
