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

    def ensure_ssh_key(self, key_name: str, region: str) -> None:
        """Ensure SSH key pair exists in AWS, create if not"""
        logger.info(f"Checking SSH key pair: {key_name}")

        ec2_client = boto3.client('ec2', region_name=region)
        ssh_dir = Path.home() / ".ssh"
        ssh_dir.mkdir(mode=0o700, exist_ok=True)
        private_key_path = ssh_dir / f"{key_name}.pem"

        try:
            # Check if key exists in AWS
            ec2_client.describe_key_pairs(KeyNames=[key_name])
            logger.info(f"SSH key pair '{key_name}' already exists in AWS")

            # Verify local private key exists
            if not private_key_path.exists():
                logger.warning(f"Private key not found locally at {private_key_path}")
                logger.warning("You may need to download it from AWS or create a new key pair")

        except ec2_client.exceptions.ClientError as e:
            if 'InvalidKeyPair.NotFound' in str(e):
                logger.info(f"Creating SSH key pair: {key_name}")

                # Create key pair
                response = ec2_client.create_key_pair(KeyName=key_name)

                # Save private key
                with open(private_key_path, 'w') as f:
                    f.write(response['KeyMaterial'])

                # Set correct permissions
                private_key_path.chmod(0o400)

                logger.info(f"SSH key pair created and saved to {private_key_path}")
            else:
                raise OrchestratorError(f"Error checking SSH key: {e}") from e

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
            "experiment_id": config["experiment"]["id"],
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

        # Check if ansible-playbook is available
        ansible_path = shutil.which("ansible-playbook")
        if not ansible_path:
            raise OrchestratorError(
                "ansible-playbook not found in PATH.\n"
                "Please install Ansible: pip install ansible\n"
                "Or: apt-get install ansible (Ubuntu) / brew install ansible (macOS)"
            )

        # Get inventory from Terraform if not provided
        if not inventory:
            ansible_inventory = self.get_terraform_output("ansible_inventory")
            inventory_file = self.experiment_dir / "inventory.ini"
            with open(inventory_file, 'w') as f:
                f.write(ansible_inventory)
            inventory = str(inventory_file)

        # Load Pulsar cluster config
        pulsar_config = self.load_config(CONFIG_DIR / "pulsar-cluster.yaml")

        # Build ansible command
        cmd = [
            ansible_path,  # Use full path
            "-i", inventory,
            str(ANSIBLE_DIR / "playbooks" / playbook),
            "-e", f"@{CONFIG_DIR / 'pulsar-cluster.yaml'}"
        ]

        # Override pulsar_version from infrastructure config if available
        if self.infrastructure_config and 'pulsar_version' in self.infrastructure_config:
            cmd.extend(["-e", f"pulsar_version={self.infrastructure_config['pulsar_version']}"])
            logger.info(f"Using Pulsar version: {self.infrastructure_config['pulsar_version']}")

        try:
            # Stream output to console in real-time
            result = subprocess.run(cmd, check=True)
            logger.info("Ansible playbook completed successfully")
        except subprocess.CalledProcessError as e:
            logger.error(f"Ansible playbook failed with exit code {e.returncode}")
            raise OrchestratorError("Ansible playbook failed") from e

    def setup(self, config_file: Path) -> None:
        """Setup infrastructure and deploy Pulsar cluster"""
        logger.info("Starting setup phase")

        # Load infrastructure config
        self.infrastructure_config = self.load_config(config_file)

        # Ensure SSH key exists
        ssh_key_name = self.infrastructure_config['compute']['ssh_key_name']
        aws_region = self.infrastructure_config['aws']['region']
        self.ensure_ssh_key(ssh_key_name, aws_region)

        # Initialize Terraform
        self.run_terraform("init")

        # Plan infrastructure
        self.run_terraform("plan", config_file)

        # Apply infrastructure
        self.run_terraform("apply", config_file)

        # Wait for instances to be ready
        logger.info("Waiting for instances to be ready...")
        time.sleep(30)

        # Deploy Pulsar cluster
        self.run_ansible("deploy.yaml")

        logger.info("Setup phase completed successfully")

    def teardown(self) -> None:
        """Destroy infrastructure"""
        logger.info("Starting teardown phase")

        # Check if Terraform state exists
        state_file = TERRAFORM_DIR / "terraform.tfstate"
        if not state_file.exists():
            logger.error("Terraform state file not found!")
            logger.error(f"Expected: {state_file}")
            logger.error("Cannot destroy infrastructure without state file.")
            logger.error("\nTo manually cleanup, use AWS console or CLI:")
            logger.error(f"  aws ec2 describe-instances --filters 'Name=tag:ExperimentID,Values={self.experiment_id}'")
            raise OrchestratorError("Terraform state file not found")

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
        """Execute test plan"""
        logger.info(f"Running tests from plan: {test_plan_file}")

        test_plan = self.load_config(test_plan_file)

        # TODO: Implement test execution
        # This will be implemented with the test runner module
        logger.warning("Test execution not yet implemented")

    def generate_report(self) -> None:
        """Generate experiment report"""
        logger.info("Generating report")

        # TODO: Implement report generation
        # This will be implemented with the reporting module
        logger.warning("Report generation not yet implemented")

    def full_lifecycle(self, config_file: Path, test_plan_file: Path) -> None:
        """Execute full lifecycle: setup -> test -> report -> teardown"""
        logger.info("Starting full lifecycle")

        # Setup infrastructure
        self.setup(config_file)

        # Run tests (not yet implemented)
        logger.warning("\n" + "="*60)
        logger.warning("NOTE: Test execution is not yet implemented!")
        logger.warning("Infrastructure will remain running for manual testing.")
        logger.warning("To teardown manually, run:")
        logger.warning(f"  python scripts/orchestrator.py teardown --experiment-id {self.experiment_id}")
        logger.warning("="*60 + "\n")

        # Uncomment when tests are implemented:
        # try:
        #     self.run_tests(test_plan_file)
        #     self.generate_report()
        # finally:
        #     self.teardown()

        logger.info("Setup completed - infrastructure is running")

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

        if args.command == "setup":
            orchestrator.setup(args.config)
        elif args.command == "teardown":
            orchestrator.teardown()
        elif args.command == "run":
            orchestrator.run_tests(args.test_plan)
        elif args.command == "report":
            orchestrator.generate_report()
        elif args.command == "full":
            orchestrator.full_lifecycle(args.config, args.test_plan)

    except OrchestratorError as e:
        logger.error(f"Orchestrator error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
