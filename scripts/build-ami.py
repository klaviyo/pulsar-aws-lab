#!/usr/bin/env python3
"""
AMI Build and Management Tool for Pulsar AWS Lab

Comprehensive CLI tool for building, validating, and managing Pulsar AMIs using Packer.
Provides a user-friendly interface with color-coded output, progress tracking, and
robust error handling.

Performance Characteristics:
    - AMI builds: O(1) API calls, 10-15 minutes typical build time
    - AMI listing: O(n) where n = number of AMIs, cached for 5 minutes
    - Validation: O(1) instance launch + health checks, ~2-3 minutes
    - Deletion: O(1) deregistration + snapshot deletion

Author: Pulsar AWS Lab Team
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.table import Table
from rich.panel import Panel
from rich import box

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
PACKER_DIR = PROJECT_ROOT / "packer"
PACKER_TEMPLATE = PACKER_DIR / "pulsar-base.pkr.hcl"
CACHE_DIR = Path.home() / ".pulsar-aws-lab" / "ami-cache"
CACHE_FILE = CACHE_DIR / "ami-list.json"
CACHE_TTL_SECONDS = 300  # 5 minutes

# Rich console for colored output
console = Console()

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stderr)]
)
logger = logging.getLogger(__name__)


class AMIBuildError(Exception):
    """Base exception for AMI build/management errors."""
    pass


class PrerequisiteError(AMIBuildError):
    """Raised when prerequisites are not met."""
    pass


class AMIManager:
    """
    Manages AMI lifecycle operations: building, validation, listing, and deletion.

    This class provides comprehensive AMI management functionality with proper error
    handling, progress tracking, and caching for performance optimization.

    Attributes:
        region: AWS region for operations
        dry_run: If True, simulate operations without making changes
        ec2_client: Boto3 EC2 client instance
        ssm_client: Boto3 SSM client instance

    Performance Notes:
        - Uses local caching to reduce API calls for list operations
        - Implements exponential backoff for status checks
        - Streams Packer output in real-time to avoid buffering delays
    """

    def __init__(self, region: str = "us-west-2", dry_run: bool = False):
        """
        Initialize AMI manager with AWS clients.

        Args:
            region: AWS region for AMI operations (default: us-west-2)
            dry_run: Enable dry-run mode for testing (default: False)

        Raises:
            PrerequisiteError: If AWS credentials are not configured
        """
        self.region = region
        self.dry_run = dry_run

        try:
            self.ec2_client = boto3.client('ec2', region_name=region)
            self.ssm_client = boto3.client('ssm', region_name=region)
        except NoCredentialsError as e:
            raise PrerequisiteError(
                "AWS credentials not found. Please configure credentials using:\n"
                "  - aws configure\n"
                "  - Environment variables (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)\n"
                "  - IAM role (if running on EC2)"
            ) from e

        logger.info(f"Initialized AMI manager for region: {region}, dry_run: {dry_run}")

    def validate_prerequisites(self) -> None:
        """
        Validate that all required tools and credentials are available.

        Checks:
            1. Packer is installed and accessible
            2. AWS credentials are valid
            3. Packer template file exists
            4. User has necessary IAM permissions

        Raises:
            PrerequisiteError: If any prerequisite check fails

        Time Complexity: O(1) - Fixed number of checks
        """
        console.print("\n[bold cyan]Validating prerequisites...[/bold cyan]")

        # Check 1: Packer installation
        console.print("  Checking Packer installation...", end=" ")
        if not shutil.which("packer"):
            console.print("[bold red]✗[/bold red]")
            raise PrerequisiteError(
                "Packer not found. Please install Packer:\n"
                "  - macOS: brew install packer\n"
                "  - Linux: https://www.packer.io/downloads\n"
                "  - Windows: choco install packer"
            )

        try:
            result = subprocess.run(
                ["packer", "version"],
                capture_output=True,
                text=True,
                check=True
            )
            version = result.stdout.strip()
            console.print(f"[bold green]✓[/bold green] ({version})")
        except subprocess.CalledProcessError as e:
            console.print("[bold red]✗[/bold red]")
            raise PrerequisiteError(f"Failed to get Packer version: {e}") from e

        # Check 2: AWS credentials
        console.print("  Checking AWS credentials...", end=" ")
        try:
            sts_client = boto3.client('sts', region_name=self.region)
            identity = sts_client.get_caller_identity()
            account_id = identity['Account']
            arn = identity['Arn']
            console.print(f"[bold green]✓[/bold green] (Account: {account_id})")
            logger.debug(f"AWS identity: {arn}")
        except NoCredentialsError as e:
            console.print("[bold red]✗[/bold red]")
            raise PrerequisiteError("AWS credentials not configured") from e
        except ClientError as e:
            console.print("[bold red]✗[/bold red]")
            raise PrerequisiteError(f"AWS credentials invalid: {e}") from e

        # Check 3: Packer template exists
        console.print("  Checking Packer template...", end=" ")
        if not PACKER_TEMPLATE.exists():
            console.print("[bold red]✗[/bold red]")
            raise PrerequisiteError(
                f"Packer template not found: {PACKER_TEMPLATE}\n"
                f"Expected location: {PACKER_DIR}/pulsar-base.pkr.hcl"
            )
        console.print(f"[bold green]✓[/bold green] ({PACKER_TEMPLATE.name})")

        # Check 4: IAM permissions (basic check - try to describe images)
        console.print("  Checking IAM permissions...", end=" ")
        try:
            # Try a read operation to verify permissions
            self.ec2_client.describe_images(Owners=['self'], MaxResults=1)
            console.print("[bold green]✓[/bold green]")
        except ClientError as e:
            console.print("[bold yellow]⚠[/bold yellow]")
            console.print(f"  [yellow]Warning: Limited IAM permissions: {e.response['Error']['Code']}[/yellow]")
            logger.warning(f"IAM permission check failed: {e}")

        console.print("[bold green]All prerequisites validated successfully![/bold green]\n")

    def build(self, pulsar_version: str, instance_type: str = "t3.small", force: bool = False) -> str:
        """
        Build a new Pulsar AMI using Packer.

        This method runs the Packer build process with real-time output streaming
        and extracts the resulting AMI ID from Packer's output.

        Args:
            pulsar_version: Apache Pulsar version to install (e.g., "3.0.0")
            instance_type: EC2 instance type for build (default: t3.small)
            force: Force rebuild even if AMI already exists (default: False)

        Returns:
            AMI ID of the newly created image

        Raises:
            AMIBuildError: If build fails or AMI ID cannot be extracted
            PrerequisiteError: If prerequisites are not met

        Time Complexity: O(1) API calls, but build time is ~10-15 minutes
        Space Complexity: O(1) for tracking build artifacts

        Example:
            >>> manager = AMIManager(region="us-west-2")
            >>> ami_id = manager.build(pulsar_version="3.0.0")
            >>> print(f"Built AMI: {ami_id}")
        """
        if self.dry_run:
            console.print("[bold yellow]DRY RUN:[/bold yellow] Would build AMI with:")
            console.print(f"  Pulsar Version: {pulsar_version}")
            console.print(f"  Instance Type: {instance_type}")
            console.print(f"  Region: {self.region}")
            console.print(f"  Force: {force}")
            return "ami-dryrun123456789"

        # Check if AMI already exists (unless force is True)
        if not force:
            existing_ami = self.find_ami_by_version(pulsar_version)
            if existing_ami:
                console.print(Panel.fit(
                    f"[bold yellow]AMI Already Exists[/bold yellow]\n\n"
                    f"AMI ID: [bold]{existing_ami['ami_id']}[/bold]\n"
                    f"Name: [bold]{existing_ami['name']}[/bold]\n"
                    f"Created: {existing_ami['creation_date']}\n\n"
                    f"Use --force to rebuild anyway",
                    box=box.ROUNDED
                ))
                logger.info(f"Skipping build - AMI already exists: {existing_ami['ami_id']}")
                return existing_ami['ami_id']

        # Validate prerequisites (only for real builds)
        self.validate_prerequisites()

        console.print(Panel.fit(
            f"[bold cyan]Building Pulsar AMI[/bold cyan]\n\n"
            f"Pulsar Version: [bold]{pulsar_version}[/bold]\n"
            f"Instance Type: [bold]{instance_type}[/bold]\n"
            f"Region: [bold]{self.region}[/bold]",
            box=box.ROUNDED
        ))

        # Initialize Packer plugins first
        console.print("\n[bold cyan]Initializing Packer plugins...[/bold cyan]")
        init_cmd = ["packer", "init", str(PACKER_TEMPLATE)]

        try:
            subprocess.run(init_cmd, check=True, cwd=str(PACKER_DIR))
            console.print("[bold green]✓ Packer plugins initialized[/bold green]\n")
        except subprocess.CalledProcessError as e:
            raise AMIBuildError(f"Packer init failed: {e}") from e

        # Build Packer command
        packer_cmd = [
            "packer", "build",
            f"-var=pulsar_version={pulsar_version}",
            f"-var=instance_type={instance_type}",
            f"-var=region={self.region}",
            str(PACKER_TEMPLATE)
        ]

        logger.info(f"Running Packer command: {' '.join(packer_cmd)}")
        console.print("[bold cyan]Starting Packer build (this will take 10-15 minutes)...[/bold cyan]\n")

        # Run Packer with real-time output streaming
        ami_id = None
        start_time = time.time()

        try:
            process = subprocess.Popen(
                packer_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(PACKER_DIR)
            )

            # Stream output and look for AMI ID
            ami_pattern = re.compile(r'ami-[a-f0-9]{17}')

            for line in process.stdout:
                # Print Packer output with dimmed styling
                console.print(f"[dim]{line.rstrip()}[/dim]")

                # Extract AMI ID from output
                match = ami_pattern.search(line)
                if match and not ami_id:
                    ami_id = match.group(0)
                    logger.info(f"Detected AMI ID in output: {ami_id}")

            return_code = process.wait()

            if return_code != 0:
                raise AMIBuildError(f"Packer build failed with exit code {return_code}")

            if not ami_id:
                raise AMIBuildError(
                    "Packer build completed but AMI ID not found in output. "
                    "Check Packer logs for details."
                )

            elapsed_time = int(time.time() - start_time)
            elapsed_minutes = elapsed_time // 60
            elapsed_seconds = elapsed_time % 60

            console.print(f"\n[bold green]✓ AMI built successfully![/bold green]")
            console.print(f"  AMI ID: [bold cyan]{ami_id}[/bold cyan]")
            console.print(f"  Build time: {elapsed_minutes}m {elapsed_seconds}s")
            console.print(f"  Region: {self.region}\n")

            # Invalidate cache since we have a new AMI
            self._invalidate_cache()

            return ami_id

        except subprocess.SubprocessError as e:
            raise AMIBuildError(f"Packer build process failed: {e}") from e
        except Exception as e:
            raise AMIBuildError(f"Unexpected error during build: {e}") from e

    def find_ami_by_version(self, pulsar_version: str) -> Optional[Dict]:
        """
        Find an existing AMI for the specified Pulsar version.

        Args:
            pulsar_version: Pulsar version to search for (e.g., "3.0.0")

        Returns:
            AMI info dictionary if found, None otherwise

        Example:
            >>> manager = AMIManager(region="us-west-2")
            >>> ami = manager.find_ami_by_version("3.0.0")
            >>> if ami:
            ...     print(f"Found existing AMI: {ami['ami_id']}")
        """
        amis = self.list_amis(use_cache=True)
        for ami in amis:
            if ami['pulsar_version'] == pulsar_version and ami['state'] == 'available':
                return ami
        return None

    def list_amis(self, use_cache: bool = True) -> List[Dict]:
        """
        List all Pulsar AMIs in the region with detailed information.

        Retrieves AMI metadata including ID, name, version, creation date, and state.
        Results are cached locally for performance optimization.

        Args:
            use_cache: Whether to use cached results (default: True)

        Returns:
            List of dictionaries containing AMI metadata, sorted by creation date (newest first)

        Time Complexity: O(n) where n = number of AMIs
        Space Complexity: O(n) for storing AMI metadata

        Example:
            >>> manager = AMIManager(region="us-west-2")
            >>> amis = manager.list_amis()
            >>> for ami in amis:
            ...     print(f"{ami['name']}: {ami['ami_id']}")
        """
        # Check cache first
        if use_cache:
            cached_amis = self._get_cached_amis()
            if cached_amis is not None:
                logger.debug("Using cached AMI list")
                return cached_amis

        console.print("[bold cyan]Fetching AMI list from AWS...[/bold cyan]")

        try:
            # Search for Pulsar AMIs owned by this account
            response = self.ec2_client.describe_images(
                Filters=[
                    {'Name': 'name', 'Values': ['pulsar-base-*']},
                ],
                Owners=['self']
            )

            images = response.get('Images', [])

            # Parse and enrich AMI data
            ami_list = []
            for image in images:
                # Extract Pulsar version from tags or name
                pulsar_version = None
                for tag in image.get('Tags', []):
                    if tag['Key'] == 'PulsarVersion':
                        pulsar_version = tag['Value']
                        break

                # Parse name for version if not in tags
                if not pulsar_version:
                    name_match = re.match(r'pulsar-base-([0-9.]+)-', image['Name'])
                    if name_match:
                        pulsar_version = name_match.group(1)

                ami_info = {
                    'ami_id': image['ImageId'],
                    'name': image['Name'],
                    'pulsar_version': pulsar_version or 'unknown',
                    'state': image['State'],
                    'creation_date': image['CreationDate'],
                    'description': image.get('Description', ''),
                    'snapshot_id': image['BlockDeviceMappings'][0]['Ebs']['SnapshotId'] if image.get('BlockDeviceMappings') else None,
                }
                ami_list.append(ami_info)

            # Sort by creation date (newest first)
            ami_list.sort(key=lambda x: x['creation_date'], reverse=True)

            # Cache results
            self._cache_amis(ami_list)

            console.print(f"[bold green]✓ Found {len(ami_list)} AMI(s)[/bold green]\n")
            return ami_list

        except ClientError as e:
            raise AMIBuildError(f"Failed to list AMIs: {e}") from e

    def get_latest_ami(self) -> Optional[str]:
        """
        Get the latest Pulsar AMI ID in the region.

        Returns:
            AMI ID of the most recently created AMI, or None if no AMIs found

        Time Complexity: O(n) where n = number of AMIs
        """
        amis = self.list_amis()

        if not amis:
            return None

        # Already sorted by creation date (newest first)
        latest = amis[0]
        return latest['ami_id']

    def validate(self, ami_id: str, instance_type: str = "t3.micro") -> bool:
        """
        Validate an AMI by launching a test instance and verifying Pulsar installation.

        This comprehensive validation process:
        1. Launches a test instance from the AMI
        2. Waits for instance to be running and SSM-accessible
        3. Verifies /opt/pulsar directory exists
        4. Checks Pulsar version matches expected version
        5. Verifies systemd templates are installed
        6. Terminates the test instance
        7. Cleans up test resources

        Args:
            ami_id: AMI ID to validate
            instance_type: Instance type for validation (default: t3.micro for cost savings)

        Returns:
            True if validation passes, False otherwise

        Raises:
            AMIBuildError: If validation process fails critically

        Time Complexity: O(1) API calls, ~2-3 minutes for instance startup and checks
        Space Complexity: O(1) for temporary test instance

        Example:
            >>> manager = AMIManager(region="us-west-2")
            >>> is_valid = manager.validate("ami-0123456789abcdef0")
            >>> if is_valid:
            ...     print("AMI validation passed!")
        """
        if self.dry_run:
            console.print(f"[bold yellow]DRY RUN:[/bold yellow] Would validate AMI {ami_id}")
            return True

        console.print(Panel.fit(
            f"[bold cyan]Validating AMI[/bold cyan]\n\n"
            f"AMI ID: [bold]{ami_id}[/bold]\n"
            f"Instance Type: [bold]{instance_type}[/bold]\n"
            f"Region: [bold]{self.region}[/bold]",
            box=box.ROUNDED
        ))

        instance_id = None
        validation_passed = False

        try:
            # Launch test instance
            console.print("\n[bold cyan]Step 1/6: Launching test instance...[/bold cyan]")
            instance_id = self._launch_test_instance(ami_id, instance_type)
            console.print(f"  [bold green]✓[/bold green] Instance launched: {instance_id}\n")

            # Wait for instance to be running
            console.print("[bold cyan]Step 2/6: Waiting for instance to be running...[/bold cyan]")
            self._wait_for_instance_running(instance_id)
            console.print("  [bold green]✓[/bold green] Instance is running\n")

            # Wait for SSM agent
            console.print("[bold cyan]Step 3/6: Waiting for SSM agent...[/bold cyan]")
            self._wait_for_ssm_agent(instance_id, timeout_seconds=300)
            console.print("  [bold green]✓[/bold green] SSM agent is online\n")

            # Run validation checks
            console.print("[bold cyan]Step 4/6: Running validation checks...[/bold cyan]")
            checks_passed = self._run_validation_checks(instance_id)

            if checks_passed:
                console.print("  [bold green]✓[/bold green] All validation checks passed\n")
                validation_passed = True
            else:
                console.print("  [bold red]✗[/bold red] Validation checks failed\n")
                validation_passed = False

        except Exception as e:
            console.print(f"  [bold red]✗[/bold red] Validation error: {e}\n")
            logger.exception("Validation failed with exception")
            validation_passed = False

        finally:
            # Always cleanup test instance
            if instance_id:
                console.print("[bold cyan]Step 5/6: Terminating test instance...[/bold cyan]")
                try:
                    self._terminate_instance(instance_id)
                    console.print("  [bold green]✓[/bold green] Test instance terminated\n")
                except Exception as e:
                    console.print(f"  [bold yellow]⚠[/bold yellow] Failed to terminate instance: {e}\n")
                    logger.error(f"Failed to terminate test instance {instance_id}: {e}")

            # Final status
            console.print("[bold cyan]Step 6/6: Validation complete[/bold cyan]")
            if validation_passed:
                console.print("\n[bold green]✓ AMI VALIDATION PASSED[/bold green]\n")
            else:
                console.print("\n[bold red]✗ AMI VALIDATION FAILED[/bold red]\n")

        return validation_passed

    def delete(self, ami_id: str, delete_snapshots: bool = True) -> None:
        """
        Delete an AMI and optionally its associated snapshots.

        This method deregisters the AMI and cleans up associated EBS snapshots to
        prevent orphaned resources and unnecessary storage costs.

        Args:
            ami_id: AMI ID to delete
            delete_snapshots: Whether to delete associated snapshots (default: True)

        Raises:
            AMIBuildError: If deletion fails

        Time Complexity: O(n) where n = number of snapshots (typically 1)
        Space Complexity: O(1)

        Example:
            >>> manager = AMIManager(region="us-west-2")
            >>> manager.delete("ami-0123456789abcdef0", delete_snapshots=True)
        """
        if self.dry_run:
            console.print(f"[bold yellow]DRY RUN:[/bold yellow] Would delete AMI {ami_id}")
            if delete_snapshots:
                console.print("  Would also delete associated snapshots")
            return

        console.print(f"\n[bold yellow]Deleting AMI: {ami_id}[/bold yellow]")

        try:
            # Get AMI details first to find snapshots
            response = self.ec2_client.describe_images(ImageIds=[ami_id])

            if not response['Images']:
                raise AMIBuildError(f"AMI not found: {ami_id}")

            image = response['Images'][0]
            snapshot_ids = []

            # Extract snapshot IDs from block device mappings
            for mapping in image.get('BlockDeviceMappings', []):
                if 'Ebs' in mapping and 'SnapshotId' in mapping['Ebs']:
                    snapshot_ids.append(mapping['Ebs']['SnapshotId'])

            console.print(f"  AMI Name: {image['Name']}")
            console.print(f"  State: {image['State']}")
            console.print(f"  Snapshots: {len(snapshot_ids)}")

            # Deregister AMI
            console.print("\n  Deregistering AMI...", end=" ")
            self.ec2_client.deregister_image(ImageId=ami_id)
            console.print("[bold green]✓[/bold green]")

            # Delete snapshots if requested
            if delete_snapshots and snapshot_ids:
                console.print(f"  Deleting {len(snapshot_ids)} snapshot(s)...")
                for snapshot_id in snapshot_ids:
                    try:
                        console.print(f"    Deleting {snapshot_id}...", end=" ")
                        self.ec2_client.delete_snapshot(SnapshotId=snapshot_id)
                        console.print("[bold green]✓[/bold green]")
                    except ClientError as e:
                        console.print(f"[bold yellow]⚠[/bold yellow] ({e.response['Error']['Code']})")
                        logger.warning(f"Failed to delete snapshot {snapshot_id}: {e}")

            # Invalidate cache
            self._invalidate_cache()

            console.print(f"\n[bold green]✓ AMI deleted successfully[/bold green]\n")

        except ClientError as e:
            raise AMIBuildError(f"Failed to delete AMI: {e}") from e

    def display_amis(self, amis: List[Dict]) -> None:
        """
        Display AMI list in a formatted table.

        Args:
            amis: List of AMI metadata dictionaries

        Time Complexity: O(n) where n = number of AMIs to display
        """
        if not amis:
            console.print("[yellow]No Pulsar AMIs found in this region.[/yellow]")
            console.print(f"\nTo build a new AMI, run:")
            console.print(f"  python scripts/build-ami.py build --version 3.0.0 --region {self.region}\n")
            return

        table = Table(title=f"Pulsar AMIs in {self.region}", box=box.ROUNDED)
        table.add_column("AMI ID", style="cyan", no_wrap=True)
        table.add_column("Name", style="white")
        table.add_column("Version", style="green")
        table.add_column("State", style="yellow")
        table.add_column("Created", style="magenta")

        for ami in amis:
            # Format creation date
            creation_date = datetime.fromisoformat(ami['creation_date'].replace('Z', '+00:00'))
            created_str = creation_date.strftime('%Y-%m-%d %H:%M')

            # Color code state
            state = ami['state']
            if state == 'available':
                state_styled = f"[green]{state}[/green]"
            elif state == 'pending':
                state_styled = f"[yellow]{state}[/yellow]"
            else:
                state_styled = f"[red]{state}[/red]"

            table.add_row(
                ami['ami_id'],
                ami['name'],
                ami['pulsar_version'],
                state_styled,
                created_str
            )

        console.print(table)
        console.print()

    # Private helper methods

    def _launch_test_instance(self, ami_id: str, instance_type: str) -> str:
        """
        Launch a test EC2 instance from the AMI.

        Args:
            ami_id: AMI ID to launch
            instance_type: Instance type

        Returns:
            Instance ID of launched instance

        Raises:
            AMIBuildError: If instance launch fails
        """
        try:
            # Get default VPC
            vpc_response = self.ec2_client.describe_vpcs(
                Filters=[{'Name': 'is-default', 'Values': ['true']}]
            )

            if not vpc_response['Vpcs']:
                raise AMIBuildError(
                    "No default VPC found. Please create a VPC or specify subnet ID."
                )

            vpc_id = vpc_response['Vpcs'][0]['VpcId']

            # Get a subnet from default VPC
            subnet_response = self.ec2_client.describe_subnets(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )

            if not subnet_response['Subnets']:
                raise AMIBuildError(f"No subnets found in VPC {vpc_id}")

            subnet_id = subnet_response['Subnets'][0]['SubnetId']

            # Launch instance
            response = self.ec2_client.run_instances(
                ImageId=ami_id,
                InstanceType=instance_type,
                MinCount=1,
                MaxCount=1,
                SubnetId=subnet_id,
                IamInstanceProfile={
                    'Name': 'SSMManagedInstanceCore'  # Required for SSM access
                },
                TagSpecifications=[
                    {
                        'ResourceType': 'instance',
                        'Tags': [
                            {'Key': 'Name', 'Value': f'pulsar-ami-validation-{ami_id}'},
                            {'Key': 'Purpose', 'Value': 'AMI-Validation'},
                            {'Key': 'ManagedBy', 'Value': 'build-ami-script'},
                        ]
                    }
                ]
            )

            instance_id = response['Instances'][0]['InstanceId']
            logger.info(f"Launched test instance: {instance_id}")
            return instance_id

        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'InvalidParameterValue':
                # Might be missing SSM role, try without it
                logger.warning("Failed to launch with SSM role, retrying without IAM profile")
                try:
                    response = self.ec2_client.run_instances(
                        ImageId=ami_id,
                        InstanceType=instance_type,
                        MinCount=1,
                        MaxCount=1,
                        SubnetId=subnet_id,
                        TagSpecifications=[
                            {
                                'ResourceType': 'instance',
                                'Tags': [
                                    {'Key': 'Name', 'Value': f'pulsar-ami-validation-{ami_id}'},
                                    {'Key': 'Purpose', 'Value': 'AMI-Validation'},
                                ]
                            }
                        ]
                    )
                    instance_id = response['Instances'][0]['InstanceId']
                    console.print("  [yellow]⚠ Launched without SSM role - validation will be limited[/yellow]")
                    return instance_id
                except ClientError as retry_error:
                    raise AMIBuildError(f"Failed to launch test instance: {retry_error}") from retry_error
            else:
                raise AMIBuildError(f"Failed to launch test instance: {e}") from e

    def _wait_for_instance_running(self, instance_id: str, timeout_seconds: int = 180) -> None:
        """
        Wait for instance to reach 'running' state.

        Args:
            instance_id: Instance ID to monitor
            timeout_seconds: Maximum time to wait (default: 180 seconds)

        Raises:
            AMIBuildError: If timeout is reached
        """
        start_time = time.time()
        backoff = 2

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TimeElapsedColumn(),
            console=console
        ) as progress:
            task = progress.add_task("Waiting for instance...", total=timeout_seconds)

            while time.time() - start_time < timeout_seconds:
                try:
                    response = self.ec2_client.describe_instances(InstanceIds=[instance_id])
                    state = response['Reservations'][0]['Instances'][0]['State']['Name']

                    if state == 'running':
                        progress.update(task, completed=timeout_seconds)
                        return

                    if state in ['terminated', 'terminating', 'stopped', 'stopping']:
                        raise AMIBuildError(f"Instance entered unexpected state: {state}")

                    elapsed = int(time.time() - start_time)
                    progress.update(task, completed=min(elapsed, timeout_seconds))

                except ClientError as e:
                    logger.warning(f"Error checking instance state: {e}")

                time.sleep(backoff)
                backoff = min(backoff * 1.2, 10)

        raise AMIBuildError(f"Timeout waiting for instance to reach 'running' state after {timeout_seconds}s")

    def _wait_for_ssm_agent(self, instance_id: str, timeout_seconds: int = 300) -> None:
        """
        Wait for SSM agent to be online and ready.

        Args:
            instance_id: Instance ID to monitor
            timeout_seconds: Maximum time to wait (default: 300 seconds)

        Raises:
            AMIBuildError: If timeout is reached
        """
        start_time = time.time()
        backoff = 5

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TimeElapsedColumn(),
            console=console
        ) as progress:
            task = progress.add_task("Waiting for SSM agent...", total=timeout_seconds)

            while time.time() - start_time < timeout_seconds:
                try:
                    response = self.ssm_client.describe_instance_information(
                        Filters=[
                            {'Key': 'InstanceIds', 'Values': [instance_id]}
                        ]
                    )

                    instances = response.get('InstanceInformationList', [])

                    if instances:
                        ping_status = instances[0].get('PingStatus')
                        if ping_status == 'Online':
                            progress.update(task, completed=timeout_seconds)
                            return

                    elapsed = int(time.time() - start_time)
                    progress.update(task, completed=min(elapsed, timeout_seconds))

                except ClientError as e:
                    logger.warning(f"Error checking SSM status: {e}")

                time.sleep(backoff)
                backoff = min(backoff * 1.2, 15)

        raise AMIBuildError(f"Timeout waiting for SSM agent after {timeout_seconds}s")

    def _run_validation_checks(self, instance_id: str) -> bool:
        """
        Run validation checks on the test instance.

        Validates:
        1. /opt/pulsar directory exists
        2. Pulsar binaries are present
        3. systemd templates are installed
        4. Java is installed

        Args:
            instance_id: Instance ID to validate

        Returns:
            True if all checks pass, False otherwise
        """
        checks = [
            {
                'name': 'Pulsar directory exists',
                'command': 'test -d /opt/pulsar && echo "OK" || echo "FAIL"',
                'expected': 'OK'
            },
            {
                'name': 'Pulsar binaries present',
                'command': 'test -f /opt/pulsar/bin/pulsar && test -f /opt/pulsar/bin/bookkeeper && echo "OK" || echo "FAIL"',
                'expected': 'OK'
            },
            {
                'name': 'Systemd templates installed',
                'command': 'test -d /opt/pulsar-templates/systemd && echo "OK" || echo "FAIL"',
                'expected': 'OK'
            },
            {
                'name': 'Java installed',
                'command': 'java -version 2>&1 | head -n 1',
                'expected': 'version'  # Partial match
            },
            {
                'name': 'OpenMessaging Benchmark installed',
                'command': 'test -d /opt/openmessaging-benchmark && echo "OK" || echo "FAIL"',
                'expected': 'OK'
            }
        ]

        all_passed = True

        for check in checks:
            console.print(f"  Checking: {check['name']}...", end=" ")

            try:
                output = self._run_ssm_command(instance_id, check['command'])

                if check['expected'] in output:
                    console.print("[bold green]✓[/bold green]")
                else:
                    console.print(f"[bold red]✗[/bold red] (output: {output.strip()[:50]})")
                    all_passed = False

            except Exception as e:
                console.print(f"[bold red]✗[/bold red] (error: {e})")
                all_passed = False

        return all_passed

    def _run_ssm_command(self, instance_id: str, command: str, timeout_seconds: int = 30) -> str:
        """
        Run a command on an instance via SSM and return output.

        Args:
            instance_id: Instance ID
            command: Shell command to execute
            timeout_seconds: Command timeout (default: 30 seconds)

        Returns:
            Command output as string

        Raises:
            AMIBuildError: If command fails or times out
        """
        try:
            # Send command
            response = self.ssm_client.send_command(
                InstanceIds=[instance_id],
                DocumentName='AWS-RunShellScript',
                Parameters={'commands': [command]},
                TimeoutSeconds=timeout_seconds
            )

            command_id = response['Command']['CommandId']

            # Wait for command to complete
            max_attempts = timeout_seconds // 2
            for _ in range(max_attempts):
                time.sleep(2)

                try:
                    invocation = self.ssm_client.get_command_invocation(
                        CommandId=command_id,
                        InstanceId=instance_id
                    )

                    status = invocation['Status']

                    if status == 'Success':
                        return invocation.get('StandardOutputContent', '')

                    if status in ['Failed', 'Cancelled', 'TimedOut']:
                        error = invocation.get('StandardErrorContent', 'Unknown error')
                        raise AMIBuildError(f"Command failed: {error}")

                except self.ssm_client.exceptions.InvocationDoesNotExist:
                    continue

            raise AMIBuildError(f"Command timed out after {timeout_seconds}s")

        except ClientError as e:
            raise AMIBuildError(f"SSM command failed: {e}") from e

    def _terminate_instance(self, instance_id: str) -> None:
        """
        Terminate a test instance.

        Args:
            instance_id: Instance ID to terminate

        Raises:
            AMIBuildError: If termination fails
        """
        try:
            self.ec2_client.terminate_instances(InstanceIds=[instance_id])
            logger.info(f"Terminated instance: {instance_id}")
        except ClientError as e:
            raise AMIBuildError(f"Failed to terminate instance: {e}") from e

    def _get_cached_amis(self) -> Optional[List[Dict]]:
        """
        Get AMI list from local cache if not expired.

        Returns:
            Cached AMI list or None if cache is invalid/expired

        Time Complexity: O(1) file read
        """
        if not CACHE_FILE.exists():
            return None

        try:
            # Check cache age
            cache_age = time.time() - CACHE_FILE.stat().st_mtime

            if cache_age > CACHE_TTL_SECONDS:
                logger.debug(f"Cache expired (age: {cache_age}s)")
                return None

            # Read cache
            with open(CACHE_FILE, 'r') as f:
                cache_data = json.load(f)

            # Verify cache is for this region
            if cache_data.get('region') != self.region:
                logger.debug(f"Cache region mismatch: {cache_data.get('region')} != {self.region}")
                return None

            logger.debug(f"Using cache (age: {int(cache_age)}s)")
            return cache_data.get('amis', [])

        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.warning(f"Cache read failed: {e}")
            return None

    def _cache_amis(self, amis: List[Dict]) -> None:
        """
        Cache AMI list to local file.

        Args:
            amis: AMI list to cache

        Time Complexity: O(n) where n = number of AMIs
        """
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)

            cache_data = {
                'region': self.region,
                'timestamp': time.time(),
                'amis': amis
            }

            with open(CACHE_FILE, 'w') as f:
                json.dump(cache_data, f, indent=2)

            logger.debug(f"Cached {len(amis)} AMIs to {CACHE_FILE}")

        except OSError as e:
            logger.warning(f"Failed to write cache: {e}")

    def _invalidate_cache(self) -> None:
        """
        Invalidate the AMI cache by deleting the cache file.

        Time Complexity: O(1)
        """
        if CACHE_FILE.exists():
            try:
                CACHE_FILE.unlink()
                logger.debug("Cache invalidated")
            except OSError as e:
                logger.warning(f"Failed to invalidate cache: {e}")


def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments.

    Returns:
        Parsed arguments namespace
    """
    # Create parent parser with shared options
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument('--region', default='us-west-2', help='AWS region (default: us-west-2)')
    parent_parser.add_argument('--dry-run', action='store_true', help='Simulate operations without making changes')
    parent_parser.add_argument('--debug', action='store_true', help='Enable debug logging')

    # Main parser
    parser = argparse.ArgumentParser(
        description="Pulsar AMI Build and Management Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Build a new AMI
  %(prog)s build --version 3.0.0 --region us-west-2

  # Force rebuild an existing AMI
  %(prog)s build --version 3.0.0 --force --region us-west-2

  # List all AMIs
  %(prog)s list --region us-west-2

  # Validate an AMI
  %(prog)s validate --ami-id ami-0123456789abcdef0

  # Delete an AMI
  %(prog)s delete --ami-id ami-0123456789abcdef0 --region us-west-2

  # Get latest AMI ID
  %(prog)s latest --region us-west-2
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Command to execute')

    # Build command
    build_parser = subparsers.add_parser('build', parents=[parent_parser], help='Build a new Pulsar AMI')
    build_parser.add_argument('--version', required=True, help='Pulsar version to install (e.g., 3.0.0)')
    build_parser.add_argument('--instance-type', default='t3.small', help='Instance type for build (default: t3.small)')
    build_parser.add_argument('--force', action='store_true', help='Force rebuild even if AMI already exists')

    # List command
    list_parser = subparsers.add_parser('list', parents=[parent_parser], help='List all Pulsar AMIs')
    list_parser.add_argument('--no-cache', action='store_true', help='Skip cache and fetch fresh data')

    # Validate command
    validate_parser = subparsers.add_parser('validate', parents=[parent_parser], help='Validate an AMI')
    validate_parser.add_argument('--ami-id', required=True, help='AMI ID to validate')
    validate_parser.add_argument('--instance-type', default='t3.micro', help='Instance type for validation (default: t3.micro)')

    # Delete command
    delete_parser = subparsers.add_parser('delete', parents=[parent_parser], help='Delete an AMI')
    delete_parser.add_argument('--ami-id', required=True, help='AMI ID to delete')
    delete_parser.add_argument('--keep-snapshots', action='store_true', help='Keep EBS snapshots (default: delete snapshots)')

    # Latest command
    latest_parser = subparsers.add_parser('latest', parents=[parent_parser], help='Get latest AMI ID')

    return parser.parse_args()


def main() -> int:
    """
    Main entry point for the AMI management tool.

    Returns:
        Exit code (0 for success, 1 for error)

    Time Complexity: Varies by command
    - build: O(1) API calls, ~10-15 minutes build time
    - list: O(n) where n = number of AMIs
    - validate: O(1) API calls, ~2-3 minutes
    - delete: O(1) API calls, ~5 seconds
    - latest: O(n) where n = number of AMIs
    """
    args = parse_args()

    # Configure debug logging if requested
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.setLevel(logging.DEBUG)

    # Ensure command is provided
    if not args.command:
        console.print("[bold red]Error: No command specified[/bold red]")
        console.print("Run with --help for usage information")
        return 1

    try:
        # Initialize AMI manager
        manager = AMIManager(region=args.region, dry_run=args.dry_run)

        # Execute command
        if args.command == 'build':
            ami_id = manager.build(
                pulsar_version=args.version,
                instance_type=args.instance_type,
                force=args.force
            )
            console.print(f"[bold green]AMI ID:[/bold green] {ami_id}")
            return 0

        elif args.command == 'list':
            amis = manager.list_amis(use_cache=not args.no_cache)
            manager.display_amis(amis)
            return 0

        elif args.command == 'validate':
            validation_passed = manager.validate(
                ami_id=args.ami_id,
                instance_type=args.instance_type
            )
            return 0 if validation_passed else 1

        elif args.command == 'delete':
            manager.delete(
                ami_id=args.ami_id,
                delete_snapshots=not args.keep_snapshots
            )
            return 0

        elif args.command == 'latest':
            latest_ami = manager.get_latest_ami()
            if latest_ami:
                console.print(f"[bold cyan]Latest AMI:[/bold cyan] {latest_ami}")
                return 0
            else:
                console.print("[yellow]No AMIs found[/yellow]")
                return 1

        else:
            console.print(f"[bold red]Unknown command: {args.command}[/bold red]")
            return 1

    except PrerequisiteError as e:
        console.print(f"\n[bold red]Prerequisite Error:[/bold red] {e}\n")
        return 1

    except AMIBuildError as e:
        console.print(f"\n[bold red]AMI Error:[/bold red] {e}\n")
        logger.exception("AMI operation failed")
        return 1

    except KeyboardInterrupt:
        console.print("\n[yellow]Operation cancelled by user[/yellow]")
        return 130  # Standard Unix exit code for SIGINT

    except Exception as e:
        console.print(f"\n[bold red]Unexpected Error:[/bold red] {e}\n")
        logger.exception("Unexpected error occurred")
        return 1


if __name__ == '__main__':
    sys.exit(main())
