#!/usr/bin/env python3
"""
Unit tests for build-ami.py

Tests cover:
- Argument parsing
- AMI listing and caching
- Validation logic
- Error handling
- Dry-run mode
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, call

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent))

# Import after path modification (handling the dash in filename)
import importlib.util
spec = importlib.util.spec_from_file_location("build_ami", Path(__file__).parent / "build-ami.py")
build_ami = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_ami)

AMIManager = build_ami.AMIManager
AMIBuildError = build_ami.AMIBuildError
PrerequisiteError = build_ami.PrerequisiteError


class TestAMIManager(unittest.TestCase):
    """Test cases for AMIManager class."""

    def setUp(self):
        """Set up test fixtures."""
        self.manager = AMIManager(region='us-west-2', dry_run=True)

    @patch('boto3.client')
    def test_init(self, mock_boto_client):
        """Test AMIManager initialization."""
        manager = AMIManager(region='us-east-1', dry_run=False)
        self.assertEqual(manager.region, 'us-east-1')
        self.assertEqual(manager.dry_run, False)

    @patch('shutil.which')
    @patch('subprocess.run')
    @patch('boto3.client')
    def test_validate_prerequisites_success(self, mock_boto_client, mock_subprocess, mock_which):
        """Test prerequisite validation with all checks passing."""
        # Mock Packer installation
        mock_which.return_value = '/usr/local/bin/packer'

        # Mock Packer version command
        mock_subprocess.return_value = MagicMock(
            stdout='Packer v1.9.0',
            returncode=0
        )

        # Mock AWS credentials
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.return_value = {
            'Account': '123456789012',
            'Arn': 'arn:aws:iam::123456789012:user/test'
        }
        mock_boto_client.return_value = mock_sts

        # Mock EC2 client for permission check
        self.manager.ec2_client = MagicMock()
        self.manager.ec2_client.describe_images.return_value = {'Images': []}

        # Mock Packer template existence
        with patch('pathlib.Path.exists', return_value=True):
            # Should not raise exception
            try:
                self.manager.validate_prerequisites()
            except Exception as e:
                self.fail(f"validate_prerequisites raised {e}")

    @patch('shutil.which')
    def test_validate_prerequisites_no_packer(self, mock_which):
        """Test prerequisite validation fails when Packer not installed."""
        mock_which.return_value = None

        with self.assertRaises(PrerequisiteError) as context:
            self.manager.validate_prerequisites()

        self.assertIn('Packer not found', str(context.exception))

    def test_list_amis_cache(self):
        """Test AMI listing with caching."""
        # Create temporary cache
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / 'ami-list.json'

            # Mock cache file
            with patch.object(build_ami, 'CACHE_FILE', cache_file):
                # Create cache data
                cache_data = {
                    'region': 'us-west-2',
                    'timestamp': datetime.now().timestamp(),
                    'amis': [
                        {
                            'ami_id': 'ami-test123',
                            'name': 'pulsar-base-3.0.0-20251006',
                            'pulsar_version': '3.0.0',
                            'state': 'available',
                            'creation_date': '2025-10-06T14:23:00.000Z',
                            'description': 'Test AMI',
                            'snapshot_id': 'snap-test123'
                        }
                    ]
                }

                # Write cache
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                with open(cache_file, 'w') as f:
                    json.dump(cache_data, f)

                # Get cached AMIs
                amis = self.manager._get_cached_amis()

                self.assertIsNotNone(amis)
                self.assertEqual(len(amis), 1)
                self.assertEqual(amis[0]['ami_id'], 'ami-test123')

    def test_list_amis_cache_expired(self):
        """Test that expired cache is ignored."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / 'ami-list.json'

            with patch.object(build_ami, 'CACHE_FILE', cache_file):
                # Create expired cache data
                cache_data = {
                    'region': 'us-west-2',
                    'timestamp': (datetime.now() - timedelta(minutes=10)).timestamp(),
                    'amis': []
                }

                cache_file.parent.mkdir(parents=True, exist_ok=True)
                with open(cache_file, 'w') as f:
                    json.dump(cache_data, f)

                # Get cached AMIs (modify stat to make file appear old)
                # Set file modification time to 11 minutes ago
                old_time = datetime.now().timestamp() - (11 * 60)
                os.utime(cache_file, (old_time, old_time))

                amis = self.manager._get_cached_amis()

                # Should return None for expired cache
                self.assertIsNone(amis)

    def test_list_amis_cache_wrong_region(self):
        """Test that cache for different region is ignored."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / 'ami-list.json'

            with patch.object(build_ami, 'CACHE_FILE', cache_file):
                # Create cache for different region
                cache_data = {
                    'region': 'us-east-1',  # Different region
                    'timestamp': datetime.now().timestamp(),
                    'amis': []
                }

                cache_file.parent.mkdir(parents=True, exist_ok=True)
                with open(cache_file, 'w') as f:
                    json.dump(cache_data, f)

                # Manager is for us-west-2
                amis = self.manager._get_cached_amis()

                # Should return None for region mismatch
                self.assertIsNone(amis)

    @patch('subprocess.Popen')
    def test_build_dry_run(self, mock_popen):
        """Test build in dry-run mode."""
        ami_id = self.manager.build(pulsar_version='3.0.0')

        # Dry-run should return dummy AMI ID
        self.assertEqual(ami_id, 'ami-dryrun123456789')

        # Should not call Packer
        mock_popen.assert_not_called()

    def test_validate_dry_run(self):
        """Test validation in dry-run mode."""
        result = self.manager.validate(ami_id='ami-test123')

        # Dry-run validation should always return True
        self.assertTrue(result)

    def test_delete_dry_run(self):
        """Test deletion in dry-run mode."""
        # Should not raise exception in dry-run mode
        try:
            self.manager.delete(ami_id='ami-test123')
        except Exception as e:
            self.fail(f"delete raised {e} in dry-run mode")

    def test_get_latest_ami_empty(self):
        """Test get_latest_ami with no AMIs."""
        with patch.object(self.manager, 'list_amis', return_value=[]):
            latest = self.manager.get_latest_ami()
            self.assertIsNone(latest)

    def test_get_latest_ami_with_amis(self):
        """Test get_latest_ami with multiple AMIs."""
        mock_amis = [
            {
                'ami_id': 'ami-newest',
                'creation_date': '2025-10-06T14:23:00.000Z'
            },
            {
                'ami_id': 'ami-older',
                'creation_date': '2025-09-20T10:15:00.000Z'
            }
        ]

        with patch.object(self.manager, 'list_amis', return_value=mock_amis):
            latest = self.manager.get_latest_ami()
            self.assertEqual(latest, 'ami-newest')

    def test_cache_invalidation(self):
        """Test that cache is invalidated after operations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / 'ami-list.json'

            with patch.object(build_ami, 'CACHE_FILE', cache_file):
                # Create cache
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text('{"test": "data"}')

                self.assertTrue(cache_file.exists())

                # Invalidate cache
                self.manager._invalidate_cache()

                # Cache should be deleted
                self.assertFalse(cache_file.exists())


