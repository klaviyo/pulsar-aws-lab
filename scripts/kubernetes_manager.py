"""
Kubernetes infrastructure management for Pulsar OMB Orchestrator.
Handles namespace creation, PVC management, pod monitoring, and kubectl operations.
"""

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class KubernetesError(Exception):
    """Raised when Kubernetes operations fail."""
    pass


class KubernetesManager:
    """Manages Kubernetes infrastructure operations."""

    def __init__(self, namespace: str, experiment_dir: Path):
        """
        Initialize Kubernetes manager.

        Args:
            namespace: Kubernetes namespace for OMB tests
            experiment_dir: Directory for experiment artifacts
        """
        self.namespace = namespace
        self.experiment_dir = experiment_dir

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
            KubernetesError: If command fails and check=True
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
                raise KubernetesError(error_msg) from e
            # Return a dummy result for non-checked commands
            return subprocess.CompletedProcess(cmd, returncode=124, stdout="", stderr=str(e))
        except subprocess.CalledProcessError as e:
            error_msg = f"Command failed: {description}"
            if capture_output and e.stderr:
                error_msg += f"\nError: {e.stderr}"
            logger.error(error_msg)
            raise KubernetesError(error_msg) from e

    def ensure_namespace_exists(self) -> None:
        """Ensure the K8s namespace exists, create it if not."""
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

    def ensure_results_pvc_exists(self) -> None:
        """Ensure shared PVC for OMB results exists, create it if not."""
        pvc_name = "omb-results"

        result = self.run_command(
            ["kubectl", "get", "pvc", pvc_name, "-n", self.namespace],
            f"Check if PVC {pvc_name} exists",
            capture_output=True,
            check=False
        )

        if result.returncode != 0:
            logger.info(f"Creating PVC: {pvc_name}")

            pvc_yaml = f"""apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {pvc_name}
  namespace: {self.namespace}
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 10Gi
  storageClassName: gp2
"""
            pvc_file = self.experiment_dir / f"{pvc_name}.yaml"
            with open(pvc_file, 'w') as f:
                f.write(pvc_yaml)

            self.run_command(
                ["kubectl", "apply", "-f", str(pvc_file)],
                f"Create PVC {pvc_name}"
            )
            logger.info(f"✓ PVC '{pvc_name}' created")
        else:
            logger.debug(f"PVC '{pvc_name}' already exists")

    def setup_kubectl_context(self, region: str, cluster_name: str) -> None:
        """
        Configure kubectl to connect to EKS cluster.

        Args:
            region: AWS region
            cluster_name: EKS cluster name
        """
        logger.info(f"Configuring kubectl for cluster: {cluster_name} in {region}")

        self.run_command(
            ["aws", "eks", "update-kubeconfig",
             "--region", region,
             "--name", cluster_name],
            f"Update kubeconfig for {cluster_name}"
        )

        # Verify connection
        result = self.run_command(
            ["kubectl", "cluster-info"],
            "Verify kubectl cluster connection",
            capture_output=True
        )

        logger.info(f"✓ kubectl configured for cluster {cluster_name}")
        logger.debug(f"Cluster info: {result.stdout}")

    def wait_for_pods_ready(self, timeout_seconds: int = 600) -> None:
        """
        Wait for all pods in namespace to be in Ready state.

        Args:
            timeout_seconds: Maximum time to wait

        Raises:
            KubernetesError: If pods don't become ready within timeout
        """
        logger.info(f"Waiting for pods in namespace '{self.namespace}' to be ready...")

        start_time = time.time()

        while time.time() - start_time < timeout_seconds:
            result = self.run_command(
                ["kubectl", "get", "pods", "-n", self.namespace, "-o", "json"],
                f"Get pods in {self.namespace}",
                capture_output=True
            )

            pods_data = json.loads(result.stdout)
            all_ready = True
            pod_status = []

            for pod in pods_data.get('items', []):
                pod_name = pod['metadata']['name']
                phase = pod['status'].get('phase', 'Unknown')

                # Check container readiness
                container_ready = True
                if 'containerStatuses' in pod['status']:
                    for container in pod['status']['containerStatuses']:
                        if not container.get('ready', False):
                            container_ready = False
                            break

                if phase != 'Running' or not container_ready:
                    all_ready = False

                pod_status.append(f"{pod_name}: {phase} (ready: {container_ready})")

            if all_ready and len(pod_status) > 0:
                logger.info(f"✓ All {len(pod_status)} pods ready in '{self.namespace}'")
                return

            logger.debug(f"Pod status: {pod_status}")
            time.sleep(5)

        raise KubernetesError(
            f"Timeout waiting for pods to be ready after {timeout_seconds}s"
        )

    def get_pod_component(self, pod_name: str) -> str:
        """
        Determine which Pulsar component a pod belongs to.

        Args:
            pod_name: Name of the pod

        Returns:
            Component name (e.g., 'zookeeper', 'bookkeeper', 'broker')
        """
        if 'zookeeper' in pod_name:
            return 'zookeeper'
        elif 'bookkeeper' in pod_name:
            return 'bookkeeper'
        elif 'broker' in pod_name:
            return 'broker'
        elif 'proxy' in pod_name:
            return 'proxy'
        elif 'autorecovery' in pod_name:
            return 'autorecovery'
        else:
            return 'unknown'

    def cleanup_namespace(self) -> None:
        """Delete the Kubernetes namespace and all its resources."""
        logger.info(f"Cleaning up namespace: {self.namespace}")

        self.run_command(
            ["kubectl", "delete", "namespace", self.namespace, "--wait=true"],
            f"Delete namespace {self.namespace}",
            check=False,
            timeout=300
        )

        logger.info(f"✓ Namespace '{self.namespace}' deleted")
