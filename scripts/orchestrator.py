#!/usr/bin/env python3
"""
Pulsar OMB Load Testing Orchestrator
Workflow controller for running OpenMessaging Benchmark tests against existing Pulsar clusters
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
from typing import Dict, List, Optional

import boto3
import yaml
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Project directories
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
RESULTS_DIR = PROJECT_ROOT / "results"

# Pulsar cluster connection details
PULSAR_SERVICE_URL = "pulsar://pulsar-proxy.pulsar.svc.cluster.local:6650"
PULSAR_HTTP_URL = "http://pulsar-proxy.pulsar.svc.cluster.local:80"
PULSAR_TEST_NAMESPACE = "public/omb-test"  # Namespace prefix for OMB test topics (OMB appends random suffix)

# Grafana dashboard URL
GRAFANA_BASE_URL = "https://grafana.dev-pulsar-lab.clovesoftware-dev.com"
GRAFANA_DASHBOARD_PATH = "/d/EetmjdhnA/pulsar-messaging"

# OMB Docker image
DEFAULT_OMB_IMAGE = "439508887365.dkr.ecr.us-east-1.amazonaws.com/sre/pulsar-omb:latest"


class OrchestratorError(Exception):
    """Base exception for orchestrator errors"""
    pass


class Orchestrator:
    """Main orchestrator for OMB load testing against existing Pulsar clusters"""

    def __init__(self, experiment_id: Optional[str] = None, namespace: str = "omb", omb_image: Optional[str] = None):
        """
        Initialize orchestrator with experiment tracking.

        Args:
            experiment_id: Unique experiment identifier (auto-generated if not provided)
            namespace: Kubernetes namespace where OMB jobs will run (default: omb)
            omb_image: OMB Docker image to use (default: from DEFAULT_OMB_IMAGE)
        """
        self.experiment_id = experiment_id or f"exp-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        self.experiment_dir = RESULTS_DIR / self.experiment_id
        self.experiment_dir.mkdir(parents=True, exist_ok=True)
        self.namespace = namespace
        self.pulsar_service_url = PULSAR_SERVICE_URL
        self.pulsar_http_url = PULSAR_HTTP_URL
        self.omb_image = omb_image or DEFAULT_OMB_IMAGE

        # Rich console for UI
        self.console = Console()
        self.status_messages = []  # Track status updates
        self.current_test = None
        self.pulsar_tenant_namespace = PULSAR_TEST_NAMESPACE  # Will be updated with actual namespace after detection

        # Track test run times for Grafana links
        self.test_start_time = None
        self.test_end_time = None

        # Ensure K8s namespace exists
        self._ensure_namespace_exists()

        # Ensure Pulsar namespace exists
        self._ensure_pulsar_namespace_exists()

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
        self._display_initial_info()

    def _display_initial_info(self) -> None:
        """Display initial experiment information"""
        table = Table(show_header=False, box=box.ROUNDED, border_style="cyan")
        table.add_column("Key", style="bold cyan")
        table.add_column("Value", style="white")

        table.add_row("Experiment ID", self.experiment_id)
        table.add_row("OMB Namespace", self.namespace)
        table.add_row("Pulsar Namespace", "pulsar")
        table.add_row("Pulsar URL", self.pulsar_service_url)
        table.add_row("Results Directory", str(self.experiment_dir))

        self.console.print()
        self.console.print(Panel(table, title="[bold cyan]Experiment Configuration[/bold cyan]", border_style="cyan"))
        self.console.print()

    def _create_metadata_panel(self) -> Panel:
        """Create static metadata panel for left side"""
        # Experiment info
        exp_table = Table(show_header=False, box=None, padding=(0, 1))
        exp_table.add_column("Key", style="bold cyan", width=18)
        exp_table.add_column("Value", style="white")

        exp_table.add_row("Experiment ID", self.experiment_id)
        exp_table.add_row("K8s Namespace", self.namespace)
        exp_table.add_row("Pulsar K8s NS", "pulsar")
        exp_table.add_row("Pulsar Tenant/NS", self.pulsar_tenant_namespace)

        # Test info
        if self.current_test:
            test_table = Table(show_header=False, box=None, padding=(0, 1), title="[bold yellow]Current Test[/bold yellow]", title_justify="left")
            test_table.add_column("Key", style="bold yellow", width=18)
            test_table.add_column("Value", style="white")

            test_table.add_row("Test Name", self.current_test.get('name', 'N/A'))
            test_table.add_row("Workers", str(self.current_test.get('workers', 'N/A')))
            test_table.add_row("Type", self.current_test.get('type', 'N/A'))

            content = Table.grid()
            content.add_row(exp_table)
            content.add_row("")
            content.add_row(test_table)
        else:
            content = exp_table

        # Monitoring info with Grafana link
        monitor_text = Text()
        monitor_text.append("\n\nMonitoring:\n", style="bold green")
        grafana_url = self._get_grafana_url()
        monitor_text.append(f"{grafana_url}\n", style="blue underline")

        final_content = Table.grid()
        final_content.add_row(content)
        final_content.add_row(monitor_text)

        return Panel(
            final_content,
            title="[bold cyan]Experiment Info[/bold cyan]",
            border_style="cyan",
            padding=(1, 2)
        )

    def _create_status_panel(self) -> Panel:
        """Create live status panel for right side"""
        if not self.status_messages:
            content = Text("Waiting for test to start...", style="dim italic")
        else:
            # Show last 20 status messages
            content = Text()
            for msg in self.status_messages[-20:]:
                timestamp = msg.get('time', '')
                message = msg.get('message', '')
                level = msg.get('level', 'info')

                style_map = {
                    'info': 'white',
                    'success': 'green',
                    'warning': 'yellow',
                    'error': 'red'
                }
                style = style_map.get(level, 'white')

                content.append(f"[dim]{timestamp}[/dim] ", style="dim")
                content.append(f"{message}\n", style=style)

        return Panel(
            content,
            title="[bold green]Status Log[/bold green]",
            border_style="green",
            padding=(1, 2)
        )

    def _add_status(self, message: str, level: str = 'info') -> None:
        """Add a status message to the log"""
        self.status_messages.append({
            'time': datetime.now().strftime('%H:%M:%S'),
            'message': message,
            'level': level
        })

    def _create_layout(self) -> Layout:
        """Create the split-pane layout (horizontal split: metadata on top, status on bottom)"""
        layout = Layout()
        layout.split_column(
            Layout(name="top", ratio=1),
            Layout(name="bottom", ratio=2)
        )

        layout["top"].update(self._create_metadata_panel())
        layout["bottom"].update(self._create_status_panel())

        return layout

    def _ensure_namespace_exists(self) -> None:
        """Ensure the K8s OMB namespace exists, create it if not."""
        result = self.run_command(
            ["kubectl", "get", "namespace", self.namespace],
            f"Check if K8s namespace {self.namespace} exists",
            capture_output=True,
            check=False
        )

        if result.returncode != 0:
            logger.info(f"Creating K8s namespace: {self.namespace}")
            self.run_command(
                ["kubectl", "create", "namespace", self.namespace],
                f"Create K8s namespace {self.namespace}"
            )
            logger.info(f"✓ K8s namespace '{self.namespace}' created")
        else:
            logger.debug(f"K8s namespace '{self.namespace}' already exists")

    def _ensure_pulsar_namespace_exists(self) -> None:
        """Ensure the Pulsar tenant/namespace for tests exists, create it if not."""
        # Check if namespace exists
        result = self.run_command(
            ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
             "bin/pulsar-admin", "namespaces", "list", "public"],
            f"List Pulsar namespaces in public tenant",
            capture_output=True,
            check=False
        )

        if result.returncode == 0:
            namespaces = [line.strip().strip('"') for line in result.stdout.strip().split('\n')
                         if line.strip() and line.strip().startswith('public/')]

            if self.pulsar_tenant_namespace in namespaces:
                logger.debug(f"Pulsar namespace '{self.pulsar_tenant_namespace}' already exists")
                return

        # Create the namespace
        logger.info(f"Creating Pulsar namespace: {self.pulsar_tenant_namespace}")
        result = self.run_command(
            ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
             "bin/pulsar-admin", "namespaces", "create", self.pulsar_tenant_namespace],
            f"Create Pulsar namespace {self.pulsar_tenant_namespace}",
            check=False,
            capture_output=True
        )

        if result.returncode == 0:
            logger.info(f"✓ Pulsar namespace '{self.pulsar_tenant_namespace}' created")
        else:
            # If it already exists, that's fine
            if "already exists" in result.stderr.lower():
                logger.debug(f"Pulsar namespace '{self.pulsar_tenant_namespace}' already exists")
            else:
                logger.warning(f"Failed to create Pulsar namespace: {result.stderr}")

    def _detect_namespace_from_job_logs(self, test_name: str) -> Optional[str]:
        """
        Detect Pulsar namespace by reading OMB driver Job logs.
        OMB prints the namespace when creating topics, giving us definitive data.

        Args:
            test_name: Name of the test (used to find Job pod)

        Returns:
            Namespace string like 'public/omb-test-7Wv9Uqc' or None if not found
        """
        try:
            # Get the pod name for the OMB driver Job
            result = self.run_command(
                ["kubectl", "get", "pods", "-n", "omb",
                 "-l", f"job-name=omb-{test_name}",
                 "-o", "jsonpath={.items[0].metadata.name}"],
                f"Get OMB driver pod for {test_name}",
                capture_output=True,
                check=False
            )

            if result.returncode != 0 or not result.stdout.strip():
                logger.warning("Could not find OMB driver pod")
                return None

            pod_name = result.stdout.strip()
            logger.debug(f"Found OMB driver pod: {pod_name}")

            # Read pod logs
            result = self.run_command(
                ["kubectl", "logs", pod_name, "-n", "omb"],
                f"Read logs from {pod_name}",
                capture_output=True,
                check=False
            )

            if result.returncode != 0:
                logger.warning("Could not read OMB driver pod logs")
                return None

            logs = result.stdout

            # Parse logs to find namespace
            # Look for patterns like:
            # - "Creating topic: persistent://public/omb-test-7Wv9Uqc/test-topic-0"
            # - "namespace: public/omb-test-7Wv9Uqc"
            import re

            # Try pattern 1: topic URL in logs
            topic_pattern = r'persistent://([^/]+/[^/]+)/'
            matches = re.findall(topic_pattern, logs)

            if matches:
                # Find first match that starts with our prefix
                for match in matches:
                    if match.startswith(PULSAR_TEST_NAMESPACE + '-') or match.startswith('public/omb-test-'):
                        logger.info(f"Detected namespace from Job logs: {match}")
                        return match

            # Try pattern 2: direct namespace mention
            ns_pattern = r'namespace[:\s]+([^/\s]+/[^\s,]+)'
            matches = re.findall(ns_pattern, logs, re.IGNORECASE)

            if matches:
                for match in matches:
                    if match.startswith(PULSAR_TEST_NAMESPACE + '-') or match.startswith('public/omb-test-'):
                        logger.info(f"Detected namespace from Job logs: {match}")
                        return match

            logger.warning("Could not find namespace in OMB driver logs")
            return None

        except Exception as e:
            logger.warning(f"Error detecting namespace from logs: {e}")
            return None

    def _detect_pulsar_namespace(self) -> Optional[str]:
        """
        Detect the actual Pulsar namespace being used for the current test.
        OMB appends random suffixes like 'omb-test-yDPSfpI' to the namespace prefix.

        Strategy: Find the last (most recently listed) namespace with topics.
        This is called right after the Job starts, so the newest namespace should be the one
        OMB just created for this test.

        Returns:
            The full namespace string (e.g., 'public/omb-test-yDPSfpI') or None if not detected
        """
        try:
            result = self.run_command(
                ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
                 "bin/pulsar-admin", "namespaces", "list", "public"],
                "List Pulsar namespaces",
                capture_output=True,
                check=False
            )

            if result.returncode != 0:
                logger.warning("Failed to query Pulsar namespaces")
                return None

            # Parse namespaces
            lines = result.stdout.strip().split('\n')
            namespaces = [line.strip().strip('"') for line in lines
                         if line.strip() and line.strip().startswith('public/')
                         and 'Defaulted container' not in line]

            # Look for namespaces starting with our prefix
            omb_namespaces = [ns for ns in namespaces if ns.startswith(PULSAR_TEST_NAMESPACE + '-')]

            if not omb_namespaces:
                return None

            # Check namespaces in reverse order (newest first) and return the first one with topics
            for ns in reversed(omb_namespaces):
                topic_result = self.run_command(
                    ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
                     "bin/pulsar-admin", "topics", "list", ns],
                    f"List topics in {ns}",
                    capture_output=True,
                    check=False
                )

                if topic_result.returncode == 0:
                    topics = [l.strip() for l in topic_result.stdout.strip().split('\n')
                             if l.strip() and l.strip().startswith('persistent://')]

                    if topics:
                        logger.info(f"Detected Pulsar namespace: {ns} (with {len(topics)} topics)")
                        return ns

            # If no namespace has topics yet, return the last one (newest)
            last_ns = omb_namespaces[-1]
            logger.info(f"Detected Pulsar namespace: {last_ns} (no topics yet, but newest)")
            return last_ns

        except Exception as e:
            logger.warning(f"Error detecting Pulsar namespace: {e}")
            return None

    def _get_grafana_url(self, from_time: str = 'now-15m', to_time: str = 'now', dashboard_path: Optional[str] = None) -> str:
        """
        Generate Grafana dashboard URL with the correct parameters for each dashboard type.

        Args:
            from_time: Start time for dashboard (e.g., 'now-15m' or timestamp in ms)
            to_time: End time for dashboard (e.g., 'now' or timestamp in ms)
            dashboard_path: Override default dashboard path

        Returns:
            Full Grafana dashboard URL
        """
        # Extract just the namespace part (without 'public/' prefix) for Grafana
        namespace_part = self.pulsar_tenant_namespace.replace('public/', '')

        # Base parameters for all dashboards
        params = {
            'orgId': '1',
            'from': from_time,
            'to': to_time,
            'timezone': 'utc',
        }

        path = dashboard_path or GRAFANA_DASHBOARD_PATH

        # Add dashboard-specific parameters
        if 'pulsar-messaging' in path:
            params.update({
                'var-cluster': '$__all',
                'var-tenant': 'public',
                'var-namespace': namespace_part,
                'refresh': '30s'
            })
        elif 'pulsar-jvm' in path:
            params.update({
                'var-cluster': '$__all',
                'var-job': '$__all',
                'var-instance': '$__all',
                'refresh': '30s'
            })
        elif 'pulsar-proxy' in path:
            params.update({
                'var-proxy': '$__all',
                'refresh': '30s'
            })
        else:
            # Default parameters
            params.update({
                'var-cluster': '$__all',
                'refresh': '10s'
            })

        param_string = '&'.join([f"{k}={v}" for k, v in params.items()])
        return f"{GRAFANA_BASE_URL}{path}?{param_string}"

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

    def run_command(
        self,
        cmd: List[str],
        description: str,
        capture_output: bool = False,
        check: bool = True,
        timeout: Optional[int] = None
    ) -> subprocess.CompletedProcess:
        """
        Run shell command with logging.

        Args:
            cmd: Command and arguments as list
            description: Human-readable description
            capture_output: Whether to capture stdout/stderr
            check: Whether to raise exception on non-zero exit
            timeout: Optional timeout in seconds

        Returns:
            CompletedProcess object

        Raises:
            OrchestratorError: If command fails and check=True
        """
        logger.info(f"Running: {description}")
        logger.debug(f"Command: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=capture_output,
                text=True,
                check=check,
                timeout=timeout
            )
            if capture_output and result.stdout:
                logger.debug(f"Output: {result.stdout[:500]}")
            return result
        except subprocess.TimeoutExpired as e:
            error_msg = f"Command timed out after {timeout}s: {description}"
            logger.error(error_msg)
            if check:
                raise OrchestratorError(error_msg) from e
            # Return a dummy result for non-checked commands
            return subprocess.CompletedProcess(cmd, returncode=124, stdout="", stderr=str(e))
        except subprocess.CalledProcessError as e:
            error_msg = f"Command failed: {description}"
            if capture_output and e.stderr:
                error_msg += f"\nError: {e.stderr}"
            logger.error(error_msg)
            raise OrchestratorError(error_msg) from e

    def setup_kubectl_context(self, region: str, cluster_name: str) -> None:
        """
        Configure kubectl to connect to EKS cluster.

        Args:
            region: AWS region
            cluster_name: EKS cluster name

        Raises:
            OrchestratorError: If kubectl setup fails
        """
        logger.info(f"Setting up kubectl context for cluster: {cluster_name}")

        # Update kubeconfig
        self.run_command(
            ["aws", "eks", "update-kubeconfig",
             "--region", region,
             "--name", cluster_name],
            "Configure kubectl context"
        )

        # Verify connectivity
        result = self.run_command(
            ["kubectl", "cluster-info"],
            "Verify kubectl connectivity",
            capture_output=True
        )
        logger.info(f"✓ kubectl configured successfully")
        logger.debug(result.stdout)

        # Get node count
        result = self.run_command(
            ["kubectl", "get", "nodes", "-o", "json"],
            "Get cluster nodes",
            capture_output=True
        )
        nodes = json.loads(result.stdout)
        node_count = len(nodes.get('items', []))
        logger.info(f"✓ Cluster has {node_count} nodes")

    def wait_for_pods_ready(self, timeout_seconds: int = 600) -> None:
        """
        Wait for all Pulsar pods to be ready.

        Args:
            timeout_seconds: Maximum time to wait

        Raises:
            OrchestratorError: If pods don't become ready within timeout
        """
        logger.info("="*60)
        logger.info("WAITING FOR PODS TO BE READY")
        logger.info("="*60)

        start_time = time.time()
        backoff_seconds = 5
        max_backoff = 30

        critical_components = ['zookeeper', 'bookkeeper', 'broker']

        while time.time() - start_time < timeout_seconds:
            # Get all pods
            result = self.run_command(
                ["kubectl", "get", "pods", "-n", self.namespace, "-o", "json"],
                "Get pod status",
                capture_output=True
            )

            pods = json.loads(result.stdout)
            pod_items = pods.get('items', [])

            if not pod_items:
                logger.warning("No pods found yet, retrying...")
                time.sleep(backoff_seconds)
                continue

            # Check pod readiness
            all_ready = True
            component_status = {}

            for pod in pod_items:
                name = pod['metadata']['name']
                component = self._get_pod_component(name)

                # Get pod phase and conditions
                phase = pod['status'].get('phase', 'Unknown')
                conditions = pod['status'].get('conditions', [])

                # Check if this is an initialization Job (these complete and don't need to be "Ready")
                is_init_job = 'init' in name.lower() or phase == 'Succeeded'

                ready = False
                if is_init_job and phase == 'Succeeded':
                    # Init jobs that succeeded are considered "ready"
                    ready = True
                else:
                    # Regular pods need Ready condition
                    for condition in conditions:
                        if condition['type'] == 'Ready':
                            ready = condition['status'] == 'True'
                            break

                status_str = f"{phase} ({'Ready' if ready or is_init_job else 'Not Ready'})"
                component_status.setdefault(component, []).append((name, status_str, ready))

                if not ready:
                    all_ready = False

            # Log status
            logger.info(f"Pod status ({len(pod_items)} total):")
            for component in sorted(component_status.keys()):
                pods_status = component_status[component]
                ready_count = sum(1 for _, _, ready in pods_status if ready)
                logger.info(f"  {component}: {ready_count}/{len(pods_status)} ready")
                for pod_name, status, _ in pods_status:
                    symbol = "✓" if "Ready" in status else "✗"
                    logger.info(f"    {symbol} {pod_name}: {status}")

            if all_ready:
                logger.info("\n✓ All pods are ready!")
                break

            elapsed = int(time.time() - start_time)
            logger.info(f"Waiting for pods... ({elapsed}s elapsed)")
            time.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 1.5, max_backoff)

        if time.time() - start_time >= timeout_seconds:
            raise OrchestratorError(
                f"Timeout waiting for pods to be ready after {timeout_seconds}s"
            )

        total_time = int(time.time() - start_time)
        logger.info("="*60)
        logger.info(f"PODS READY! (Total time: {total_time}s)")
        logger.info("="*60)

    def _get_pod_component(self, pod_name: str) -> str:
        """Extract component type from pod name"""
        for component in ['zookeeper', 'bookkeeper', 'broker', 'proxy', 'prometheus', 'grafana']:
            if component in pod_name.lower():
                return component
        return 'other'

    def cleanup_namespace(self) -> None:
        """
        Clean up any leftover OMB test resources in the namespace.
        This removes Jobs, StatefulSets, Services, and ConfigMaps created during testing.
        """
        logger.info(f"Cleaning up OMB resources in namespace '{self.namespace}'...")

        # Delete all OMB Jobs
        result = self.run_command(
            ["kubectl", "get", "jobs", "-n", self.namespace, "-l", "app=omb-driver", "-o", "name"],
            "List OMB Jobs",
            capture_output=True,
            check=False
        )
        if result.returncode == 0 and result.stdout.strip():
            jobs = result.stdout.strip().split('\n')
            logger.info(f"Found {len(jobs)} OMB Jobs to clean up")
            self.run_command(
                ["kubectl", "delete", "jobs", "-n", self.namespace, "-l", "app=omb-driver"],
                "Delete OMB Jobs",
                check=False
            )

        # Delete all OMB worker StatefulSets
        result = self.run_command(
            ["kubectl", "get", "statefulsets", "-n", self.namespace, "-l", "app=omb-worker", "-o", "name"],
            "List OMB worker StatefulSets",
            capture_output=True,
            check=False
        )
        if result.returncode == 0 and result.stdout.strip():
            statefulsets = result.stdout.strip().split('\n')
            logger.info(f"Found {len(statefulsets)} OMB worker StatefulSets to clean up")
            self.run_command(
                ["kubectl", "delete", "statefulsets", "-n", self.namespace, "-l", "app=omb-worker"],
                "Delete OMB worker StatefulSets",
                check=False
            )

        # Delete all OMB worker Services
        result = self.run_command(
            ["kubectl", "get", "services", "-n", self.namespace, "-o", "name"],
            "List Services",
            capture_output=True,
            check=False
        )
        if result.returncode == 0 and result.stdout.strip():
            for service in result.stdout.strip().split('\n'):
                if 'omb-workers-' in service:
                    svc_name = service.split('/')[-1]
                    self.run_command(
                        ["kubectl", "delete", "service", svc_name, "-n", self.namespace],
                        f"Delete Service {svc_name}",
                        check=False
                    )

        # Delete all OMB workload ConfigMaps
        result = self.run_command(
            ["kubectl", "get", "configmaps", "-n", self.namespace, "-o", "name"],
            "List ConfigMaps",
            capture_output=True,
            check=False
        )
        if result.returncode == 0 and result.stdout.strip():
            for configmap in result.stdout.strip().split('\n'):
                if 'omb-workload-' in configmap:
                    cm_name = configmap.split('/')[-1]
                    self.run_command(
                        ["kubectl", "delete", "configmap", cm_name, "-n", self.namespace],
                        f"Delete ConfigMap {cm_name}",
                        check=False
                    )

        logger.info(f"✓ Namespace '{self.namespace}' cleanup completed")

    def _cleanup_test_topics(self, test_name: str = "", workload_config: Optional[Dict] = None, live: Optional[Live] = None) -> None:
        """
        Delete all Pulsar topics in the test namespace.
        OMB creates topics in the fixed namespace 'public/omb-test'.

        Args:
            test_name: Name of the test (unused, kept for compatibility)
            workload_config: Workload configuration (unused, kept for compatibility)
            live: Rich Live display instance for UI updates
        """
        _ = test_name, workload_config  # Keep for backward compatibility, unused

        if live:
            self._add_status(f"Cleaning up topics in {self.pulsar_tenant_namespace}...", 'info')
            live.update(self._create_layout())

        logger.info(f"Cleaning up Pulsar topics in namespace '{self.pulsar_tenant_namespace}'...")

        # List all topics in the detected namespace
        result = self.run_command(
            ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
             "bin/pulsar-admin", "topics", "list", self.pulsar_tenant_namespace],
            f"List topics in {self.pulsar_tenant_namespace}",
            check=False,
            capture_output=True
        )

        if result.returncode != 0:
            logger.warning(f"Failed to list topics in {self.pulsar_tenant_namespace}: {result.stderr}")
            if live:
                self._add_status("⚠ Failed to list topics for cleanup", 'warning')
                live.update(self._create_layout())
            return

        # Parse topic list (filter out kubectl stderr messages)
        lines = result.stdout.strip().split('\n')
        topics = []
        for line in lines:
            line = line.strip()
            if line and line.startswith('persistent://') and 'Defaulted container' not in line:
                topics.append(line)

        if not topics:
            logger.info(f"No topics to delete in namespace '{self.pulsar_tenant_namespace}'")
            if live:
                self._add_status("✓ No topics to clean up", 'success')
                live.update(self._create_layout())
            return

        logger.info(f"Found {len(topics)} topic(s) to delete")

        # Delete each topic
        topics_deleted = 0
        for topic_url in topics:
            result = self.run_command(
                ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
                 "bin/pulsar-admin", "topics", "delete", topic_url, "-f"],  # -f for force delete
                f"Delete topic {topic_url.split('/')[-1]}",
                check=False,
                capture_output=True
            )

            if result.returncode == 0:
                topics_deleted += 1
                logger.debug(f"  ✓ Deleted: {topic_url.split('/')[-1]}")
            else:
                logger.warning(f"  ✗ Failed to delete {topic_url.split('/')[-1]}: {result.stderr}")

        logger.info(f"✓ Deleted {topics_deleted}/{len(topics)} topic(s) from '{self.pulsar_tenant_namespace}'")
        if live:
            self._add_status(f"✓ Cleaned up {topics_deleted}/{len(topics)} topics", 'success')
            live.update(self._create_layout())

    def run_omb_job(self, test_config: Dict, workload_config: Dict, live: Live) -> str:
        """
        Run OpenMessaging Benchmark job with distributed workers.

        Args:
            test_config: Test run configuration
            workload_config: Workload specification
            live: Rich Live display instance

        Returns:
            Test results as JSON string

        Raises:
            OrchestratorError: If test execution fails
        """
        test_name = test_config['name']
        num_workers = test_config.get('num_workers', 3)  # Default to 3 workers
        logger.info(f"Running OMB test: {test_name} (with {num_workers} workers)")

        # Set current test info for UI
        self.current_test = {
            'name': test_name,
            'workers': num_workers,
            'type': test_config.get('type', 'unknown')
        }

        self._add_status(f"Starting test: {test_name}", 'info')
        live.update(self._create_layout())
        self._add_status(f"Deploying {num_workers} worker pods", 'info')
        live.update(self._create_layout())

        # Generate workload ConfigMap
        workload_yaml = self._generate_omb_workload_yaml(test_name, workload_config)
        workload_file = self.experiment_dir / f"workload_{test_name}.yaml"

        with open(workload_file, 'w') as f:
            f.write(workload_yaml)

        # Apply workload ConfigMap
        self._add_status("Creating workload ConfigMap", 'info')
        live.update(self._create_layout())
        self.run_command(
            ["kubectl", "apply", "-f", str(workload_file)],
            f"Apply workload ConfigMap for {test_name}"
        )

        # Deploy OMB workers StatefulSet
        workers_yaml = self._generate_omb_workers_yaml(test_name, num_workers)
        workers_file = self.experiment_dir / f"omb_workers_{test_name}.yaml"

        with open(workers_file, 'w') as f:
            f.write(workers_yaml)

        self._add_status("Deploying worker StatefulSet", 'info')
        live.update(self._create_layout())
        self.run_command(
            ["kubectl", "apply", "-f", str(workers_file)],
            f"Deploy OMB workers for {test_name}"
        )

        # Wait for workers to be ready
        self._add_status(f"Waiting for {num_workers} workers to be ready...", 'info')
        live.update(self._create_layout())
        logger.info(f"Waiting for {num_workers} worker pods to be ready...")
        timeout_seconds = 5 * 60  # 5 minutes
        start_time = time.time()
        poll_interval = 5

        while time.time() - start_time < timeout_seconds:
            result = self.run_command(
                ["kubectl", "get", "pods", "-n", self.namespace,
                 "-l", f"app=omb-worker,test={test_name}",
                 "-o", "json"],
                "Check worker pods status",
                capture_output=True,
                check=False
            )

            if result.returncode == 0:
                pods = json.loads(result.stdout)
                pod_items = pods.get('items', [])

                if len(pod_items) == num_workers:
                    # Check if all are ready
                    ready_count = 0
                    for pod in pod_items:
                        conditions = pod.get('status', {}).get('conditions', [])
                        for condition in conditions:
                            if condition['type'] == 'Ready' and condition['status'] == 'True':
                                ready_count += 1
                                break

                    if ready_count == num_workers:
                        self._add_status(f"✓ All {num_workers} workers are ready", 'success')
                        live.update(self._create_layout())
                        logger.info(f"✓ All {num_workers} workers are ready")
                        break
                    else:
                        self._add_status(f"Workers ready: {ready_count}/{num_workers}", 'info')
                        live.update(self._create_layout())
                        logger.info(f"Workers ready: {ready_count}/{num_workers}")
                else:
                    self._add_status(f"Workers created: {len(pod_items)}/{num_workers}", 'info')
                    live.update(self._create_layout())
                    logger.info(f"Workers created: {len(pod_items)}/{num_workers}")

            time.sleep(poll_interval)
        else:
            raise OrchestratorError(f"Timeout waiting for worker pods to be ready after {timeout_seconds}s")

        # Create OMB driver Job
        job_yaml = self._generate_omb_job_yaml(test_name, num_workers)
        job_file = self.experiment_dir / f"omb_job_{test_name}.yaml"

        with open(job_file, 'w') as f:
            f.write(job_yaml)

        # Apply Job
        self._add_status("Starting driver Job", 'info')
        live.update(self._create_layout())
        self.run_command(
            ["kubectl", "apply", "-f", str(job_file)],
            f"Create OMB driver Job for {test_name}"
        )

        # Wait for Job pod to start and read logs to detect namespace
        self._add_status("Detecting Pulsar namespace from Job logs...", 'info')
        live.update(self._create_layout())

        # Wait a bit for pod to start and produce logs
        time.sleep(5)

        # Try to get namespace from Job logs (OMB prints namespace when creating topics)
        detected_ns = self._detect_namespace_from_job_logs(test_name)
        if detected_ns:
            self.pulsar_tenant_namespace = detected_ns
            self._add_status(f"✓ Pulsar namespace: {detected_ns}", 'success')
            logger.info(f"Using Pulsar namespace: {detected_ns}")
        else:
            # Fallback to guessing from topic list
            logger.warning("Could not detect namespace from logs, falling back to topic search")
            detected_ns = self._detect_pulsar_namespace()
            if detected_ns:
                self.pulsar_tenant_namespace = detected_ns
                self._add_status(f"✓ Pulsar namespace: {detected_ns} (detected from topics)", 'success')
            else:
                self._add_status("⚠ Could not detect Pulsar namespace", 'warning')
        live.update(self._create_layout())

        # Wait for Job completion or failure
        self._add_status(f"Running benchmark test (this may take several minutes)...", 'info')
        live.update(self._create_layout())
        logger.info(f"Waiting for test {test_name} to complete...")

        # Poll Job status until complete or failed
        timeout_seconds = 30 * 60  # 30 minutes
        start_time = time.time()
        poll_interval = 10  # seconds

        job_succeeded = False
        job_failed = False

        while time.time() - start_time < timeout_seconds:
            result = self.run_command(
                ["kubectl", "get", "job", f"omb-{test_name}", "-n", self.namespace, "-o", "json"],
                f"Get Job {test_name} status",
                capture_output=True,
                check=False
            )

            if result.returncode == 0:
                job_status = json.loads(result.stdout)
                status = job_status.get('status', {})

                # Check for completion via succeeded/failed counts (more reliable than conditions)
                succeeded_count = status.get('succeeded', 0)
                failed_count = status.get('failed', 0)
                active_count = status.get('active', 0)

                if succeeded_count > 0:
                    job_succeeded = True
                    self._add_status(f"✓ Benchmark completed successfully", 'success')
                    live.update(self._create_layout())
                    logger.info(f"✓ Job {test_name} completed successfully (succeeded: {succeeded_count})")
                    # Give pod a moment to fully terminate before collecting logs
                    time.sleep(2)
                    break
                elif failed_count > 0:
                    job_failed = True
                    self._add_status(f"✗ Benchmark failed", 'error')
                    live.update(self._create_layout())
                    logger.error(f"✗ Job {test_name} failed (failed: {failed_count})")
                    # Give pod a moment to fully terminate before collecting logs
                    time.sleep(2)
                    break

                # Still running - log progress
                elapsed = int(time.time() - start_time)
                minutes = elapsed // 60
                seconds = elapsed % 60
                self._add_status(f"Test running... ({minutes}m {seconds}s elapsed)", 'info')
                live.update(self._create_layout())
                logger.info(f"Job {test_name} still running... ({elapsed}s elapsed, active: {active_count}, succeeded: {succeeded_count}, failed: {failed_count})")

            time.sleep(poll_interval)

        if not (job_succeeded or job_failed):
            logger.error(f"Timeout waiting for Job {test_name} after {timeout_seconds}s")
            self._collect_job_logs(test_name, success=False)
            raise OrchestratorError(f"OMB test {test_name} timed out")

        if job_failed:
            self._collect_job_logs(test_name, success=False)
            raise OrchestratorError(f"OMB test {test_name} failed")

        # Collect results from Job logs
        self._add_status("Collecting test results...", 'info')
        live.update(self._create_layout())
        logger.info(f"Collecting results for {test_name}...")
        results = self._collect_job_logs(test_name, success=True)

        if results:
            self._add_status(f"✓ Results collected ({len(results)} bytes)", 'success')
        else:
            self._add_status("⚠ No results data collected", 'warning')
        live.update(self._create_layout())

        # Cleanup Pulsar topics created during test
        self._cleanup_test_topics(test_name, workload_config, live)

        # Cleanup Job and workers
        logger.info(f"Cleaning up test resources for {test_name}...")
        self.run_command(
            ["kubectl", "delete", "job", f"omb-{test_name}", "-n", self.namespace],
            f"Delete OMB driver Job {test_name}",
            check=False
        )
        self.run_command(
            ["kubectl", "delete", "statefulset", f"omb-workers-{test_name}", "-n", self.namespace],
            f"Delete OMB workers StatefulSet {test_name}",
            check=False
        )
        self.run_command(
            ["kubectl", "delete", "service", f"omb-workers-{test_name}", "-n", self.namespace],
            f"Delete OMB workers Service {test_name}",
            check=False
        )
        self.run_command(
            ["kubectl", "delete", "configmap", f"omb-workload-{test_name}", "-n", self.namespace],
            f"Delete workload ConfigMap {test_name}",
            check=False
        )

        return results

    def _generate_omb_workload_yaml(self, test_name: str, workload: Dict) -> str:
        """Generate Kubernetes ConfigMap YAML for OMB workload"""
        workload_content = yaml.dump(workload)

        return f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: omb-workload-{test_name}
  namespace: {self.namespace}
data:
  workload.yaml: |
{chr(10).join('    ' + line for line in workload_content.split(chr(10)))}
  driver.yaml: |
    name: Pulsar
    driverClass: io.openmessaging.benchmark.driver.pulsar.PulsarBenchmarkDriver
    client:
      serviceUrl: {self.pulsar_service_url}
      httpUrl: {self.pulsar_http_url}
      namespacePrefix: {self.pulsar_tenant_namespace}
    producer:
      batchingEnabled: true
      batchingMaxPublishDelayMs: 1
      blockIfQueueFull: true
      pendingQueueSize: 1000
    consumer:
      subscriptionType: Exclusive
"""

    def _generate_omb_workers_yaml(self, test_name: str, num_workers: int = 3) -> str:
        """Generate Kubernetes StatefulSet YAML for OMB workers"""
        return f"""apiVersion: v1
kind: Service
metadata:
  name: omb-workers-{test_name}
  namespace: {self.namespace}
spec:
  clusterIP: None  # Headless service for StatefulSet
  selector:
    app: omb-worker
    test: {test_name}
  ports:
  - name: http
    port: 8080
    targetPort: 8080
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: omb-workers-{test_name}
  namespace: {self.namespace}
spec:
  serviceName: omb-workers-{test_name}
  replicas: {num_workers}
  selector:
    matchLabels:
      app: omb-worker
      test: {test_name}
  template:
    metadata:
      labels:
        app: omb-worker
        test: {test_name}
    spec:
      containers:
      - name: worker
        image: {self.omb_image}
        imagePullPolicy: Always
        command: ["/bin/bash", "-c"]
        args:
          - |
            set -x
            echo "Starting OMB worker on $(hostname)"
            echo "Worker will listen on 0.0.0.0:8080"

            # Start worker and keep it running
            exec /app/bin/benchmark-worker --port 8080
        ports:
        - containerPort: 8080
          name: http
        readinessProbe:
          tcpSocket:
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 5
          failureThreshold: 3
        livenessProbe:
          tcpSocket:
            port: 8080
          initialDelaySeconds: 10
          periodSeconds: 10
          failureThreshold: 3
"""

    def _generate_omb_job_yaml(self, test_name: str, num_workers: int = 3) -> str:
        """Generate Kubernetes Job YAML for OMB driver"""
        # Generate worker addresses for the StatefulSet
        worker_addresses = []
        for i in range(num_workers):
            worker_addresses.append(f"http://omb-workers-{test_name}-{i}.omb-workers-{test_name}.{self.namespace}.svc.cluster.local:8080")
        workers_list = ",".join(worker_addresses)

        return f"""apiVersion: batch/v1
kind: Job
metadata:
  name: omb-{test_name}
  namespace: {self.namespace}
  labels:
    app: omb-driver
    test: {test_name}
spec:
  backoffLimit: 0
  template:
    metadata:
      labels:
        app: omb-driver
        test: {test_name}
    spec:
      restartPolicy: Never
      containers:
      - name: omb-driver
        image: {self.omb_image}
        imagePullPolicy: Always
        command: ["/bin/bash", "-c"]
        args:
          - |
            set -x  # Enable debug output
            echo "===== OMB Debug Information ====="
            echo "Test name: {test_name}"
            echo "Timestamp: $(date)"
            echo "Hostname: $(hostname)"
            echo ""

            echo "===== DNS Resolution ====="
            nslookup pulsar-proxy.pulsar.svc.cluster.local || echo "DNS lookup failed"
            echo ""

            echo "===== Network Connectivity ====="
            echo "Testing binary protocol port (6650)..."
            timeout 5 nc -zv pulsar-proxy.pulsar.svc.cluster.local 6650 || echo "Port 6650 not reachable"
            echo "Testing HTTP port (80)..."
            timeout 5 nc -zv pulsar-proxy.pulsar.svc.cluster.local 80 || echo "Port 80 not reachable"
            echo ""

            echo "===== HTTP Endpoint Tests ====="
            echo "Testing /admin/v2/brokers/health..."
            curl -v -m 10 http://pulsar-proxy.pulsar.svc.cluster.local:80/admin/v2/brokers/health || echo "Health check failed"
            echo ""
            echo "Testing /admin/v2/namespaces/public/default..."
            curl -v -m 10 http://pulsar-proxy.pulsar.svc.cluster.local:80/admin/v2/namespaces/public/default || echo "Namespace check failed"
            echo ""

            echo "===== Configuration Files ====="
            echo "Driver configuration:"
            cat /workload/driver.yaml
            echo ""
            echo "Workload configuration:"
            cat /workload/workload.yaml
            echo ""

            echo "===== Java Environment ====="
            java -version
            echo "JAVA_HOME: $JAVA_HOME"
            echo "PATH: $PATH"
            echo ""

            echo "===== Worker Connectivity Tests ====="
            echo "Testing worker endpoints..."
            WORKERS="{workers_list}"
            IFS=',' read -ra WORKER_ARRAY <<< "$WORKERS"
            for worker in "${{WORKER_ARRAY[@]}}"; do
              echo "Testing $worker..."
              curl -m 5 "$worker" || echo "Worker $worker not reachable"
            done
            echo ""

            echo "===== Starting OMB Benchmark (Driver Mode) ====="
            /app/bin/benchmark \\
              --drivers /workload/driver.yaml \\
              --workers {workers_list} \\
              --output /results/result.json \\
              /workload/workload.yaml

            EXIT_CODE=$?
            echo ""
            echo "===== Benchmark Exit Code: $EXIT_CODE ====="
            if [ $EXIT_CODE -eq 0 ]; then
              echo "Results saved to /results/result.json"
              cat /results/result.json
            else
              echo "Benchmark failed with exit code $EXIT_CODE"
            fi
            exit $EXIT_CODE
        volumeMounts:
        - name: workload
          mountPath: /workload
        - name: results
          mountPath: /results
      volumes:
      - name: workload
        configMap:
          name: omb-workload-{test_name}
      - name: results
        emptyDir: {{}}
"""

    def _collect_job_logs(self, test_name: str, success: bool) -> str:
        """
        Collect logs and results from OMB Job pod.

        Returns:
            JSON results as string
        """
        # Get Job pod name - retry a few times if not found immediately
        pod_name = ""
        for attempt in range(5):
            result = self.run_command(
                ["kubectl", "get", "pods", "-n", self.namespace,
                 "-l", f"job-name=omb-{test_name}",
                 "-o", "jsonpath={.items[0].metadata.name}"],
                f"Get Job pod for {test_name} (attempt {attempt + 1})",
                capture_output=True,
                check=False
            )
            pod_name = result.stdout.strip()
            if pod_name:
                break
            time.sleep(1)

        if not pod_name:
            logger.warning(f"Could not find pod for Job {test_name}")
            return ""

        logger.info(f"Found pod: {pod_name}")

        # Wait for pod to be in a terminal state (Succeeded or Failed)
        for attempt in range(10):
            result = self.run_command(
                ["kubectl", "get", "pod", pod_name, "-n", self.namespace,
                 "-o", "jsonpath={.status.phase}"],
                f"Check pod {pod_name} phase",
                capture_output=True,
                check=False
            )
            phase = result.stdout.strip()
            logger.info(f"Pod phase: {phase}")

            if phase in ["Succeeded", "Failed"]:
                break

            logger.info(f"Waiting for pod to reach terminal state (currently {phase})...")
            time.sleep(2)

        # Get pod logs
        result = self.run_command(
            ["kubectl", "logs", pod_name, "-n", self.namespace],
            f"Get logs for {test_name}",
            capture_output=True,
            check=False
        )

        logs = result.stdout

        # Save logs to file
        log_file = self.experiment_dir / f"omb_{test_name}_{'success' if success else 'failed'}.log"
        with open(log_file, 'w') as f:
            f.write(logs)

        logger.info(f"Logs saved to: {log_file}")

        # Copy JSON results from pod if test succeeded
        json_data = ""
        if success:
            results_dir = self.experiment_dir / "benchmark_results"
            results_dir.mkdir(exist_ok=True)

            result_file = results_dir / f"{test_name}.json"

            # Copy result file from pod
            result = self.run_command(
                ["kubectl", "cp",
                 f"{self.namespace}/{pod_name}:/results/result.json",
                 str(result_file)],
                f"Copy results for {test_name}",
                check=False
            )

            if result.returncode == 0 and result_file.exists():
                logger.info(f"Results saved to: {result_file}")
                with open(result_file, 'r') as f:
                    json_data = f.read()
            else:
                logger.warning(f"Failed to copy results file for {test_name}")

        return json_data

    def collect_pod_logs(self) -> None:
        """Collect logs from all Pulsar component pods for debugging"""
        logger.info("Collecting pod logs for troubleshooting...")

        result = self.run_command(
            ["kubectl", "get", "pods", "-n", self.namespace, "-o", "json"],
            "Get all pods",
            capture_output=True
        )

        pods = json.loads(result.stdout)

        logs_dir = self.experiment_dir / "pod_logs"
        logs_dir.mkdir(exist_ok=True)

        for pod in pods.get('items', []):
            pod_name = pod['metadata']['name']
            component = self._get_pod_component(pod_name)

            logger.info(f"Collecting logs from {pod_name}...")

            result = self.run_command(
                ["kubectl", "logs", pod_name, "-n", self.namespace,
                 "--tail=1000"],
                f"Get logs from {pod_name}",
                capture_output=True,
                check=False
            )

            log_file = logs_dir / f"{component}_{pod_name}.log"
            with open(log_file, 'w') as f:
                f.write(result.stdout if result.stdout else "No logs available")

        logger.info(f"✓ Pod logs collected in: {logs_dir}")

    def run_tests(self, test_plan_file: Path) -> None:
        """
        Execute test plan with OMB.

        Args:
            test_plan_file: Path to test plan YAML
        """
        logger.info("="*60)
        logger.info("RUNNING BENCHMARK TESTS")
        logger.info("="*60)

        test_plan = self.load_config(test_plan_file)

        # Create results directory
        results_dir = self.experiment_dir / "benchmark_results"
        results_dir.mkdir(exist_ok=True)

        # Track test execution times for Grafana links
        self.test_start_time = datetime.now()

        # Run tests with Rich Live display
        with Live(self._create_layout(), refresh_per_second=2, console=self.console) as live:
            # Run each test
            for idx, test_run in enumerate(test_plan['test_runs']):
                test_name = test_run['name']
                logger.info(f"\n{'='*60}")
                logger.info(f"Test {idx + 1}/{len(test_plan['test_runs'])}: {test_name}")
                logger.info(f"{'='*60}\n")

                # Generate workload
                workload = self._generate_workload(test_plan['base_workload'], test_run)

                # Run OMB job
                try:
                    results = self.run_omb_job(test_run, workload, live)

                    # Save results
                    result_file = results_dir / f"{test_name}.log"
                    with open(result_file, 'w') as f:
                        f.write(results)

                    self._add_status(f"✓ Test '{test_name}' completed", 'success')
                    live.update(self._create_layout())
                    logger.info(f"✓ Test '{test_name}' completed")
                    logger.info(f"Results: {result_file}")

                except OrchestratorError as e:
                    self._add_status(f"✗ Test '{test_name}' failed: {e}", 'error')
                    live.update(self._create_layout())
                    logger.error(f"Test '{test_name}' failed: {e}")
                    continue

        logger.info(f"\n{'='*60}")
        logger.info(f"ALL TESTS COMPLETED")
        logger.info(f"Results: {results_dir}")
        logger.info(f"{'='*60}\n")

        # Track end time for Grafana links
        self.test_end_time = datetime.now()

        # Generate HTML report
        self.console.print("\n[bold cyan]Generating test report...[/bold cyan]")
        report_file = self.generate_html_report(test_plan, results_dir)
        self.console.print(f"[bold green]✓ Report generated:[/bold green] {report_file}\n")

        # Cleanup any leftover resources in namespace
        self.cleanup_namespace()

    def _generate_workload(self, base: Dict, overrides: Dict) -> Dict:
        """Generate OMB workload from test plan"""
        workload = {
            'name': overrides.get('name', base['name']),
            'topics': overrides.get('workload_overrides', {}).get('topics', base['topics']),
            'partitionsPerTopic': overrides.get('workload_overrides', {}).get('partitions_per_topic', base['partitions_per_topic']),
            'messageSize': overrides.get('workload_overrides', {}).get('message_size', base['message_size']),
            'payloadFile': 'payload/payload-1Kb.data',  # Use OMB's built-in payload file
            'subscriptionsPerTopic': base.get('subscriptions_per_topic', 1),
            'consumerPerSubscription': overrides.get('workload_overrides', {}).get('consumers_per_topic', base.get('consumers_per_topic', 1)),
            'producersPerTopic': overrides.get('workload_overrides', {}).get('producers_per_topic', base.get('producers_per_topic', 1)),
            'consumerBacklogSizeGB': base.get('consumer_backlog_size_gb', 0),
            'testDurationMinutes': overrides.get('workload_overrides', {}).get('test_duration_minutes', base.get('test_duration_minutes', 5)),
            'warmupDurationMinutes': overrides.get('workload_overrides', {}).get('warmup_duration_minutes', base.get('warmup_duration_minutes', 1)),
        }

        if overrides['type'] == 'fixed_rate' and 'producer_rate' in overrides:
            workload['producerRate'] = overrides['producer_rate']

        return workload

    def generate_html_report(self, test_plan: Dict, results_dir: Path) -> Path:
        """
        Generate comprehensive HTML report with test results and Grafana links.

        Args:
            test_plan: Test plan configuration
            results_dir: Directory containing test result files

        Returns:
            Path to generated HTML report
        """
        report_file = self.experiment_dir / "test_report.html"

        # Collect all result files
        log_files = list(results_dir.glob("*.log"))
        json_files = list(results_dir.glob("*.json"))

        # Build HTML
        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OMB Test Report - {self.experiment_id}</title>
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }}
        h1 {{
            color: #2c3e50;
            border-bottom: 3px solid #3498db;
            padding-bottom: 10px;
        }}
        h2 {{
            color: #34495e;
            margin-top: 30px;
            border-left: 4px solid #3498db;
            padding-left: 15px;
        }}
        .metadata {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }}
        .metadata table {{
            width: 100%;
            border-collapse: collapse;
        }}
        .metadata td {{
            padding: 8px;
            border-bottom: 1px solid #ecf0f1;
        }}
        .metadata td:first-child {{
            font-weight: bold;
            color: #7f8c8d;
            width: 200px;
        }}
        .grafana-link {{
            display: inline-block;
            background: #e74c3c;
            color: white;
            padding: 12px 24px;
            text-decoration: none;
            border-radius: 5px;
            font-weight: bold;
            margin: 10px 10px 10px 0;
        }}
        .grafana-link:hover {{
            background: #c0392b;
        }}
        .test-results {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }}
        .test-card {{
            background: #ecf0f1;
            padding: 15px;
            border-radius: 5px;
            margin: 10px 0;
            border-left: 4px solid #27ae60;
        }}
        .test-card.failed {{
            border-left-color: #e74c3c;
        }}
        .log-viewer {{
            background: #2c3e50;
            color: #ecf0f1;
            padding: 15px;
            border-radius: 5px;
            font-family: 'Courier New', monospace;
            font-size: 12px;
            max-height: 400px;
            overflow-y: auto;
            white-space: pre-wrap;
            word-wrap: break-word;
        }}
        .status-badge {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: bold;
        }}
        .status-success {{
            background: #27ae60;
            color: white;
        }}
        .status-failed {{
            background: #e74c3c;
            color: white;
        }}
        code {{
            background: #ecf0f1;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: 'Courier New', monospace;
        }}
    </style>