class TestArgumentParsing(unittest.TestCase):
    """Test command-line argument parsing."""

    @patch('sys.argv', ['build-ami.py', 'build', '--version', '3.0.0'])
    def test_build_args(self):
        """Test build command argument parsing."""
        args = build_ami.parse_args()

        self.assertEqual(args.command, 'build')
        self.assertEqual(args.version, '3.0.0')
        self.assertEqual(args.instance_type, 't3.small')

    @patch('sys.argv', ['build-ami.py', 'list'])
    def test_list_args(self):
        """Test list command argument parsing."""
        args = build_ami.parse_args()

        self.assertEqual(args.command, 'list')
        self.assertFalse(args.no_cache)

    @patch('sys.argv', ['build-ami.py', 'list', '--no-cache'])
    def test_list_args_no_cache(self):
        """Test list command with --no-cache."""
        args = build_ami.parse_args()

        self.assertEqual(args.command, 'list')
        self.assertTrue(args.no_cache)

    @patch('sys.argv', ['build-ami.py', 'validate', '--ami-id', 'ami-test123'])
    def test_validate_args(self):
        """Test validate command argument parsing."""
        args = build_ami.parse_args()

        self.assertEqual(args.command, 'validate')
        self.assertEqual(args.ami_id, 'ami-test123')
        self.assertEqual(args.instance_type, 't3.micro')

    @patch('sys.argv', ['build-ami.py', 'delete', '--ami-id', 'ami-test123'])
    def test_delete_args(self):
        """Test delete command argument parsing."""
        args = build_ami.parse_args()

        self.assertEqual(args.command, 'delete')
        self.assertEqual(args.ami_id, 'ami-test123')
        self.assertFalse(args.keep_snapshots)

    @patch('sys.argv', ['build-ami.py', 'delete', '--ami-id', 'ami-test123', '--keep-snapshots'])
    def test_delete_args_keep_snapshots(self):
        """Test delete command with --keep-snapshots."""
        args = build_ami.parse_args()

        self.assertTrue(args.keep_snapshots)

    @patch('sys.argv', ['build-ami.py', 'latest'])
    def test_latest_args(self):
        """Test latest command argument parsing."""
        args = build_ami.parse_args()

        self.assertEqual(args.command, 'latest')

    @patch('sys.argv', ['build-ami.py', '--region', 'us-east-1', 'list'])
    def test_global_args_region(self):
        """Test global --region argument."""
        args = build_ami.parse_args()

        self.assertEqual(args.region, 'us-east-1')

    @patch('sys.argv', ['build-ami.py', '--dry-run', 'build', '--version', '3.0.0'])
    def test_global_args_dry_run(self):
        """Test global --dry-run argument."""
        args = build_ami.parse_args()

        self.assertTrue(args.dry_run)

    @patch('sys.argv', ['build-ami.py', '--debug', 'list'])
    def test_global_args_debug(self):
        """Test global --debug argument."""
        args = build_ami.parse_args()

        self.assertTrue(args.debug)


