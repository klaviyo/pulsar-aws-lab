#!/usr/bin/env python3
"""
Pulsar AWS Lab Orchestrator
Main workflow controller for infrastructure, deployment, testing, and teardown
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
RESULTS_DIR = Path.home() / ".pulsar-aws-lab"


class OrchestratorError(Exception):
    """Base exception for orchestrator errors"""
    pass


class Orchestrator:
    """Main orchestrator class for AMI-based deployments"""

    def __init__(self, experiment_id: Optional[str] = None):
        """
        Initialize orchestrator with experiment tracking.

        Args:
            experiment_id: Unique experiment identifier (auto-generated if not provided)
        """
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
        """
        Load YAML configuration file.

        Args:
            config_file: Path to YAML configuration

        Returns:
            Parsed configuration dictionary
        """
        logger.info(f"Loading configuration from {config_file}")
        with open(config_file, 'r') as f:
            return yaml.safe_load(f)

    def validate_ami_exists(self, region: str, ami_name_pattern: str = "pulsar-base-*") -> Optional[str]:
        """
        Validate that the required AMI exists in the region.

        Args:
            region: AWS region to check
            ami_name_pattern: AMI name pattern to search for

        Returns:
            AMI ID if found, None otherwise

        Raises:
            OrchestratorError: If AMI not found or validation fails
        """
        logger.info(f"Validating AMI availability in {region}...")
        logger.info(f"Searching for AMI with pattern: {ami_name_pattern}")

        ec2_client = boto3.client('ec2', region_name=region)

        try:
            # Search for AMI by name pattern (owned by self)
            response = ec2_client.describe_images(
                Filters=[
                    {'Name': 'name', 'Values': [ami_name_pattern]},
                    {'Name': 'state', 'Values': ['available']}
                ],
                Owners=['self']  # Only search AMIs owned by this account
            )

            images = response.get('Images', [])

            if not images:
                # CHANGED: Provide detailed error message with troubleshooting steps
                error_msg = (
                    f"No AMI found matching pattern '{ami_name_pattern}' in {region}.\n"
                    f"Please ensure you have built the Pulsar base AMI using Packer.\n"
                    f"Run: cd packer && packer build pulsar-base.pkr.hcl\n"
                    f"Or check that the AMI exists in region {region}"
                )
                logger.error(error_msg)
                raise OrchestratorError(error_msg)

            # Sort by creation date (newest first)
            images.sort(key=lambda x: x['CreationDate'], reverse=True)
            latest_ami = images[0]
            ami_id = latest_ami['ImageId']
            ami_name = latest_ami['Name']
            creation_date = latest_ami['CreationDate']

            logger.info(f"✓ Found AMI: {ami_name} ({ami_id})")
            logger.info(f"  Created: {creation_date}")
            logger.info(f"  State: {latest_ami['State']}")

            if len(images) > 1:
                logger.info(f"  Note: Found {len(images)} matching AMIs, using the latest")

            return ami_id

        except Exception as e:
            if isinstance(e, OrchestratorError):
                raise
            logger.error(f"AMI validation failed: {e}")
            raise OrchestratorError(f"Failed to validate AMI: {e}") from e

    def run_terraform(self, action: str, var_file: Optional[Path] = None) -> None:
        """
        Execute Terraform commands.

        Args:
            action: Terraform action (init, plan, apply, destroy)
            var_file: Optional path to variables file
        """
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
        """
        Generate Terraform variables file from YAML config.

        Args:
            config: Parsed infrastructure configuration
            output_file: Path to output tfvars.json file
        """
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

    def _discover_instance_ids(self, region: str) -> List[str]:
        """
        Discover EC2 instance IDs tagged with the experiment.

        Args:
            region: AWS region

        Returns:
            List of instance IDs
        """
        ec2_client = boto3.client('ec2', region_name=region)

        instance_ids: List[str] = []
        next_token = None

        try:
            while True:
                request_kwargs = {
                    'Filters': [
                        {'Name': 'tag:ExperimentID', 'Values': [self.experiment_id]},
                        {'Name': 'instance-state-name', 'Values': ['pending', 'running']}
                    ]
                }
                if next_token:
                    request_kwargs['NextToken'] = next_token

                response = ec2_client.describe_instances(**request_kwargs)

                for reservation in response.get('Reservations', []):
                    for instance in reservation.get('Instances', []):
                        instance_ids.append(instance['InstanceId'])

                next_token = response.get('NextToken')
                if not next_token:
                    break

        except Exception as exc:
            logger.warning(f"Failed to discover instance IDs via EC2 API: {exc}")

        return sorted(instance_ids)

    def get_terraform_output(self, output_name: str) -> str:
        """
        Get Terraform output value.

        Args:
            output_name: Name of Terraform output

        Returns:
            Output value as string
        """
        cmd = ["terraform", "-chdir=" + str(TERRAFORM_DIR), "output", "-raw", output_name]
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        return result.stdout.strip()

    def wait_for_cluster(self, region: str, timeout_seconds: int = 600) -> None:
        """
        Wait for all cluster instances to be ready with exponential backoff.
        Uses AWS SSM RunCommand for health checks.

        Args:
            region: AWS region
            timeout_seconds: Maximum time to wait (default: 10 minutes)

        Raises:
            OrchestratorError: If cluster doesn't become ready within timeout
        """
        logger.info("="*60)
        logger.info("WAITING FOR CLUSTER TO BE READY")
        logger.info("="*60)

        ssm_client = boto3.client('ssm', region_name=region)
        ec2_client = boto3.client('ec2', region_name=region)

        start_time = time.time()
        backoff_seconds = 5  # Initial backoff
        max_backoff = 30

        # Step 1: Wait for all instances to be in 'running' state
        logger.info("Step 1/3: Waiting for EC2 instances to reach 'running' state...")
        instance_ids = []

        while time.time() - start_time < timeout_seconds:
            instance_ids = self._discover_instance_ids(region)

            if not instance_ids:
                logger.warning("No instances found yet, retrying...")
                time.sleep(backoff_seconds)
                continue

            # Check instance states
            response = ec2_client.describe_instances(InstanceIds=instance_ids)
            all_running = True
            instance_states = {}

            for reservation in response['Reservations']:
                for instance in reservation['Instances']:
                    instance_id = instance['InstanceId']
                    state = instance['State']['Name']
                    instance_states[instance_id] = state
                    if state != 'running':
                        all_running = False

            logger.info(f"Found {len(instance_ids)} instances:")
            for instance_id, state in instance_states.items():
                logger.info(f"  {instance_id}: {state}")

            if all_running:
                logger.info("✓ All instances are running")
                break

            elapsed = int(time.time() - start_time)
            logger.info(f"Waiting for instances to start... ({elapsed}s elapsed)")
            time.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 1.5, max_backoff)

        if time.time() - start_time >= timeout_seconds:
            raise OrchestratorError(
                f"Timeout waiting for instances to reach 'running' state after {timeout_seconds}s"
            )

        # Step 2: Wait for SSM agent registration
        logger.info("\nStep 2/3: Waiting for SSM agent registration...")
        backoff_seconds = 5

        while time.time() - start_time < timeout_seconds:
            response = ssm_client.describe_instance_information(
                Filters=[
                    {'Key': 'tag:ExperimentID', 'Values': [self.experiment_id]}
                ]
            )

            managed_instances = response.get('InstanceInformationList', [])
            online_instances = [i for i in managed_instances if i.get('PingStatus') == 'Online']

            logger.info(f"SSM status: {len(online_instances)}/{len(instance_ids)} instances online")

            if len(online_instances) == len(instance_ids):
                logger.info("✓ All instances registered with SSM")
                for instance in managed_instances:
                    logger.info(f"  {instance['InstanceId']}: {instance.get('PingStatus')}")
                break

            elapsed = int(time.time() - start_time)
            logger.info(f"Waiting for SSM registration... ({elapsed}s elapsed)")
            time.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 1.5, max_backoff)

        if time.time() - start_time >= timeout_seconds:
            raise OrchestratorError(
                f"Timeout waiting for SSM agent registration after {timeout_seconds}s"
            )

        # Step 3: Wait for systemd services to be active
        logger.info("\nStep 3/3: Waiting for Pulsar services to be active...")

        # Get instance details by component
        component_instances = self._get_instances_by_component(region)

        # Define service checks by component
        service_checks = {
            'zookeeper': ['zookeeper.service'],
            'bookkeeper': ['bookkeeper.service'],
            'broker': ['pulsar-broker.service'],
            'client': []  # No critical services on client nodes
        }

        backoff_seconds = 10

        while time.time() - start_time < timeout_seconds:
            all_services_ready = True

            for component, instances in component_instances.items():
                services = service_checks.get(component, [])

                if not services:
                    logger.info(f"Component '{component}': No service checks required")
                    continue

                for instance_id in instances:
                    for service_name in services:
                        # Check if service is active using SSM RunCommand
                        is_active, status_msg = self._check_service_status(
                            ssm_client, instance_id, service_name
                        )

                        if is_active:
                            logger.info(f"✓ {instance_id} ({component}): {service_name} is active")
                        else:
                            logger.warning(
                                f"✗ {instance_id} ({component}): {service_name} not active ({status_msg})"
                            )
                            all_services_ready = False

            if all_services_ready:
                logger.info("\n✓ All Pulsar services are active and ready!")
                break

            elapsed = int(time.time() - start_time)
            logger.info(f"Waiting for services to start... ({elapsed}s elapsed)")
            time.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 1.5, max_backoff)

        if time.time() - start_time >= timeout_seconds:
            raise OrchestratorError(
                f"Timeout waiting for Pulsar services to be ready after {timeout_seconds}s"
            )

        # Step 4: Verify service health endpoints
        logger.info("\nVerifying service health endpoints...")
        self._verify_health_endpoints(region, component_instances)

        total_time = int(time.time() - start_time)
        logger.info("="*60)
        logger.info(f"CLUSTER READY! (Total time: {total_time}s)")
        logger.info("="*60)

    def _get_instances_by_component(self, region: str) -> Dict[str, List[str]]:
        """
        Get instance IDs organized by component type.

        Args:
            region: AWS region

        Returns:
            Dictionary mapping component names to lists of instance IDs
        """
        ec2_client = boto3.client('ec2', region_name=region)

        response = ec2_client.describe_instances(
            Filters=[
                {'Name': 'tag:ExperimentID', 'Values': [self.experiment_id]},
                {'Name': 'instance-state-name', 'Values': ['running']}
            ]
        )

        component_instances = {
            'zookeeper': [],
            'bookkeeper': [],
            'broker': [],
            'client': []
        }

        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                instance_id = instance['InstanceId']
                # Find Component tag
                component = None
                for tag in instance.get('Tags', []):
                    if tag['Key'] == 'Component':
                        component = tag['Value'].lower()
                        break

                if component in component_instances:
                    component_instances[component].append(instance_id)

        return component_instances

    def _check_service_status(
        self,
        ssm_client,
        instance_id: str,
        service_name: str,
        timeout_seconds: int = 30
    ) -> Tuple[bool, str]:
        """
        Check if a systemd service is active using SSM RunCommand.

        Args:
            ssm_client: Boto3 SSM client
            instance_id: EC2 instance ID
            service_name: Systemd service name
            timeout_seconds: Command timeout

        Returns:
            Tuple of (is_active: bool, status_message: str)
        """
        try:
            # Send command to check service status
            response = ssm_client.send_command(
                InstanceIds=[instance_id],
                DocumentName='AWS-RunShellScript',
                Parameters={
                    'commands': [f'systemctl is-active {service_name}']
                },
                TimeoutSeconds=timeout_seconds
            )

            command_id = response['Command']['CommandId']

            # Wait for command to complete (short timeout for health checks)
            max_attempts = 10  # 10 attempts * 2 seconds = 20 seconds
            for attempt in range(max_attempts):
                time.sleep(2)

                try:
                    invocation = ssm_client.get_command_invocation(
                        CommandId=command_id,
                        InstanceId=instance_id
                    )

                    status = invocation['Status']

                    if status == 'Success':
                        output = invocation.get('StandardOutputContent', '').strip()
                        return (output == 'active', output)

                    elif status in ['Failed', 'Cancelled', 'TimedOut']:
                        error = invocation.get('StandardErrorContent', 'Unknown error')
                        return (False, f"{status}: {error}")

                    # Still running, wait for next attempt
                    continue

                except ssm_client.exceptions.InvocationDoesNotExist:
                    # Command not yet registered
                    continue

            return (False, "Timeout waiting for status check")

        except Exception as e:
            logger.warning(f"Service status check failed for {instance_id}/{service_name}: {e}")
            return (False, str(e))

    def _verify_health_endpoints(
        self,
        region: str,
        component_instances: Dict[str, List[str]]
    ) -> None:
        """
        Verify service health endpoints are responding.

        Args:
            region: AWS region
            component_instances: Dictionary mapping components to instance IDs
        """
        ssm_client = boto3.client('ssm', region_name=region)

        # Define health checks by component
        health_checks = {
            'zookeeper': ('localhost', 2181, 'ruok'),  # ZooKeeper four-letter word
            'bookkeeper': ('localhost', 3181, None),    # BookKeeper client port (TCP check)
            'broker': ('localhost', 8080, '/admin/v2/brokers/health'),  # Broker HTTP endpoint
        }

        for component, instances in component_instances.items():
            if component not in health_checks:
                continue

            host, port, path = health_checks[component]

            for instance_id in instances:
                logger.info(f"Checking health endpoint for {instance_id} ({component})...")

                # Build health check command based on component
                if component == 'zookeeper':
                    # ZooKeeper: send "ruok" via netcat
                    cmd = f"echo {path} | nc {host} {port}"
                elif component == 'bookkeeper':
                    # BookKeeper: simple TCP connection test
                    cmd = f"timeout 5 nc -zv {host} {port} 2>&1"
                else:
                    # HTTP endpoints: use curl
                    cmd = f"curl -f -s -o /dev/null -w '%{{http_code}}' http://{host}:{port}{path}"

                try:
                    response = ssm_client.send_command(
                        InstanceIds=[instance_id],
                        DocumentName='AWS-RunShellScript',
                        Parameters={'commands': [cmd]},
                        TimeoutSeconds=30
                    )

                    command_id = response['Command']['CommandId']

                    # Wait for result
                    for attempt in range(10):
                        time.sleep(2)

                        try:
                            invocation = ssm_client.get_command_invocation(
                                CommandId=command_id,
                                InstanceId=instance_id
                            )

                            if invocation['Status'] == 'Success':
                                output = invocation.get('StandardOutputContent', '').strip()

                                # Validate response
                                if component == 'zookeeper' and 'imok' in output:
                                    logger.info(f"  ✓ ZooKeeper health check passed")
                                elif component == 'bookkeeper' and 'succeeded' in output:
                                    logger.info(f"  ✓ BookKeeper port check passed")
                                elif component == 'broker' and output == '200':
                                    logger.info(f"  ✓ Broker health endpoint returned 200")
                                else:
                                    logger.warning(f"  ✗ Unexpected health check response: {output}")

                                break

                            elif invocation['Status'] in ['Failed', 'Cancelled', 'TimedOut']:
                                logger.warning(
                                    f"  ✗ Health check failed: {invocation.get('StandardErrorContent', 'Unknown')}"
                                )
                                break

                        except ssm_client.exceptions.InvocationDoesNotExist:
                            continue

                except Exception as e:
                    logger.warning(f"  ✗ Health check error: {e}")

    def setup(self, config_file: Path, runtime_tags: Optional[Dict[str, str]] = None) -> None:
        """
        Setup infrastructure using Terraform and wait for AMI-based cluster to be ready.

        Args:
            config_file: Path to infrastructure configuration YAML
            runtime_tags: Optional additional tags to apply

        Raises:
            OrchestratorError: If setup fails
        """
        logger.info("Starting setup phase (AMI-based deployment)")

        try:
            # Load infrastructure config
            self.infrastructure_config = self.load_config(config_file)

            # Merge runtime tags with config tags
            if runtime_tags:
                config_tags = self.infrastructure_config.get('experiment', {}).get('tags', {})
                merged_tags = {**config_tags, **runtime_tags}  # Runtime tags override config tags
                self.infrastructure_config.setdefault('experiment', {})['tags'] = merged_tags
                logger.info(f"Merged tags: {merged_tags}")

            # CHANGED: Validate AMI exists before Terraform
            aws_region = self.infrastructure_config['aws']['region']
            ami_id = self.validate_ami_exists(aws_region, ami_name_pattern="pulsar-base-*")
            logger.info(f"Using AMI: {ami_id}")

            # Initialize Terraform
            self.run_terraform("init")

            # Plan infrastructure
            self.run_terraform("plan", config_file)

            # Apply infrastructure
            logger.info("Provisioning infrastructure with Terraform...")
            self.run_terraform("apply", config_file)

            # Wait for AMI-based cluster to be ready
            self.wait_for_cluster(aws_region, timeout_seconds=600)

            logger.info("="*60)
            logger.info("SETUP PHASE COMPLETED SUCCESSFULLY")
            logger.info("="*60)

        except Exception as e:
            logger.error(f"Setup failed: {e}")
            logger.warning("Initiating automatic cleanup of resources...")
            self.emergency_cleanup()
            raise

    def emergency_cleanup(self, region: str = None) -> None:
        """
        Emergency cleanup using tag-based resource discovery (doesn't need Terraform state).

        Args:
            region: AWS region (auto-detected if not provided)
        """
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
        """
        Destroy infrastructure using Terraform.
        """
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
        """
        Execute test plan using AWS SSM SendCommand.

        Args:
            test_plan_file: Path to test plan YAML file
        """
        logger.info(f"Running tests from plan: {test_plan_file}")

        test_plan = self.load_config(test_plan_file)

        # Get AWS region and client instance ID
        aws_region = self.infrastructure_config['aws']['region']
        client_data_str = self.get_terraform_output("client_instances")
        client_data = json.loads(client_data_str)
        client_instance_id = client_data['ids'][0]

        logger.info(f"Running tests on client instance: {client_instance_id}")

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

            # Upload workload file to client instance
            remote_workload_path = f"/tmp/workload_{test_name}.yaml"
            logger.info(f"Uploading workload to client instance: {remote_workload_path}")

            # Use SSM SendCommand to write file (avoiding SCP complexity)
            with open(workload_file, 'r') as f:
                workload_content = f.read()

            upload_cmd = f"cat > {remote_workload_path} << 'EOF'\n{workload_content}\nEOF"

            self._ssm_run_command(
                ssm_client,
                client_instance_id,
                [upload_cmd],
                f"Upload workload {test_name}"
            )

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

            # Download results using SSM
            local_result = results_dir / f"{test_name}.json"
            logger.info(f"Downloading results to: {local_result}")

            download_invocation = self._ssm_run_command(
                ssm_client,
                client_instance_id,
                [f"cat {result_file}"],
                f"Download results {test_name}"
            )

            # Write results to local file
            result_content = download_invocation.get('StandardOutputContent', '')
            with open(local_result, 'w') as f:
                f.write(result_content)

            logger.info(f"Test '{test_name}' completed. Results saved to {local_result}")

        logger.info(f"\n{'='*60}")
        logger.info(f"All tests completed! Results: {results_dir}")
        logger.info(f"{'='*60}\n")

    def _generate_workload(self, base: Dict, overrides: Dict) -> Dict:
        """
        Generate OpenMessaging Benchmark workload from test plan.

        Args:
            base: Base workload configuration
            overrides: Test-specific overrides

        Returns:
            Complete workload configuration
        """
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

    def _ssm_run_command(
        self,
        ssm_client,
        instance_id: str,
        commands: List[str],
        description: str
    ) -> Dict:
        """
        Execute commands on an instance using SSM SendCommand and wait for completion.

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

    def generate_report(self) -> None:
        """Generate experiment report"""
        logger.info("Generating report")

        # TODO: Implement report generation
        # This will be implemented with the reporting module
        logger.warning("Report generation not yet implemented")

    def full_lifecycle(
        self,
        config_file: Path,
        test_plan_file: Path,
        runtime_tags: Optional[Dict[str, str]] = None
    ) -> None:
        """
        Execute full lifecycle: setup -> test -> report -> teardown.

        Args:
            config_file: Infrastructure configuration path
            test_plan_file: Test plan configuration path
            runtime_tags: Optional runtime tags to apply

        Raises:
            OrchestratorError: If any phase fails
        """
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
        """
        Resolve experiment ID, handling 'latest' shortcut.

        Args:
            experiment_id: Experiment ID or 'latest'

        Returns:
            Resolved experiment ID

        Raises:
            OrchestratorError: If 'latest' link doesn't exist
        """
        if experiment_id == "latest":
            latest_link = RESULTS_DIR / "latest"
            if not latest_link.exists():
                raise OrchestratorError("No experiments found. 'latest' link does not exist.")
            return latest_link.resolve().name
        return experiment_id

    @staticmethod
    def list_experiments() -> None:
        """List all experiments with timestamps"""
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
    parser = argparse.ArgumentParser(description="Pulsar AWS Lab Orchestrator (AMI-based)")
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
