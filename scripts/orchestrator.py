#!/usr/bin/env python3
"""
Pulsar AWS Lab Orchestrator
Workflow controller for EKS cluster management, Helm deployments, and benchmark testing
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

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Project directories
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
TERRAFORM_DIR = PROJECT_ROOT / "terraform"
HELM_DIR = PROJECT_ROOT / "helm"
RESULTS_DIR = Path.home() / ".pulsar-aws-lab"


class OrchestratorError(Exception):
    """Base exception for orchestrator errors"""
    pass


class Orchestrator:
    """Main orchestrator for EKS-based Pulsar deployments"""

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
        self.cluster_name = None
        self.helm_release_name = "pulsar"
        self.namespace = "pulsar"

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

    def helm_deploy(self, values_overrides: Optional[Dict] = None) -> None:
        """
        Deploy or upgrade Pulsar using Helm.

        Args:
            values_overrides: Optional Helm values to override

        Raises:
            OrchestratorError: If Helm deployment fails
        """
        logger.info(f"Deploying Helm chart: {self.helm_release_name}")

        # Check if release exists
        result = self.run_command(
            ["helm", "list", "-n", self.namespace, "-o", "json"],
            "Check existing Helm releases",
            capture_output=True,
            check=False
        )

        releases = json.loads(result.stdout) if result.stdout else []
        release_exists = any(r['name'] == self.helm_release_name for r in releases)

        # Delete existing OMB Jobs if upgrading (Jobs are immutable and can't be patched)
        if release_exists:
            logger.info("Checking for existing OMB Jobs to delete...")
            try:
                # Get OMB Jobs
                result = self.run_command(
                    ["kubectl", "get", "jobs", "-n", self.namespace,
                     "-l", f"app.kubernetes.io/instance={self.helm_release_name}",
                     "-l", "component in (omb-producer,omb-consumer)",
                     "-o", "name"],
                    "List existing OMB Jobs",
                    capture_output=True,
                    check=False
                )

                if result.stdout:
                    jobs = result.stdout.strip().split('\n')
                    for job in jobs:
                        if job:
                            logger.info(f"Deleting {job}...")
                            self.run_command(
                                ["kubectl", "delete", job, "-n", self.namespace, "--wait=false"],
                                f"Delete {job}",
                                check=False
                            )
            except Exception as e:
                logger.warning(f"Failed to delete existing OMB Jobs: {e}")

        # Prepare Helm command
        if release_exists:
            cmd = ["helm", "upgrade", self.helm_release_name]
            action = "Upgrading"
        else:
            cmd = ["helm", "install", self.helm_release_name]
            action = "Installing"

        cmd.extend([
            str(HELM_DIR),
            "-n", self.namespace,
            "--create-namespace",
            "--wait",
            "--timeout", "15m"
        ])

        # Add custom values if provided
        if values_overrides:
            values_file = self.experiment_dir / "helm-values.yaml"
            with open(values_file, 'w') as f:
                yaml.dump(values_overrides, f)
            cmd.extend(["-f", str(values_file)])

        # Execute Helm deploy
        logger.info(f"{action} {self.helm_release_name}...")
        logger.info("This may take up to 15 minutes. Helm will wait for all pods to be ready.")
        logger.info(f"To monitor progress in another terminal:")
        logger.info(f"  kubectl get pods -n {self.namespace} -w")
        logger.info(f"  kubectl get events -n {self.namespace} --sort-by='.lastTimestamp'")
        logger.info(f"Command: {' '.join(cmd)}")

        # Run without capturing output so we see Helm's progress
        self.run_command(cmd, f"{action} Helm release", capture_output=False)

        logger.info(f"✓ Helm release {self.helm_release_name} deployed successfully")

    def helm_undeploy(self) -> None:
        """
        Uninstall Pulsar Helm release.

        Raises:
            OrchestratorError: If Helm uninstall fails
        """
        logger.info(f"Uninstalling Helm release: {self.helm_release_name}")

        self.run_command(
            ["helm", "uninstall", self.helm_release_name,
             "-n", self.namespace,
             "--wait",
             "--timeout", "10m"],
            "Uninstall Helm release"
        )

        logger.info(f"✓ Helm release {self.helm_release_name} uninstalled")

        # Wait for namespace cleanup
        logger.info("Waiting for pods to terminate...")
        timeout = 120
        start = time.time()

        while time.time() - start < timeout:
            result = self.run_command(
                ["kubectl", "get", "pods", "-n", self.namespace, "-o", "json"],
                "Check pod status",
                capture_output=True,
                check=False
            )

            pods = json.loads(result.stdout) if result.stdout else {}
            pod_count = len(pods.get('items', []))

            if pod_count == 0:
                logger.info("✓ All pods terminated")
                break

            logger.info(f"Waiting for {pod_count} pods to terminate...")
            time.sleep(5)

    def wipe_namespace(self, force: bool = False) -> None:
        """
        Force delete namespace and all resources within it.
        This function will aggressively remove everything without manual intervention.

        Args:
            force: Skip confirmation prompt

        Raises:
            OrchestratorError: If wipe operation fails
        """
        logger.info(f"Wiping namespace: {self.namespace}")

        # Confirmation prompt
        if not force:
            print(f"\n⚠️  WARNING: This will forcefully delete the '{self.namespace}' namespace")
            print("    and ALL resources within it, including:")
            print("    - Helm releases")
            print("    - Pods, Deployments, StatefulSets, Jobs")
            print("    - PersistentVolumeClaims and PersistentVolumes")
            print("    - Services, ConfigMaps, Secrets")
            print()
            response = input("Are you sure you want to continue? [yes/no]: ")
            if response.lower() not in ["yes", "y"]:
                logger.info("Wipe cancelled by user")
                return

        # Check if namespace exists
        result = self.run_command(
            ["kubectl", "get", "namespace", self.namespace],
            "Check if namespace exists",
            capture_output=True,
            check=False
        )

        if result.returncode != 0:
            logger.info(f"Namespace {self.namespace} does not exist")
            return

        logger.info("Step 1: Uninstalling Helm releases...")
        try:
            result = self.run_command(
                ["helm", "list", "-n", self.namespace, "-o", "json"],
                "List Helm releases",
                capture_output=True,
                check=False,
                timeout=10
            )
            if result.stdout:
                releases = json.loads(result.stdout)
                for release in releases:
                    release_name = release.get('name')
                    logger.info(f"  Uninstalling Helm release: {release_name}")
                    subprocess.run(
                        ["helm", "uninstall", release_name, "-n", self.namespace, "--no-hooks", "--wait=false"],
                        capture_output=True,
                        timeout=15,
                        check=False
                    )
        except Exception as e:
            logger.warning(f"Failed to uninstall Helm releases: {e}")

        logger.info("Step 2: Scaling down all controllers to prevent recreation...")
        controller_types = ["deployments", "statefulsets", "daemonsets", "replicasets"]
        for controller in controller_types:
            try:
                subprocess.run(
                    ["kubectl", "scale", controller, "--all", "--replicas=0", "-n", self.namespace],
                    capture_output=True,
                    timeout=10,
                    check=False
                )
            except Exception:
                pass

        logger.info("Step 3: Removing all finalizers from all resources...")
        # Get ALL resource types in the namespace
        try:
            result = subprocess.run(
                ["kubectl", "api-resources", "--verbs=list", "--namespaced", "-o", "name"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False
            )
            if result.stdout:
                all_resource_types = result.stdout.strip().split('\n')
                for resource_type in all_resource_types:
                    if resource_type and not resource_type.startswith('events'):
                        try:
                            result = subprocess.run(
                                ["kubectl", "get", resource_type, "-n", self.namespace, "-o", "name"],
                                capture_output=True,
                                text=True,
                                timeout=5,
                                check=False
                            )
                            if result.stdout:
                                resources = result.stdout.strip().split('\n')
                                for resource in resources:
                                    if resource:
                                        subprocess.run(
                                            ["kubectl", "patch", resource, "-n", self.namespace,
                                             "-p", '{"metadata":{"finalizers":null}}',
                                             "--type=merge"],
                                            capture_output=True,
                                            timeout=3,
                                            check=False
                                        )
                        except Exception:
                            continue
        except Exception as e:
            logger.warning(f"Bulk finalizer removal failed: {e}")

        logger.info("Step 4: Force deleting all resources...")
        # Delete everything in parallel with aggressive flags
        delete_commands = [
            ["kubectl", "delete", "all", "--all", "-n", self.namespace, "--grace-period=0", "--force", "--ignore-not-found", "--wait=false"],
            ["kubectl", "delete", "pvc", "--all", "-n", self.namespace, "--grace-period=0", "--force", "--ignore-not-found", "--wait=false"],
            ["kubectl", "delete", "configmaps", "--all", "-n", self.namespace, "--grace-period=0", "--force", "--ignore-not-found", "--wait=false"],
            ["kubectl", "delete", "secrets", "--all", "-n", self.namespace, "--grace-period=0", "--force", "--ignore-not-found", "--wait=false"],
            ["kubectl", "delete", "serviceaccounts", "--all", "-n", self.namespace, "--grace-period=0", "--force", "--ignore-not-found", "--wait=false"],
            ["kubectl", "delete", "roles", "--all", "-n", self.namespace, "--grace-period=0", "--force", "--ignore-not-found", "--wait=false"],
            ["kubectl", "delete", "rolebindings", "--all", "-n", self.namespace, "--grace-period=0", "--force", "--ignore-not-found", "--wait=false"],
        ]

        for cmd in delete_commands:
            try:
                subprocess.run(cmd, capture_output=True, timeout=20, check=False)
            except Exception:
                pass

        # Give deletions a moment to process
        time.sleep(5)

        logger.info("Step 5: Force deleting PersistentVolumes bound to this namespace...")
        try:
            result = subprocess.run(
                ["kubectl", "get", "pv", "-o", "json"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False
            )
            if result.stdout:
                pvs = json.loads(result.stdout)
                for pv in pvs.get('items', []):
                    claim_ref = pv.get('spec', {}).get('claimRef', {})
                    if claim_ref.get('namespace') == self.namespace:
                        pv_name = pv['metadata']['name']
                        logger.info(f"  Deleting PV: {pv_name}")
                        # Remove finalizers first
                        subprocess.run(
                            ["kubectl", "patch", "pv", pv_name,
                             "-p", '{"metadata":{"finalizers":null}}',
                             "--type=merge"],
                            capture_output=True,
                            timeout=5,
                            check=False
                        )
                        # Delete
                        subprocess.run(
                            ["kubectl", "delete", "pv", pv_name, "--grace-period=0", "--force"],
                            capture_output=True,
                            timeout=10,
                            check=False
                        )
        except Exception as e:
            logger.warning(f"PV cleanup failed: {e}")

        logger.info("Step 6: Deleting namespace...")
        subprocess.run(
            ["kubectl", "delete", "namespace", self.namespace, "--wait=false"],
            capture_output=True,
            timeout=10,
            check=False
        )

        # Aggressive retry loop for namespace deletion
        logger.info("Step 7: Waiting for namespace deletion (with retries)...")
        max_attempts = 10
        for attempt in range(max_attempts):
            time.sleep(2)

            result = subprocess.run(
                ["kubectl", "get", "namespace", self.namespace],
                capture_output=True,
                timeout=5,
                check=False
            )

            if result.returncode != 0:
                logger.info(f"✓ Namespace {self.namespace} successfully wiped")
                return

            # Namespace still exists, try patching finalizers again
            logger.info(f"  Attempt {attempt + 1}/{max_attempts}: Removing namespace finalizers...")
            try:
                subprocess.run(
                    ["kubectl", "patch", "namespace", self.namespace,
                     "-p", '{"metadata":{"finalizers":null}}',
                     "--type=merge"],
                    capture_output=True,
                    timeout=5,
                    check=False
                )
                subprocess.run(
                    ["kubectl", "patch", "namespace", self.namespace,
                     "-p", '{"spec":{"finalizers":null}}',
                     "--type=merge"],
                    capture_output=True,
                    timeout=5,
                    check=False
                )
            except Exception:
                pass

        # Final check with detailed error if still exists
        result = subprocess.run(
            ["kubectl", "get", "namespace", self.namespace, "-o", "json"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False
        )

        if result.returncode == 0:
            # Try one last nuclear option: direct API call
            logger.info("Step 8: Using direct API to force delete namespace...")
            try:
                subprocess.run(
                    ["kubectl", "get", "namespace", self.namespace, "-o", "json"],
                    stdout=subprocess.PIPE,
                    text=True
                ).stdout

                # Use kubectl proxy approach
                import threading
                def run_proxy():
                    subprocess.run(["kubectl", "proxy", "--port=8765"], check=False)

                proxy_thread = threading.Thread(target=run_proxy, daemon=True)
                proxy_thread.start()
                time.sleep(2)

                # Direct API call
                import urllib.request
                import json as json_lib
                req = urllib.request.Request(
                    f"http://localhost:8765/api/v1/namespaces/{self.namespace}/finalize",
                    data=json_lib.dumps({"kind":"Namespace","apiVersion":"v1","metadata":{"name":self.namespace},"spec":{"finalizers":[]}}).encode(),
                    headers={'Content-Type': 'application/json'},
                    method='PUT'
                )
                urllib.request.urlopen(req, timeout=5)
                time.sleep(2)
            except Exception as e:
                logger.debug(f"API call approach failed: {e}")

            # Final final check
            result = subprocess.run(
                ["kubectl", "get", "namespace", self.namespace],
                capture_output=True,
                timeout=5,
                check=False
            )

            if result.returncode == 0:
                logger.error(f"✗ Failed to delete namespace {self.namespace} after all attempts")
                logger.error("This is unusual. Check for:")
                logger.error("  1. Admission webhooks blocking deletion")
                logger.error("  2. Custom resource definitions with stuck finalizers")
                logger.error("  3. Cluster-level issues")
                raise OrchestratorError(f"Could not delete namespace {self.namespace}")
            else:
                logger.info(f"✓ Namespace {self.namespace} successfully wiped")
        else:
            logger.info(f"✓ Namespace {self.namespace} successfully wiped")

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

                ready = False
                for condition in conditions:
                    if condition['type'] == 'Ready':
                        ready = condition['status'] == 'True'
                        break

                status_str = f"{phase} ({'Ready' if ready else 'Not Ready'})"
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

    def check_pulsar_health(self) -> None:
        """
        Verify Pulsar broker health endpoint.

        Raises:
            OrchestratorError: If health check fails
        """
        logger.info("Checking Pulsar broker health...")

        # Get broker service
        result = self.run_command(
            ["kubectl", "get", "svc", "-n", self.namespace,
             "-l", "component=broker",
             "-o", "jsonpath={.items[0].metadata.name}"],
            "Get broker service name",
            capture_output=True
        )

        broker_svc = result.stdout.strip()
        if not broker_svc:
            raise OrchestratorError("Broker service not found")

        # Check health via kubectl exec on broker pod
        result = self.run_command(
            ["kubectl", "get", "pods", "-n", self.namespace,
             "-l", "component=broker",
             "-o", "jsonpath={.items[0].metadata.name}"],
            "Get broker pod name",
            capture_output=True
        )

        broker_pod = result.stdout.strip()
        if not broker_pod:
            raise OrchestratorError("Broker pod not found")

        # Check health endpoint
        result = self.run_command(
            ["kubectl", "exec", "-n", self.namespace, broker_pod, "--",
             "curl", "-s", "-f", "http://localhost:8080/admin/v2/brokers/health"],
            "Check broker health endpoint",
            capture_output=True
        )

        logger.info("✓ Pulsar broker health check passed")

    def run_omb_job(self, test_config: Dict, workload_config: Dict) -> str:
        """
        Run OpenMessaging Benchmark job.

        Args:
            test_config: Test run configuration
            workload_config: Workload specification

        Returns:
            Test results as JSON string

        Raises:
            OrchestratorError: If test execution fails
        """
        test_name = test_config['name']
        logger.info(f"Running OMB test: {test_name}")

        # Generate workload ConfigMap
        workload_yaml = self._generate_omb_workload_yaml(test_name, workload_config)
        workload_file = self.experiment_dir / f"workload_{test_name}.yaml"

        with open(workload_file, 'w') as f:
            f.write(workload_yaml)

        # Apply workload ConfigMap
        self.run_command(
            ["kubectl", "apply", "-f", str(workload_file)],
            f"Apply workload ConfigMap for {test_name}"
        )

        # Create OMB Job (modify from template)
        job_yaml = self._generate_omb_job_yaml(test_name)
        job_file = self.experiment_dir / f"omb_job_{test_name}.yaml"

        with open(job_file, 'w') as f:
            f.write(job_yaml)

        # Apply Job
        self.run_command(
            ["kubectl", "apply", "-f", str(job_file)],
            f"Create OMB Job for {test_name}"
        )

        # Wait for Job completion
        logger.info(f"Waiting for test {test_name} to complete...")
        result = self.run_command(
            ["kubectl", "wait", "--for=condition=complete",
             f"job/omb-{test_name}", "-n", self.namespace,
             "--timeout=30m"],
            f"Wait for Job {test_name} completion",
            check=False
        )

        if result.returncode != 0:
            # Check if Job failed
            logger.error(f"Job {test_name} did not complete successfully")
            self._collect_job_logs(test_name, success=False)
            raise OrchestratorError(f"OMB test {test_name} failed")

        # Collect results from Job logs
        logger.info(f"Collecting results for {test_name}...")
        results = self._collect_job_logs(test_name, success=True)

        # Cleanup Job
        self.run_command(
            ["kubectl", "delete", "job", f"omb-{test_name}", "-n", self.namespace],
            f"Delete OMB Job {test_name}",
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
      serviceUrl: pulsar://{self.helm_release_name}-broker:6650
      httpUrl: http://{self.helm_release_name}-broker:8080
    producer:
      batchingEnabled: true
      batchingMaxPublishDelayMs: 1
      blockIfQueueFull: true
      pendingQueueSize: 1000
    consumer:
      subscriptionType: Exclusive
"""

    def _generate_omb_job_yaml(self, test_name: str) -> str:
        """Generate Kubernetes Job YAML for OMB test"""
        return f"""apiVersion: batch/v1
kind: Job
metadata:
  name: omb-{test_name}
  namespace: {self.namespace}
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
      - name: omb
        image: pulsar-omb:latest
        imagePullPolicy: IfNotPresent
        command: ["/bin/bash", "-c"]
        args:
          - |
            echo "Starting OMB test: {test_name}"
            /app/bin/benchmark \\
              --drivers /workload/driver.yaml \\
              --output /results/result.json \\
              /workload/workload.yaml
            echo "Results saved to /results/result.json"
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
        # Get Job pod name
        result = self.run_command(
            ["kubectl", "get", "pods", "-n", self.namespace,
             "-l", f"job-name=omb-{test_name}",
             "-o", "jsonpath={.items[0].metadata.name}"],
            f"Get Job pod for {test_name}",
            capture_output=True,
            check=False
        )

        pod_name = result.stdout.strip()
        if not pod_name:
            logger.warning(f"Could not find pod for Job {test_name}")
            return ""

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

    def deploy_pulsar(
        self,
        config_file: Path,
        values_overrides: Optional[Dict] = None
    ) -> None:
        """
        Deploy Pulsar to EKS using Helm.

        Prerequisites:
            - EKS cluster must already exist (managed externally)
            - kubectl must be configured for the target cluster

        Args:
            config_file: Infrastructure configuration
            values_overrides: Optional Helm values overrides

        Raises:
            OrchestratorError: If deployment fails
        """
        logger.info("="*60)
        logger.info("DEPLOYING PULSAR VIA HELM")
        logger.info("="*60)

        try:
            # Verify kubectl is configured
            result = self.run_command(
                ["kubectl", "cluster-info"],
                "Verify kubectl configuration",
                capture_output=True,
                check=False
            )

            if result.returncode != 0:
                raise OrchestratorError(
                    "kubectl is not configured for a cluster. "
                    "Please ensure your EKS cluster exists and kubectl is configured:\n"
                    "  aws eks update-kubeconfig --region <region> --name <cluster-name>"
                )

            # Load config if not already loaded
            if not self.infrastructure_config:
                self.infrastructure_config = self.load_config(config_file)

            # Deploy via Helm
            self.helm_deploy(values_overrides)

            # Wait for pods
            self.wait_for_pods_ready()

            # Health check
            self.check_pulsar_health()

            logger.info("="*60)
            logger.info("PULSAR DEPLOYED SUCCESSFULLY")
            logger.info("="*60)

        except Exception as e:
            logger.error(f"Pulsar deployment failed: {e}")
            logger.info("Collecting pod logs for troubleshooting...")
            self.collect_pod_logs()
            raise

    def undeploy_pulsar(self) -> None:
        """Remove Pulsar from EKS cluster"""
        logger.info("="*60)
        logger.info("UNDEPLOYING PULSAR")
        logger.info("="*60)

        try:
            self.helm_undeploy()
            logger.info("="*60)
            logger.info("PULSAR UNDEPLOYED SUCCESSFULLY")
            logger.info("="*60)
        except Exception as e:
            logger.error(f"Pulsar undeploy failed: {e}")
            raise

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
                results = self.run_omb_job(test_run, workload)

                # Save results
                result_file = results_dir / f"{test_name}.log"
                with open(result_file, 'w') as f:
                    f.write(results)

                logger.info(f"✓ Test '{test_name}' completed")
                logger.info(f"Results: {result_file}")

            except OrchestratorError as e:
                logger.error(f"Test '{test_name}' failed: {e}")
                continue

        logger.info(f"\n{'='*60}")
        logger.info(f"ALL TESTS COMPLETED")
        logger.info(f"Results: {results_dir}")
        logger.info(f"{'='*60}\n")

    def _generate_workload(self, base: Dict, overrides: Dict) -> Dict:
        """Generate OMB workload from test plan"""
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

        if overrides['type'] == 'fixed_rate' and 'producer_rate' in overrides:
            workload['producerRate'] = overrides['producer_rate']

        return workload

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
        if config_file.exists():
            config = self.load_config(config_file)

        # Get cost data
        logger.info("Fetching AWS cost data...")
        from cost_tracker import CostTracker
        cost_tracker = CostTracker(region=self.region)
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

    def full_lifecycle(
        self,
        config_file: Path,
        test_plan_file: Path,
        runtime_tags: Optional[Dict[str, str]] = None
    ) -> None:
        """
        Execute full test lifecycle: deploy → test → undeploy.

        Args:
            config_file: Infrastructure configuration
            test_plan_file: Test plan configuration
            runtime_tags: Optional runtime tags

        Raises:
            OrchestratorError: If any phase fails
        """
        logger.info("="*60)
        logger.info("STARTING FULL TEST LIFECYCLE")
        logger.info("="*60)

        try:
            # Deploy Pulsar
            self.deploy_pulsar(config_file)

            # Run tests and generate report
            try:
                self.run_tests(test_plan_file)
                self.generate_report()
            finally:
                # Always undeploy
                logger.info("\nUndeploying Pulsar...")
                self.undeploy_pulsar()

            logger.info("="*60)
            logger.info("FULL LIFECYCLE COMPLETED SUCCESSFULLY")
            logger.info("="*60)

        except Exception as e:
            logger.error(f"Lifecycle failed: {e}")
            raise

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
        description="Pulsar AWS Lab Orchestrator (EKS/Helm)\n\n"
                    "NOTE: EKS cluster management is handled externally. "
                    "Ensure your kubectl is configured for the target EKS cluster before running commands.",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Deploy command
    deploy_parser = subparsers.add_parser("deploy", help="Deploy Pulsar via Helm")
    deploy_parser.add_argument("--config", type=Path, default=CONFIG_DIR / "infrastructure.yaml", help="Infrastructure config")
    deploy_parser.add_argument("--experiment-id", help="Experiment ID")

    # Undeploy command
    undeploy_parser = subparsers.add_parser("undeploy", help="Remove Pulsar from cluster")
    undeploy_parser.add_argument("--experiment-id", required=True, help="Experiment ID")

    # Wipe command
    wipe_parser = subparsers.add_parser("wipe", help="Force delete namespace and all resources")
    wipe_parser.add_argument("--force", action="store_true", help="Skip confirmation prompt")
    wipe_parser.add_argument("--namespace", default="pulsar", help="Namespace to wipe (default: pulsar)")

    # Run tests command
    run_parser = subparsers.add_parser("run", help="Run benchmark tests")
    run_parser.add_argument("--test-plan", type=Path, required=True, help="Test plan file")
    run_parser.add_argument("--experiment-id", required=True, help="Experiment ID")

    # Report command
    report_parser = subparsers.add_parser("report", help="Generate report")
    report_parser.add_argument("--experiment-id", required=True, help="Experiment ID")

    # Full lifecycle command
    full_parser = subparsers.add_parser("full", help="Full test cycle (deploy→test→undeploy)")
    full_parser.add_argument("--config", type=Path, default=CONFIG_DIR / "infrastructure.yaml", help="Infrastructure config")
    full_parser.add_argument("--test-plan", type=Path, required=True, help="Test plan file")
    full_parser.add_argument("--experiment-id", help="Experiment ID")
    full_parser.add_argument("--tag", action="append", metavar="KEY=VALUE", help="Additional tags")

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

        # Handle wipe command (doesn't need experiment ID)
        if args.command == "wipe":
            # Create temporary orchestrator with specified namespace
            temp_orchestrator = Orchestrator()
            temp_orchestrator.namespace = args.namespace
            temp_orchestrator.wipe_namespace(force=args.force)
            return

        # Resolve experiment ID
        experiment_id = getattr(args, "experiment_id", None)
        if experiment_id and args.command in ["undeploy", "run", "report"]:
            experiment_id = Orchestrator.resolve_experiment_id(experiment_id)

        orchestrator = Orchestrator(experiment_id)

        # Parse runtime tags
        runtime_tags = {}
        if hasattr(args, 'tag') and args.tag:
            for tag in args.tag:
                if '=' not in tag:
                    raise OrchestratorError(f"Invalid tag format: {tag}")
                key, value = tag.split('=', 1)
                runtime_tags[key] = value

        # Execute command
        if args.command == "deploy":
            orchestrator.deploy_pulsar(args.config)
        elif args.command == "undeploy":
            orchestrator.undeploy_pulsar()
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