class TestErrorHandling(unittest.TestCase):
    """Test error handling scenarios."""

    def setUp(self):
        """Set up test fixtures."""
        self.manager = AMIManager(region='us-west-2', dry_run=True)

    def test_ami_build_error(self):
        """Test AMIBuildError exception."""
        error = AMIBuildError("Test error")
        self.assertIsInstance(error, Exception)
        self.assertEqual(str(error), "Test error")

    def test_prerequisite_error(self):
        """Test PrerequisiteError exception."""
        error = PrerequisiteError("Prerequisites not met")
        self.assertIsInstance(error, AMIBuildError)
        self.assertEqual(str(error), "Prerequisites not met")


class TestIntegration(unittest.TestCase):
    """Integration tests (require AWS credentials)."""

    @unittest.skipIf(
        os.getenv('SKIP_AWS_TESTS', 'true').lower() == 'true',
        'AWS integration tests disabled (set SKIP_AWS_TESTS=false to enable)'
    )
    def test_list_real_amis(self):
        """Test listing real AMIs (requires AWS credentials)."""
        manager = AMIManager(region='us-west-2', dry_run=False)

        try:
            amis = manager.list_amis(use_cache=False)
            # Should return a list (may be empty)
            self.assertIsInstance(amis, list)
        except Exception as e:
            self.fail(f"Failed to list AMIs: {e}")


def run_tests():
    """Run all tests."""
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test cases
    suite.addTests(loader.loadTestsFromTestCase(TestAMIManager))
    suite.addTests(loader.loadTestsFromTestCase(TestArgumentParsing))
    suite.addTests(loader.loadTestsFromTestCase(TestErrorHandling))
    suite.addTests(loader.loadTestsFromTestCase(TestIntegration))

    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Return exit code
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(run_tests())