</head>
<body>
    <h1>OpenMessaging Benchmark Test Report</h1>

    <div class="metadata">
        <h2>Experiment Information</h2>
        <table>
            <tr>
                <td>Experiment ID</td>
                <td><code>{self.experiment_id}</code></td>
            </tr>
            <tr>
                <td>Test Plan</td>
                <td>{test_plan.get('name', 'N/A')}</td>
            </tr>
            <tr>
                <td>Description</td>
                <td>{test_plan.get('description', 'N/A')}</td>
            </tr>
            <tr>
                <td>K8s Namespace (OMB)</td>
                <td><code>{self.namespace}</code></td>
            </tr>
            <tr>
                <td>K8s Namespace (Pulsar)</td>
                <td><code>pulsar</code></td>
            </tr>
            <tr>
                <td>Pulsar Tenant/Namespace</td>
                <td><code>{self.pulsar_tenant_namespace}</code></td>
            </tr>
            <tr>
                <td>Pulsar Service URL</td>
                <td><code>{self.pulsar_service_url}</code></td>
            </tr>
            <tr>
                <td>Results Directory</td>
                <td><code>{self.experiment_dir}</code></td>
            </tr>
            <tr>
                <td>Generated</td>
                <td>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</td>
            </tr>
        </table>
    </div>

    <div class="metadata">
        <h2>Monitoring & Dashboards</h2>
        <p>View test metrics in Grafana (VPN required):</p>
