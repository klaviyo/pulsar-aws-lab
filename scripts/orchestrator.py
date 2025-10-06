#!/usr/bin/env python3
"""
Pulsar AWS Lab Orchestrator
Main workflow controller for infrastructure, deployment, testing, and teardown
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import boto3
import yaml

# Import cleanup functions
from cleanup_by_tag import get_resources_by_experiment_id, cleanup_resources

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Project root directory
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
TERRAFORM_DIR = PROJECT_ROOT / "terraform"
ANSIBLE_DIR = PROJECT_ROOT / "ansible"
RESULTS_DIR = Path.home() / ".pulsar-aws-lab"


class OrchestratorError(Exception):
    """Base exception for orchestrator errors"""
    pass


class Orchestrator:
    """Main orchestrator class"""

    def __init__(self, experiment_id: Optional[str] = None):
        """Initialize orchestrator"""
        self.experiment_id = experiment_id or f"exp-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        self.experiment_dir = RESULTS_DIR / self.experiment_id
        self.experiment_dir.mkdir(parents=True, exist_ok=True)
        self.infrastructure_config = None

        # Create/update "latest" symlink
        latest_link = RESULTS_DIR / "latest"
        if latest_link.exists() or latest_link.is_symlink():
            latest_link.unlink()
        latest_link.symlink_to(self.experiment_dir)

        # Setup logging to file
        log_file = self.experiment_dir / "orchestrator.log"
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        logger.addHandler(file_handler)

        logger.info(f"Initialized orchestrator for experiment: {self.experiment_id}")
        print(f"\n{'='*60}")
        print(f"Experiment ID: {self.experiment_id}")
        print(f"Results will be saved to: {self.experiment_dir}")
        print(f"{'='*60}\n")

    def load_config(self, config_file: Path) -> Dict:
        """Load YAML configuration file"""
        logger.info(f"Loading configuration from {config_file}")
        with open(config_file, 'r') as f:
            return yaml.safe_load(f)

    def verify_ssm_plugin(self) -> None:
        """Verify AWS Session Manager plugin is installed"""
        logger.info("Verifying AWS Session Manager plugin installation...")

        try:
            # Try using which to find the plugin first
            which_result = subprocess.run(
                ["which", "session-manager-plugin"],
                capture_output=True,
                text=True,
                check=False
            )

            if which_result.returncode == 0:
                plugin_path = which_result.stdout.strip()
                result = subprocess.run(
                    [plugin_path],
                    capture_output=True,
                    text=True,
                    check=False
                )
                logger.info("AWS Session Manager plugin is installed")
            else:
                raise FileNotFoundError("session-manager-plugin not found in PATH")
        except (FileNotFoundError, PermissionError) as e:
            error_msg = (
                "AWS Session Manager plugin not found or not executable!\n"
                "Install it from: https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html\n"
                f"Error: {e}"
            )
            logger.error(error_msg)
            raise OrchestratorError(error_msg)

    def verify_ssm_connectivity(self, region: str, max_wait: int = 300, check_interval: int = 10) -> None:
        """Verify EC2 instances are registered with SSM and wait for SSH readiness"""
        logger.info("Verifying SSM connectivity to instances...")

        ssm_client = boto3.client('ssm', region_name=region)
        start_time = time.time()

        try:
            # Poll for instance registration
            while time.time() - start_time < max_wait:
                response = ssm_client.describe_instance_information(
                    Filters=[
                        {'Key': 'tag:ExperimentID', 'Values': [self.experiment_id]}
                    ]
                )

                managed_instances = response.get('InstanceInformationList', [])

                if managed_instances:
                    online_instances = [i for i in managed_instances if i.get('PingStatus') == 'Online']
                    logger.info(f"Found {len(online_instances)}/{len(managed_instances)} instances online with SSM")

                    if len(online_instances) == len(managed_instances):
                        # All instances online - list them
                        logger.info("All instances online with SSM:")
                        for instance in managed_instances:
                            instance_id = instance['InstanceId']
                            ping_status = instance.get('PingStatus', 'Unknown')
                            logger.info(f"  {instance_id}: {ping_status}")

                        # Fixed delay for SSH daemon initialization (empirically determined for AL2023)
                        logger.info("Waiting 90 seconds for SSH daemon and Session Manager to be fully ready...")
                        time.sleep(90)
                        logger.info("SSH readiness wait complete, proceeding to Ansible")
                        return
                    else:
                        logger.info(f"Waiting for all instances to come online, retrying in {check_interval}s...")
                else:
                    elapsed = int(time.time() - start_time)
                    logger.info(f"No instances registered with SSM yet ({elapsed}s elapsed), retrying in {check_interval}s...")

                time.sleep(check_interval)

            raise OrchestratorError(
                f"Timeout waiting for SSM connectivity after {max_wait}s. "
                "Ensure instances have IAM role with SSMManagedInstanceCore policy."
            )

        except Exception as e:
            logger.error(f"SSM connectivity check failed: {e}")
            raise

    def run_terraform(self, action: str, var_file: Optional[Path] = None) -> None:
        """Execute Terraform commands"""
        logger.info(f"Running Terraform {action}")

        cmd = ["terraform", "-chdir=" + str(TERRAFORM_DIR), action]

        if action in ["plan", "apply", "destroy"]:
            if var_file and var_file.exists():
                # Check if it's already a tfvars.json file or a YAML config
                if var_file.suffix == '.json':
                    # Already a tfvars file, use directly
                    cmd.extend(["-var-file", str(var_file)])
                else:
                    # YAML config, convert to tfvars
                    config = self.load_config(var_file)
                    tfvars_file = self.experiment_dir / "terraform.tfvars.json"
                    self._generate_tfvars(config, tfvars_file)
                    cmd.extend(["-var-file", str(tfvars_file)])

            if action in ["apply", "destroy"]:
                cmd.append("-auto-approve")

        try:
            # Stream output to console in real-time
            result = subprocess.run(cmd, check=True)
            logger.info(f"Terraform {action} completed successfully")
        except subprocess.CalledProcessError as e:
            logger.error(f"Terraform {action} failed with exit code {e.returncode}")
            raise OrchestratorError(f"Terraform {action} failed") from e

    def _generate_tfvars(self, config: Dict, output_file: Path) -> None:
        """Generate Terraform variables file from YAML config"""
        tfvars = {
            "experiment_id": self.experiment_id,  # Use orchestrator's ID, not config file
            "experiment_name": config["experiment"]["name"],
            "aws_region": config["aws"]["region"],
            "vpc_cidr": config["network"]["vpc_cidr"],
            "public_subnet_cidr": config["network"]["public_subnet_cidr"],
            "allowed_ssh_cidrs": config["network"]["allowed_ssh_cidrs"],
            "ssh_key_name": config["compute"]["ssh_key_name"],
            "zookeeper_count": config["compute"]["zookeeper"]["count"],
            "zookeeper_instance_type": config["compute"]["zookeeper"]["instance_type"],
            "bookkeeper_count": config["compute"]["bookkeeper"]["count"],
            "bookkeeper_instance_type": config["compute"]["bookkeeper"]["instance_type"],
            "broker_count": config["compute"]["broker"]["count"],
            "broker_instance_type": config["compute"]["broker"]["instance_type"],
            "client_count": config["compute"]["client"]["count"],
            "client_instance_type": config["compute"]["client"]["instance_type"],
            "additional_tags": config["experiment"].get("tags", {}),
        }

        # Add optional fields
        if "availability_zone" in config["aws"]:
            tfvars["availability_zone"] = config["aws"]["availability_zone"]

        if "use_spot_instances" in config["aws"]:
            tfvars["use_spot_instances"] = config["aws"]["use_spot_instances"]
            if config["aws"].get("spot_max_price"):
                tfvars["spot_max_price"] = config["aws"]["spot_max_price"]

        # BookKeeper storage
        if "storage" in config["compute"]["bookkeeper"]:
            storage = config["compute"]["bookkeeper"]["storage"]
            tfvars["bookkeeper_volume_size"] = storage["volume_size"]
            tfvars["bookkeeper_volume_type"] = storage["volume_type"]
            if "iops" in storage:
                tfvars["bookkeeper_iops"] = storage["iops"]
            if "throughput" in storage:
                tfvars["bookkeeper_throughput"] = storage["throughput"]

        with open(output_file, 'w') as f:
            json.dump(tfvars, f, indent=2)

        logger.info(f"Generated Terraform variables: {output_file}")

    def get_terraform_output(self, output_name: str) -> str:
        """Get Terraform output value"""
        cmd = ["terraform", "-chdir=" + str(TERRAFORM_DIR), "output", "-raw", output_name]
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        return result.stdout.strip()

    def run_ansible(self, playbook: str, inventory: Optional[str] = None) -> None:
        """Execute Ansible playbook"""
        logger.info(f"Running Ansible playbook: {playbook}")

        # Check if ansible-playbook is available (try pyenv first)
        ansible_path = None

        # Try pyenv environments (using pyenv prefix)
        try:
            result = subprocess.run(
                ["pyenv", "prefix", "pulsar"],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode == 0:
                pyenv_prefix = result.stdout.strip()
                candidate_path = Path(pyenv_prefix) / "bin" / "ansible-playbook"
                if candidate_path.exists():
                    ansible_path = str(candidate_path)
        except FileNotFoundError:
            pass

        # Fallback to system PATH
        if not ansible_path:
            ansible_path = shutil.which("ansible-playbook")

        if not ansible_path:
            raise OrchestratorError(
                "ansible-playbook not found in PATH or pyenv pulsar environment.\n"
                "Please install Ansible in pyenv pulsar environment: pyenv activate pulsar && pip install ansible\n"
                "Or install system-wide: pip install ansible"
            )

        # Get inventory from Terraform if not provided
        if not inventory:
            ansible_inventory = self.get_terraform_output("ansible_inventory")
            inventory_file = self.experiment_dir / "inventory.ini"
            with open(inventory_file, 'w') as f:
                f.write(ansible_inventory)
            inventory = str(inventory_file)

            # Log inventory for debugging
            logger.info("Generated Ansible inventory:")
            for line in ansible_inventory.strip().split('\n'):
                logger.info(f"  {line}")

        # Load Pulsar cluster config
        pulsar_config = self.load_config(CONFIG_DIR / "pulsar-cluster.yaml")

        # Build ansible command
        cmd = [
            ansible_path,  # Use full path
            "-i", inventory,
            str(ANSIBLE_DIR / "playbooks" / playbook),
            "-e", f"@{CONFIG_DIR / 'pulsar-cluster.yaml'}"
        ]

        # Set ANSIBLE_CONFIG to use our ansible.cfg and roles path
        env = os.environ.copy()
        env['ANSIBLE_CONFIG'] = str(ANSIBLE_DIR / "ansible.cfg")
        env['ANSIBLE_ROLES_PATH'] = f"{ANSIBLE_DIR / 'roles'}:~/.ansible/roles:/usr/share/ansible/roles:/etc/ansible/roles"

        # Override pulsar_version from infrastructure config if available
        if self.infrastructure_config and 'pulsar_version' in self.infrastructure_config:
            cmd.extend(["-e", f"pulsar_version={self.infrastructure_config['pulsar_version']}"])
            logger.info(f"Using Pulsar version: {self.infrastructure_config['pulsar_version']}")

        try:
            # Capture output for debugging
            result = subprocess.run(cmd, check=True, env=env, capture_output=True, text=True)
            logger.info("Ansible playbook completed successfully")
            if result.stdout:
                logger.debug(f"Ansible stdout: {result.stdout}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Ansible playbook failed with exit code {e.returncode}")
            if e.stdout:
                logger.error(f"Ansible stdout:\n{e.stdout}")
            if e.stderr:
                logger.error(f"Ansible stderr:\n{e.stderr}")
            raise OrchestratorError("Ansible playbook failed") from e

    def setup(self, config_file: Path, runtime_tags: Optional[Dict[str, str]] = None) -> None:
        """Setup infrastructure and deploy Pulsar cluster"""
        logger.info("Starting setup phase")

        try:
            # Load infrastructure config
            self.infrastructure_config = self.load_config(config_file)

            # Merge runtime tags with config tags
            if runtime_tags:
                config_tags = self.infrastructure_config.get('experiment', {}).get('tags', {})
                merged_tags = {**config_tags, **runtime_tags}  # Runtime tags override config tags
                self.infrastructure_config.setdefault('experiment', {})['tags'] = merged_tags
                logger.info(f"Merged tags: {merged_tags}")

            # Verify Session Manager plugin is installed
            self.verify_ssm_plugin()

            # Initialize Terraform
            self.run_terraform("init")

            # Plan infrastructure
            self.run_terraform("plan", config_file)

            # Apply infrastructure
            self.run_terraform("apply", config_file)

            # Wait for instances to be registered with SSM, then wait for SSH readiness
            aws_region = self.infrastructure_config['aws']['region']
            logger.info("Waiting for instances to register with SSM...")

            # Poll for SSM Online status, then wait 90s for SSH daemon
            self.verify_ssm_connectivity(aws_region, max_wait=300, check_interval=10)

            # Deploy Pulsar cluster via Ansible (using SSM)
            self.run_ansible("deploy.yaml")

            logger.info("Setup phase completed successfully")

        except Exception as e:
            logger.error(f"Setup failed: {e}")
            logger.warning("Initiating automatic cleanup of resources...")
            self.emergency_cleanup()
            raise

    def emergency_cleanup(self, region: str = None) -> None:
        """Emergency cleanup using tag-based resource discovery (doesn't need Terraform state)"""
        logger.warning("=" * 60)
        logger.warning("EMERGENCY CLEANUP: Finding resources by ExperimentID tag")
        logger.warning("=" * 60)

        if not region:
            region = self.infrastructure_config.get('aws', {}).get('region', 'us-west-2') if self.infrastructure_config else 'us-west-2'

        logger.info(f"Searching for resources with ExperimentID: {self.experiment_id} in {region}")

        try:
            resources = get_resources_by_experiment_id(self.experiment_id, region)

            # Check if any resources found
            total_resources = sum(len(v) if isinstance(v, list) else 0 for v in resources.values())

            if total_resources == 0:
                logger.info("No resources found to cleanup")
                return

            logger.warning(f"Found {total_resources} resources to cleanup")
            cleanup_resources(resources, region, dry_run=False)
            logger.info("Emergency cleanup completed")

        except Exception as e:
            logger.error(f"Emergency cleanup failed: {e}")
            logger.error("You may need to manually cleanup resources from AWS console")

    def teardown(self) -> None:
        """Destroy infrastructure"""
        logger.info("Starting teardown phase")

        # Check if Terraform state exists
        state_file = TERRAFORM_DIR / "terraform.tfstate"
        if not state_file.exists():
            logger.error("Terraform state file not found!")
            logger.error(f"Expected: {state_file}")
            logger.error("Attempting emergency cleanup by tags...")
            self.emergency_cleanup()
            return

        # Get AWS region from config or default
        region = self.infrastructure_config.get('aws', {}).get('region', 'us-west-2') if self.infrastructure_config else 'us-west-2'

        # Find tfvars file from setup
        tfvars_file = self.experiment_dir / "terraform.tfvars.json"
        if not tfvars_file.exists():
            logger.warning(f"tfvars file not found: {tfvars_file}")
            logger.warning("Attempting destroy without variable file...")

        # Destroy infrastructure
        self.run_terraform("destroy", tfvars_file if tfvars_file.exists() else None)

        # Verify resources are actually destroyed
        logger.info("Verifying resources are destroyed...")
        ec2_client = boto3.client('ec2', region_name=region)

        try:
            instances = ec2_client.describe_instances(
                Filters=[
                    {'Name': 'tag:ExperimentID', 'Values': [self.experiment_id]},
                    {'Name': 'instance-state-name', 'Values': ['running', 'pending', 'stopping', 'stopped']}
                ]
            )

            remaining = []
            for reservation in instances['Reservations']:
                for instance in reservation['Instances']:
                    remaining.append(f"{instance['InstanceId']} ({instance['State']['Name']})")

            if remaining:
                logger.warning("\n" + "="*60)
                logger.warning("WARNING: Some instances are still running!")
                logger.warning(f"Experiment ID: {self.experiment_id}")
                for inst in remaining:
                    logger.warning(f"  - {inst}")
                logger.warning("\nTo cleanup manually, run:")
                logger.warning(f"  python scripts/cleanup_by_tag.py --experiment-id {self.experiment_id} --execute")
                logger.warning("="*60 + "\n")
            else:
                logger.info("✓ All instances terminated successfully")

        except Exception as e:
            logger.warning(f"Could not verify resource cleanup: {e}")

        logger.info("Teardown phase completed")

    def run_tests(self, test_plan_file: Path) -> None:
        """Execute test plan using AWS SSM SendCommand"""
        logger.info(f"Running tests from plan: {test_plan_file}")

        test_plan = self.load_config(test_plan_file)

        # Get AWS region and client instance ID
        aws_region = self.infrastructure_config['aws']['region']
        client_data_str = self.get_terraform_output("client_instances")
        client_data = json.loads(client_data_str)
        client_instance_id = client_data['ids'][0]

        logger.info(f"Running tests on client instance: {client_instance_id}")
        logger.info("Using SSH-over-SSM for file transfers")

        # Initialize AWS client
        ssm_client = boto3.client('ssm', region_name=aws_region)

        # Create results directory
        results_dir = self.experiment_dir / "benchmark_results"
        results_dir.mkdir(exist_ok=True)

        # Run each test in the test plan
        for idx, test_run in enumerate(test_plan['test_runs']):
            test_name = test_run['name']
            logger.info(f"\n{'='*60}")
            logger.info(f"Running test: {test_name} ({idx + 1}/{len(test_plan['test_runs'])})")
            logger.info(f"{'='*60}\n")

            # Generate workload file from test plan
            workload = self._generate_workload(test_plan['base_workload'], test_run)
            workload_file = self.experiment_dir / f"workload_{test_name}.yaml"

            with open(workload_file, 'w') as f:
                yaml.dump(workload, f)

            # Upload workload file to client instance via SCP
            remote_workload_path = f"/tmp/workload_{test_name}.yaml"
            logger.info(f"Uploading workload to client instance: {remote_workload_path}")
            self._scp_upload(client_instance_id, str(workload_file), remote_workload_path, aws_region)

            # Run benchmark
            result_file = f"/opt/benchmark-results/{test_name}.json"
            benchmark_cmd = (
                f"cd /opt/openmessaging-benchmark/benchmark-framework && "
                f"sudo bin/benchmark --drivers /opt/benchmark-configs/pulsar-driver.yaml "
                f"{remote_workload_path} --output {result_file}"
            )

            logger.info(f"Executing benchmark: {test_name}")
            self._ssm_run_command(
                ssm_client,
                client_instance_id,
                [benchmark_cmd],
                f"Run benchmark {test_name}"
            )

            # Download results from client instance via SCP
            local_result = results_dir / f"{test_name}.json"
            logger.info(f"Downloading results to: {local_result}")
            self._scp_download(client_instance_id, result_file, str(local_result), aws_region)

            logger.info(f"Test '{test_name}' completed. Results saved to {local_result}")

        logger.info(f"\n{'='*60}")
        logger.info(f"All tests completed! Results: {results_dir}")
        logger.info(f"{'='*60}\n")

    def _generate_workload(self, base: Dict, overrides: Dict) -> Dict:
        """Generate OpenMessaging Benchmark workload from test plan"""
        workload = {
            'name': overrides.get('name', base['name']),
            'topics': overrides.get('workload_overrides', {}).get('topics', base['topics']),
            'partitionsPerTopic': overrides.get('workload_overrides', {}).get('partitions_per_topic', base['partitions_per_topic']),
            'messageSize': overrides.get('workload_overrides', {}).get('message_size', base['message_size']),
            'subscriptionsPerTopic': base.get('subscriptions_per_topic', 1),
            'consumerPerSubscription': overrides.get('workload_overrides', {}).get('consumers_per_topic', base.get('consumers_per_topic', 1)),
            'producersPerTopic': overrides.get('workload_overrides', {}).get('producers_per_topic', base.get('producers_per_topic', 1)),
            'consumerBacklogSizeGB': base.get('consumer_backlog_size_gb', 0),
            'testDurationMinutes': overrides.get('workload_overrides', {}).get('test_duration_minutes', base.get('test_duration_minutes', 5)),
            'warmupDurationMinutes': overrides.get('workload_overrides', {}).get('warmup_duration_minutes', base.get('warmup_duration_minutes', 1)),
        }

        # Add producer rate if specified
        if overrides['type'] == 'fixed_rate' and 'producer_rate' in overrides:
            workload['producerRate'] = overrides['producer_rate']

        return workload

    def _ssm_run_command(self, ssm_client, instance_id: str, commands: List[str], description: str) -> Dict:
        """
        Execute commands on an instance using SSM SendCommand and wait for completion

        Args:
            ssm_client: Boto3 SSM client
            instance_id: EC2 instance ID
            commands: List of shell commands to execute
            description: Human-readable description for logging

        Returns:
            Command invocation response with output

        Raises:
            OrchestratorError: If command fails or times out
        """
        logger.info(f"SSM Command: {description}")

        try:
            # Send command
            response = ssm_client.send_command(
                InstanceIds=[instance_id],
                DocumentName='AWS-RunShellScript',
                Parameters={'commands': commands},
                Comment=description,
                TimeoutSeconds=3600,  # 1 hour timeout
            )

            command_id = response['Command']['CommandId']
            logger.info(f"Command ID: {command_id}")

            # Wait for command to complete
            max_attempts = 360  # 30 minutes (5 second intervals)
            attempt = 0

            while attempt < max_attempts:
                time.sleep(5)
                attempt += 1

                try:
                    # Get command invocation status
                    invocation = ssm_client.get_command_invocation(
                        CommandId=command_id,
                        InstanceId=instance_id
                    )

                    status = invocation['Status']

                    if status == 'Success':
                        logger.info(f"Command completed successfully")
                        if invocation.get('StandardOutputContent'):
                            logger.debug(f"Output: {invocation['StandardOutputContent'][:500]}")
                        return invocation

                    elif status in ['Failed', 'Cancelled', 'TimedOut']:
                        error_msg = f"Command {description} failed with status: {status}"
                        if invocation.get('StandardErrorContent'):
                            error_msg += f"\nError: {invocation['StandardErrorContent']}"
                        logger.error(error_msg)
                        raise OrchestratorError(error_msg)

                    elif status in ['Pending', 'InProgress', 'Delayed']:
                        # Still running, continue waiting
                        if attempt % 12 == 0:  # Log every minute
                            logger.info(f"Command still running... (status: {status})")
                        continue

                    else:
                        logger.warning(f"Unknown command status: {status}")
                        continue

                except ssm_client.exceptions.InvocationDoesNotExist:
                    # Command not yet registered, keep waiting
                    if attempt % 12 == 0:
                        logger.info("Waiting for command to be registered...")
                    continue

            # Timeout reached
            raise OrchestratorError(
                f"Command {description} timed out after {max_attempts * 5} seconds"
            )

        except Exception as e:
            if isinstance(e, OrchestratorError):
                raise
            logger.error(f"SSM command execution failed: {e}")
            raise OrchestratorError(f"SSM command failed: {e}") from e

    def _scp_upload(self, instance_id: str, local_path: str, remote_path: str, region: str) -> None:
        """
        Upload a file to an instance using SCP over SSM tunnel

        Args:
            instance_id: EC2 instance ID
            local_path: Local file path
            remote_path: Remote file path on instance
            region: AWS region
        """
        ssh_key = self.infrastructure_config['ssh']['key_name']
        ssh_key_path = Path.home() / ".ssh" / f"{ssh_key}.pem"

        # Use ProxyCommand to tunnel SSH through SSM
        scp_cmd = [
            "scp",
            "-i", str(ssh_key_path),
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", f"ProxyCommand=aws ssm start-session --target {instance_id} --document-name AWS-StartSSHSession --parameters portNumber=%p --region {region}",
            local_path,
            f"ec2-user@{instance_id}:{remote_path}"
        ]

        try:
            result = subprocess.run(scp_cmd, capture_output=True, text=True, check=True)
            logger.info(f"File uploaded successfully: {local_path} -> {remote_path}")
        except subprocess.CalledProcessError as e:
            error_msg = f"SCP upload failed: {e.stderr}"
            logger.error(error_msg)
            raise OrchestratorError(error_msg) from e

    def _scp_download(self, instance_id: str, remote_path: str, local_path: str, region: str) -> None:
        """
        Download a file from an instance using SCP over SSM tunnel

        Args:
            instance_id: EC2 instance ID
            remote_path: Remote file path on instance
            local_path: Local file path
            region: AWS region
        """
        ssh_key = self.infrastructure_config['ssh']['key_name']
        ssh_key_path = Path.home() / ".ssh" / f"{ssh_key}.pem"

        # Use ProxyCommand to tunnel SSH through SSM
        scp_cmd = [
            "scp",
            "-i", str(ssh_key_path),
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", f"ProxyCommand=aws ssm start-session --target {instance_id} --document-name AWS-StartSSHSession --parameters portNumber=%p --region {region}",
            f"ec2-user@{instance_id}:{remote_path}",
            local_path
        ]

        try:
            result = subprocess.run(scp_cmd, capture_output=True, text=True, check=True)
            logger.info(f"File downloaded successfully: {remote_path} -> {local_path}")
        except subprocess.CalledProcessError as e:
            error_msg = f"SCP download failed: {e.stderr}"
            logger.error(error_msg)
            raise OrchestratorError(error_msg) from e

    def generate_report(self) -> None:
        """Generate experiment report"""
        logger.info("Generating report")

        # TODO: Implement report generation
        # This will be implemented with the reporting module
        logger.warning("Report generation not yet implemented")

    def full_lifecycle(self, config_file: Path, test_plan_file: Path, runtime_tags: Optional[Dict[str, str]] = None) -> None:
        """Execute full lifecycle: setup -> test -> report -> teardown"""
        logger.info("Starting full lifecycle")

        try:
            # Setup infrastructure (has its own error handling)
            self.setup(config_file, runtime_tags=runtime_tags)

            # Run tests and generate report, ensuring teardown happens
            try:
                self.run_tests(test_plan_file)
                self.generate_report()
            finally:
                logger.info("Tearing down infrastructure...")
                self.teardown()

            logger.info("Full lifecycle completed successfully")

        except Exception as e:
            logger.error(f"Full lifecycle failed: {e}")
            logger.warning("Resources should have been cleaned up automatically")
            raise

    @staticmethod
    def resolve_experiment_id(experiment_id: str) -> str:
        """Resolve experiment ID, handling 'latest' shortcut"""
        if experiment_id == "latest":
            latest_link = RESULTS_DIR / "latest"
            if not latest_link.exists():
                raise OrchestratorError("No experiments found. 'latest' link does not exist.")
            return latest_link.resolve().name
        return experiment_id

    @staticmethod
    def list_experiments() -> None:
        """List all experiments"""
        if not RESULTS_DIR.exists():
            print("No experiments found.")
            return

        experiments = sorted(
            [d for d in RESULTS_DIR.iterdir() if d.is_dir() and d.name.startswith("exp-")],
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )

        if not experiments:
            print("No experiments found.")
            return

        print("\nAvailable Experiments:")
        print("=" * 60)
        for exp_dir in experiments:
            exp_id = exp_dir.name
            timestamp = datetime.fromtimestamp(exp_dir.stat().st_mtime)

            # Check if this is the latest
            is_latest = ""
            latest_link = RESULTS_DIR / "latest"
            if latest_link.exists() and latest_link.resolve() == exp_dir:
                is_latest = " (latest)"

            print(f"{exp_id:30} {timestamp.strftime('%Y-%m-%d %H:%M:%S')}{is_latest}")
        print("=" * 60)
        print(f"\nTo teardown an experiment: python scripts/orchestrator.py teardown --experiment-id <id>")
        print(f"Or use 'latest': python scripts/orchestrator.py teardown --experiment-id latest\n")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="Pulsar AWS Lab Orchestrator")
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Setup command
    setup_parser = subparsers.add_parser("setup", help="Setup infrastructure and deploy cluster")
    setup_parser.add_argument("--config", type=Path, required=True, help="Infrastructure config file")
    setup_parser.add_argument("--experiment-id", help="Experiment ID (auto-generated if not provided)")
    setup_parser.add_argument("--tag", action="append", metavar="KEY=VALUE", help="Additional tags (can be used multiple times)")

    # Teardown command
    teardown_parser = subparsers.add_parser("teardown", help="Destroy infrastructure")
    teardown_parser.add_argument("--experiment-id", required=True, help="Experiment ID (or 'latest')")

    # List command
    list_parser = subparsers.add_parser("list", help="List all experiments")

    # Run tests command
    run_parser = subparsers.add_parser("run", help="Run test plan")
    run_parser.add_argument("--test-plan", type=Path, required=True, help="Test plan file")
    run_parser.add_argument("--experiment-id", required=True, help="Experiment ID")

    # Report command
    report_parser = subparsers.add_parser("report", help="Generate report")
    report_parser.add_argument("--experiment-id", required=True, help="Experiment ID")

    # Full lifecycle command
    full_parser = subparsers.add_parser("full", help="Execute full lifecycle")
    full_parser.add_argument("--config", type=Path, default=CONFIG_DIR / "infrastructure.yaml", help="Infrastructure config file")
    full_parser.add_argument("--test-plan", type=Path, required=True, help="Test plan file")
    full_parser.add_argument("--experiment-id", help="Experiment ID (auto-generated if not provided)")
    full_parser.add_argument("--tag", action="append", metavar="KEY=VALUE", help="Additional tags (can be used multiple times)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        # Handle list command separately (doesn't need orchestrator)
        if args.command == "list":
            Orchestrator.list_experiments()
            return

        # Resolve experiment ID for commands that need it
        experiment_id = getattr(args, "experiment_id", None)
        if experiment_id and args.command in ["teardown", "run", "report"]:
            experiment_id = Orchestrator.resolve_experiment_id(experiment_id)

        orchestrator = Orchestrator(experiment_id)

        # Parse runtime tags if provided
        runtime_tags = {}
        if hasattr(args, 'tag') and args.tag:
            for tag in args.tag:
                if '=' not in tag:
                    raise OrchestratorError(f"Invalid tag format: {tag}. Expected KEY=VALUE")
                key, value = tag.split('=', 1)
                runtime_tags[key] = value
            logger.info(f"Runtime tags: {runtime_tags}")

        if args.command == "setup":
            orchestrator.setup(args.config, runtime_tags=runtime_tags)
        elif args.command == "teardown":
            orchestrator.teardown()
        elif args.command == "run":
            orchestrator.run_tests(args.test_plan)
        elif args.command == "report":
            orchestrator.generate_report()
        elif args.command == "full":
            orchestrator.full_lifecycle(args.config, args.test_plan, runtime_tags=runtime_tags)

    except OrchestratorError as e:
        logger.error(f"Orchestrator error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
