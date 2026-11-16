"""Producer identity survives downstream reruns; wrong inputs fail closed."""
import copy
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import yaml

from tools.ci import dependency_artifact as contract

ROOT = Path(__file__).resolve().parents[2]
DIGEST = 'a' * 64


class DependencyArtifactTests(unittest.TestCase):
    def setUp(self):
        self.metadata = {'id': 7001, 'digest': 'sha256:' + DIGEST,
                         'expired': False, 'workflow_run': {'id': 9001}}
        self.env = {'DEPENDENCY_ARTIFACT_ID': '7001', 'DEPENDENCY_ARTIFACT_DIGEST': DIGEST,
                    'GITHUB_RUN_ID': '9001', 'GITHUB_API_URL': 'https://api.github.com',
                    'GITHUB_REPOSITORY': 'owner/repo', 'GH_TOKEN': 'synthetic-unit-token'}

    def validate(self, metadata=None, **kwargs):
        values = dict(artifact_id='7001', digest=DIGEST, run_id='9001')
        values.update(kwargs)
        contract.validate_artifact(self.metadata if metadata is None else metadata, **values)

    def test_initial_and_partial_reruns_fetch_the_same_producer_id(self):
        for attempt in ('1', '2', '10'):
            with self.subTest(attempt=attempt):
                env = dict(self.env, GITHUB_RUN_ATTEMPT=attempt)
                payload = io.BytesIO(json.dumps(self.metadata).encode())
                with patch.dict(os.environ, env), patch.object(contract.urllib.request, 'urlopen', return_value=payload) as fetch:
                    contract.main()
                request = fetch.call_args.args[0]
                self.assertEqual(request.full_url, 'https://api.github.com/repos/owner/repo/actions/artifacts/7001')

    def test_prefixed_digest_is_also_accepted(self):
        self.validate(digest='sha256:' + DIGEST)

    def test_missing_or_invalid_producer_identity_is_rejected(self):
        for artifact_id in ('', '0', '../other', 'seven'):
            with self.subTest(artifact_id=artifact_id), self.assertRaises(ValueError):
                self.validate(artifact_id=artifact_id)
        for digest in ('', 'bad', 'md5:' + DIGEST, 'b' * 63):
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                self.validate(digest=digest)

    def test_wrong_artifact_id_is_rejected(self):
        self.validate()
        with self.assertRaisesRegex(ValueError, 'ID differs'):
            self.validate(dict(self.metadata, id=7002))

    def test_artifact_from_another_run_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'another workflow'):
            self.validate(dict(self.metadata, workflow_run={'id': 9002}))

    def test_missing_workflow_identity_is_rejected(self):
        metadata = copy.deepcopy(self.metadata)
        del metadata['workflow_run']
        with self.assertRaises(ValueError):
            self.validate(metadata)

    def test_incorrect_or_missing_digest_is_rejected(self):
        for digest in (None, 'sha256:' + 'b' * 64):
            with self.subTest(digest=digest), self.assertRaisesRegex(ValueError, 'SHA256 differs'):
                self.validate(dict(self.metadata, digest=digest))

    def test_expired_or_unknown_expiry_is_rejected(self):
        for expired in (True, None):
            with self.subTest(expired=expired), self.assertRaises(ValueError):
                self.validate(dict(self.metadata, expired=expired))

    def test_missing_artifact_fails_without_name_or_latest_fallback(self):
        error = HTTPError('https://api.github.com/artifact', 404, 'Not Found', {}, None)
        with patch.dict(os.environ, self.env), patch.object(contract.urllib.request, 'urlopen', side_effect=error) as fetch:
            with self.assertRaises(HTTPError):
                contract.main()
        self.assertEqual(fetch.call_count, 1)

    def test_missing_id_is_rejected_before_network(self):
        with patch.dict(os.environ, dict(self.env, DEPENDENCY_ARTIFACT_ID='')), patch.object(contract.urllib.request, 'urlopen') as fetch:
            with self.assertRaises(ValueError):
                contract.main()
        fetch.assert_not_called()


class WorkflowContractTests(unittest.TestCase):
    def test_reusable_ci_caller_grants_artifact_read_permission(self):
        release = yaml.safe_load((ROOT / '.github/workflows/release.yml').read_text())
        self.assertEqual(release['permissions']['actions'], 'read')

    def test_all_consumers_use_successful_producer_outputs(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
        producer = workflow['jobs']['dependencies']
        self.assertEqual(producer['outputs']['artifact-id'], '${{ steps.upload.outputs.artifact-id }}')
        self.assertEqual(producer['outputs']['artifact-digest'], '${{ steps.upload.outputs.artifact-digest }}')
        upload = next(s for s in producer['steps'] if s.get('id') == 'upload')
        self.assertIn('actions/upload-artifact@', upload['uses'])
        consumers = []
        for name, job in workflow['jobs'].items():
            for step in job['steps']:
                if step.get('uses') == './.github/actions/install-production':
                    consumers.append(name)
                    self.assertIn('dependencies', job['needs'])
                    self.assertEqual(step['with']['artifact-id'], '${{ needs.dependencies.outputs.artifact-id }}')
                    self.assertEqual(step['with']['artifact-digest'], '${{ needs.dependencies.outputs.artifact-digest }}')
        self.assertEqual(set(consumers), {'static', 'tests-fast', 'tests-tenant', 'tests-postgres', 'migrations', 'deploy-rehearsal'})

    def test_download_and_install_remain_fail_closed(self):
        action = yaml.safe_load((ROOT / '.github/actions/install-production/action.yml').read_text())
        steps = action['runs']['steps']
        verify = next(i for i, s in enumerate(steps) if 'dependency_artifact.py' in s.get('run', ''))
        download = next(i for i, s in enumerate(steps) if s.get('uses', '').startswith('actions/download-artifact@'))
        self.assertLess(verify, download)
        self.assertEqual(steps[download]['with']['artifact-ids'], '${{ inputs.artifact-id }}')
        self.assertEqual(steps[download]['with']['digest-mismatch'], 'error')
        self.assertNotIn('name', steps[download]['with'])
        install = steps[-1]['run']
        for protection in ('--no-index', '--require-hashes', '--no-deps', 'pip check'):
            self.assertIn(protection, install)
        self.assertNotIn('run_attempt', json.dumps(action))


if __name__ == '__main__':
    unittest.main()