"""

        # Generate time range for dashboards (5 min before start to 5 min after end)
        if self.test_start_time and self.test_end_time:
            from_timestamp = int((self.test_start_time.timestamp() - 300) * 1000)  # -5 min in ms
            to_timestamp = int((self.test_end_time.timestamp() + 300) * 1000)      # +5 min in ms
            from_time = str(from_timestamp)
            to_time = str(to_timestamp)
        else:
            from_time = 'now-15m'
            to_time = 'now'

        # Generate dashboard links with test time range
        messaging_url = self._get_grafana_url(from_time, to_time, "/d/EetmjdhnA/pulsar-messaging")
        jvm_url = self._get_grafana_url(from_time, to_time, "/d/ystagDCsB/pulsar-jvm")
        proxy_url = self._get_grafana_url(from_time, to_time, "/d/vgnAupsuh/pulsar-proxy")

        html_content += f"""
        <a href="{messaging_url}" target="_blank" class="grafana-link">Pulsar Messaging</a>
        <a href="{jvm_url}" target="_blank" class="grafana-link">JVM Metrics</a>
        <a href="{proxy_url}" target="_blank" class="grafana-link">Proxy Metrics</a>
        <p style="color: #7f8c8d; font-size: 14px; margin-top: 10px;">
            Namespace: <strong>{self.pulsar_tenant_namespace.replace('public/', '')}</strong><br>
            Time range: Test execution ± 5 minutes
        </p>
    </div>

    <div class="test-results">
        <h2>Test Results</h2>
        <p>Total tests executed: {len(test_plan.get('test_runs', []))}</p>
        <p>Log files found: {len(log_files)}</p>
        <p>JSON results found: {len(json_files)}</p>
