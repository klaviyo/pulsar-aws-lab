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

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Project directories
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
RESULTS_DIR = Path.home() / ".pulsar-omb-lab"

# Pulsar cluster connection details
PULSAR_SERVICE_URL = "pulsar://pulsar-proxy.pulsar.svc.cluster.local:6650"
PULSAR_HTTP_URL = "http://pulsar-proxy.pulsar.svc.cluster.local:80"

# OMB Docker image
DEFAULT_OMB_IMAGE = "439508887365.dkr.ecr.us-east-1.amazonaws.com/sre/pulsar-omb:latest"


class OrchestratorError(Exception):
    """Base exception for orchestrator errors"""
    pass


class Orchestrator:
    """Main orchestrator for OMB load testing against existing Pulsar clusters"""

    def __init__(self, experiment_id: Optional[str] = None, namespace: str = "pulsar", omb_image: Optional[str] = None):
        """
        Initialize orchestrator with experiment tracking.

        Args:
            experiment_id: Unique experiment identifier (auto-generated if not provided)
            namespace: Kubernetes namespace where OMB jobs will run (default: pulsar)
            omb_image: OMB Docker image to use (default: from DEFAULT_OMB_IMAGE)
        """
        self.experiment_id = experiment_id or f"exp-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        self.experiment_dir = RESULTS_DIR / self.experiment_id
        self.experiment_dir.mkdir(parents=True, exist_ok=True)
        self.namespace = namespace
        self.pulsar_service_url = PULSAR_SERVICE_URL
        self.pulsar_http_url = PULSAR_HTTP_URL
        self.omb_image = omb_image or DEFAULT_OMB_IMAGE

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
        print(f"Namespace: {self.namespace}")
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

    def _cleanup_test_topics(self, test_name: str, workload_config: Dict) -> None:
        """
        Delete Pulsar topics created during the test.

        Args:
            test_name: Name of the test
            workload_config: Workload configuration containing topic count and naming
        """
        logger.info(f"Cleaning up Pulsar topics for test '{test_name}'...")

        # Get workload details
        workload_name = workload_config.get('name', test_name)
        num_topics = workload_config.get('topics', 1)
        num_partitions = workload_config.get('partitionsPerTopic', 1)

        # OMB creates topics with pattern: benchmark-{workload_name}-{topic_index}
        topics_deleted = 0
        for topic_idx in range(num_topics):
            topic_name = f"benchmark-{workload_name}-{topic_idx}"

            # For partitioned topics, delete the parent topic (this cascades to partitions)
            if num_partitions > 1:
                topic_url = f"persistent://public/default/{topic_name}"
            else:
                topic_url = f"persistent://public/default/{topic_name}"

            # Delete topic via pulsar-admin
            result = self.run_command(
                ["kubectl", "exec", "-n", self.namespace, "pulsar-broker-0", "--",
                 "bin/pulsar-admin", "topics", "delete", topic_url],
                f"Delete topic {topic_name}",
                check=False,
                capture_output=True
            )

            if result.returncode == 0:
                topics_deleted += 1
                logger.info(f"  ✓ Deleted topic: {topic_name}")
            elif "does not exist" in result.stderr or "TopicNotFound" in result.stderr:
                logger.debug(f"  ⊗ Topic {topic_name} doesn't exist (already deleted or never created)")
            else:
                logger.warning(f"  ✗ Failed to delete topic {topic_name}: {result.stderr}")

        if topics_deleted > 0:
            logger.info(f"✓ Deleted {topics_deleted} topic(s) for test '{test_name}'")
        else:
            logger.info(f"No topics to delete for test '{test_name}'")

    def run_omb_job(self, test_config: Dict, workload_config: Dict) -> str:
        """
        Run OpenMessaging Benchmark job with distributed workers.

        Args:
            test_config: Test run configuration
            workload_config: Workload specification

        Returns:
            Test results as JSON string

        Raises:
            OrchestratorError: If test execution fails
        """
        test_name = test_config['name']
        num_workers = test_config.get('num_workers', 3)  # Default to 3 workers
        logger.info(f"Running OMB test: {test_name} (with {num_workers} workers)")

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

        # Deploy OMB workers StatefulSet
        workers_yaml = self._generate_omb_workers_yaml(test_name, num_workers)
        workers_file = self.experiment_dir / f"omb_workers_{test_name}.yaml"

        with open(workers_file, 'w') as f:
            f.write(workers_yaml)

        self.run_command(
            ["kubectl", "apply", "-f", str(workers_file)],
            f"Deploy OMB workers for {test_name}"
        )

        # Wait for workers to be ready
        logger.info(f"Waiting for {num_workers} worker pods to be ready...")
        self.run_command(
            ["kubectl", "wait", "--for=condition=ready",
             f"pod", "-l", f"app=omb-worker,test={test_name}",
             "-n", self.namespace, "--timeout=5m"],
            f"Wait for worker pods to be ready"
        )
        logger.info(f"✓ All {num_workers} workers are ready")

        # Create OMB driver Job
        job_yaml = self._generate_omb_job_yaml(test_name, num_workers)
        job_file = self.experiment_dir / f"omb_job_{test_name}.yaml"

        with open(job_file, 'w') as f:
            f.write(job_yaml)

        # Apply Job
        self.run_command(
            ["kubectl", "apply", "-f", str(job_file)],
            f"Create OMB driver Job for {test_name}"
        )

        # Wait for Job completion or failure
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
                    logger.info(f"✓ Job {test_name} completed successfully (succeeded: {succeeded_count})")
                    # Give pod a moment to fully terminate before collecting logs
                    time.sleep(2)
                    break
                elif failed_count > 0:
                    job_failed = True
                    logger.error(f"✗ Job {test_name} failed (failed: {failed_count})")
                    # Give pod a moment to fully terminate before collecting logs
                    time.sleep(2)
                    break

                # Still running - log progress
                elapsed = int(time.time() - start_time)
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
        logger.info(f"Collecting results for {test_name}...")
        results = self._collect_job_logs(test_name, success=True)

        # Cleanup Pulsar topics created during test
        self._cleanup_test_topics(test_name, workload_config)

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
      namespacePrefix: public/default
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
            echo "Starting OMB worker on $(hostname)"
            /app/bin/benchmark-worker --port 8080
        ports:
        - containerPort: 8080
          name: http
        readinessProbe:
          httpGet:
            path: /
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 5
        livenessProbe:
          httpGet:
            path: /
            port: 8080
          initialDelaySeconds: 10
          periodSeconds: 10
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