"""

        # Add test cards for each test
        for test_run in test_plan.get('test_runs', []):
            test_name = test_run['name']
            log_file = results_dir / f"{test_name}.log"
            json_file = results_dir / f"{test_name}.json"

            has_log = log_file.exists()
            has_json = json_file.exists()
            status = "success" if (has_log or has_json) else "failed"

            html_content += f"""
        <div class="test-card {status}">
            <h3>{test_name} <span class="status-badge status-{status}">{'COMPLETED' if status == 'success' else 'NO RESULTS'}</span></h3>
            <p><strong>Type:</strong> {test_run.get('type', 'N/A')}</p>
            <p><strong>Description:</strong> {test_run.get('description', 'N/A')}</p>
"""

            if has_log:
                with open(log_file, 'r') as f:
                    log_content = f.read()
                    # Truncate if too long
                    if len(log_content) > 5000:
                        log_content = log_content[:5000] + f"\n\n... (truncated, full log: {log_file}) ..."

                html_content += f"""
            <h4>Test Logs</h4>
            <div class="log-viewer">{log_content if log_content else 'No log content'}</div>
"""

            if has_json:
                html_content += f"""
            <p style="margin-top: 10px;">
                <strong>JSON Results:</strong> <code>{json_file}</code>
            </p>
"""

            html_content += """
        </div>
"""

        html_content += """
    </div>
</body>
</html>
"""

        # Write report
        with open(report_file, 'w') as f:
            f.write(html_content)

        logger.info(f"HTML report generated: {report_file}")
        return report_file

    def parse_omb_results(self, result_files: List[Path]) -> Dict:
        """
        Parse OpenMessaging Benchmark JSON results.

        Args:
            result_files: List of JSON result file paths

        Returns:
            Dictionary with parsed metrics in ReportGenerator format:
            {
                'throughput': {test_name: {'publish_rate': X, 'consume_rate': Y}},
                'latency': {test_name: {'p50': X, 'p95': Y, 'p99': Z, 'p999': A, 'max': B}},
                'errors': {test_name: {'publish_errors': X, 'consume_errors': Y}}
            }
        """
        logger.info(f"Parsing {len(result_files)} result files...")

        metrics = {
            'throughput': {},
            'latency': {},
            'errors': {}
        }

        for result_file in result_files:
            test_name = result_file.stem  # Filename without extension

            try:
                with open(result_file, 'r') as f:
                    data = json.load(f)

                # Extract throughput metrics
                # OMB stores rates as arrays of periodic measurements, we use the average
                publish_rates = data.get('publishRate', [])
                consume_rates = data.get('consumeRate', [])

                avg_publish_rate = sum(publish_rates) / len(publish_rates) if publish_rates else 0
                avg_consume_rate = sum(consume_rates) / len(consume_rates) if consume_rates else 0

                metrics['throughput'][test_name] = {
                    'publish_rate': avg_publish_rate,
                    'consume_rate': avg_consume_rate
                }

                # Extract latency metrics (in milliseconds)
                metrics['latency'][test_name] = {
                    'p50': data.get('publishLatency50pct', 0),
                    'p95': data.get('publishLatency95pct', 0),
                    'p99': data.get('publishLatency99pct', 0),
                    'p999': data.get('publishLatency999pct', 0),
                    'max': data.get('publishLatencyMax', 0)
                }

                # Extract error metrics
                # OMB doesn't explicitly track errors in JSON, so we'll default to 0
                # Errors would show up as job failures or in logs
                metrics['errors'][test_name] = {
                    'publish_errors': 0,
                    'consume_errors': 0
                }

                logger.info(f"✓ Parsed results for '{test_name}': "
                           f"{avg_publish_rate:.0f} msg/s, "
                           f"p99={data.get('publishLatency99pct', 0):.2f}ms")

            except Exception as e:
                logger.error(f"Failed to parse {result_file}: {e}")
                # Add placeholder data for failed parse
                metrics['throughput'][test_name] = {'publish_rate': 0, 'consume_rate': 0}
                metrics['latency'][test_name] = {'p50': 0, 'p95': 0, 'p99': 0, 'p999': 0, 'max': 0}
                metrics['errors'][test_name] = {'publish_errors': 1, 'consume_errors': 0}

        return metrics

    def generate_report(self) -> None:
        """Generate comprehensive experiment report with metrics and costs"""
        logger.info("="*60)
        logger.info("GENERATING REPORT")
        logger.info("="*60)

        # Find all result files
        results_dir = self.experiment_dir / "benchmark_results"
        if not results_dir.exists():
            logger.error(f"Results directory not found: {results_dir}")
            logger.error("Run tests first using: orchestrator.py run --test-plan <file>")
            return

        result_files = list(results_dir.glob("*.json"))
        if not result_files:
            logger.error(f"No result files found in {results_dir}")
            logger.error("Expected JSON files from OMB tests")
            return

        logger.info(f"Found {len(result_files)} result files")

        # Parse OMB results
        metrics = self.parse_omb_results(result_files)

        # Load experiment configuration
        config_file = self.experiment_dir / "infrastructure.yaml"
        config = {}
        region = "us-east-1"  # Default region
        if config_file.exists():
            config = self.load_config(config_file)
            region = config.get('region', 'us-east-1')

        # Get cost data
        logger.info("Fetching AWS cost data...")
        from cost_tracker import CostTracker
        cost_tracker = CostTracker(region=region)
        cost_data = cost_tracker.get_experiment_costs(self.experiment_id)

        # Generate report package
        logger.info("Generating HTML report...")
        from report_generator import ReportGenerator
        generator = ReportGenerator(self.experiment_dir)

        report_dir = generator.create_report_package(
            results_files=result_files,
            cost_data=cost_data,
            config=config,
            include_raw_data=True
        )

        logger.info("="*60)
        logger.info("REPORT GENERATED")
        logger.info(f"  HTML: {report_dir}/index.html")
        logger.info(f"  CSV:  {report_dir}/metrics.csv")
        logger.info(f"  JSON: {report_dir}/metrics.json")
        logger.info("="*60)

    @staticmethod
    def resolve_experiment_id(experiment_id: str) -> str:
        """Resolve experiment ID, handling 'latest' shortcut"""
        if experiment_id == "latest":
            latest_link = RESULTS_DIR / "latest"
            if not latest_link.exists():
                raise OrchestratorError("No experiments found")
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

            is_latest = ""
            latest_link = RESULTS_DIR / "latest"
            if latest_link.exists() and latest_link.resolve() == exp_dir:
                is_latest = " (latest)"

            print(f"{exp_id:30} {timestamp.strftime('%Y-%m-%d %H:%M:%S')}{is_latest}")
        print("=" * 60)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Pulsar OMB Load Testing Orchestrator\n\n"
                    "Run OpenMessaging Benchmark tests against existing Pulsar clusters.\n"
                    "NOTE: Pulsar deployment must be managed externally.",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Run tests command
    run_parser = subparsers.add_parser("run", help="Run benchmark tests")
    run_parser.add_argument("--test-plan", type=Path, required=True, help="Test plan file")
    run_parser.add_argument("--experiment-id", help="Experiment ID (auto-generated if not provided)")

    # Report command
    report_parser = subparsers.add_parser("report", help="Generate report")
    report_parser.add_argument("--experiment-id", default="latest", help="Experiment ID (default: latest)")

    # List command
    list_parser = subparsers.add_parser("list", help="List experiments")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        # Handle list command
        if args.command == "list":
            Orchestrator.list_experiments()
            return

        # Resolve experiment ID
        experiment_id = getattr(args, "experiment_id", None)
        if experiment_id:
            experiment_id = Orchestrator.resolve_experiment_id(experiment_id)

        orchestrator = Orchestrator(experiment_id)

        # Execute command
        if args.command == "run":
            orchestrator.run_tests(args.test_plan)
        elif args.command == "report":
            orchestrator.generate_report()

    except OrchestratorError as e:
        logger.error(f"Orchestrator error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
