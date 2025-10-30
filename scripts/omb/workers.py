"""
OMB Worker management - persistent workers across test runs.
"""

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class WorkerManager:
    """
    Manages persistent OMB worker pods.

    Workers are deployed as a StatefulSet that persists across orchestrator invocations.
    The StatefulSet is scaled up/down as needed for different test requirements.
    """

    STATEFULSET_NAME = "omb-workers"
    SERVICE_NAME = "omb-workers"

    def __init__(self, namespace: str, omb_image: str, results_dir: Path):
        """
        Initialize worker manager.

        Args:
            namespace: Kubernetes namespace for OMB resources
            omb_image: Docker image for OMB workers
            results_dir: Directory for storing generated manifests
        """
        self.namespace = namespace
        self.omb_image = omb_image
        self.results_dir = results_dir

    def ensure_workers(self, required_count: int) -> None:
        """
        Ensure the required number of workers exist.

        - If workers don't exist, deploy them
        - If fewer workers exist than required, scale up
        - If enough workers exist, do nothing

        Args:
            required_count: Number of workers needed
        """
        current_count = self._get_current_worker_count()

        if current_count == 0:
            logger.info(f"No workers found, deploying {required_count} workers")
            self._deploy_workers(required_count)
        elif current_count < required_count:
            logger.info(f"Scaling workers from {current_count} to {required_count}")
            self._scale_workers(required_count)
        else:
            logger.info(f"Workers already exist ({current_count} >= {required_count}), reusing")

    def _get_current_worker_count(self) -> int:
        """Get the current number of worker replicas."""
        try:
            result = subprocess.run(
                ["kubectl", "get", "statefulset", self.STATEFULSET_NAME,
                 "-n", self.namespace,
                 "-o", "jsonpath={.spec.replicas}"],
                capture_output=True,
                text=True,
                check=False
            )

            if result.returncode == 0 and result.stdout.strip():
                return int(result.stdout.strip())
            return 0
        except Exception as e:
            logger.warning(f"Error checking worker count: {e}")
            return 0

    def _deploy_workers(self, count: int) -> None:
        """Deploy the worker StatefulSet and Service."""
        logger.info(f"Deploying {count} OMB workers...")

        # Generate manifests
        manifest = self._generate_worker_manifests(count)
        manifest_file = self.results_dir / "omb-workers.yaml"

        with open(manifest_file, 'w') as f:
            f.write(manifest)

        # Apply manifests
        subprocess.run(
            ["kubectl", "apply", "-f", str(manifest_file)],
            check=True
        )

        # Wait for workers to be ready
        self._wait_for_workers_ready(count)
        logger.info(f"✓ {count} workers deployed and ready")

    def _scale_workers(self, new_count: int) -> None:
        """Scale the worker StatefulSet to a new replica count."""
        logger.info(f"Scaling workers to {new_count}...")

        subprocess.run(
            ["kubectl", "scale", "statefulset", self.STATEFULSET_NAME,
             "-n", self.namespace,
             f"--replicas={new_count}"],
            check=True
        )

        # Wait for new workers to be ready
        self._wait_for_workers_ready(new_count)
        logger.info(f"✓ Workers scaled to {new_count}")

    def _wait_for_workers_ready(self, expected_count: int, timeout_seconds: int = 300) -> None:
        """Wait for all workers to reach Ready state."""
        logger.info(f"Waiting for {expected_count} workers to be ready...")
        start_time = time.time()

        while time.time() - start_time < timeout_seconds:
            result = subprocess.run(
                ["kubectl", "get", "pods", "-n", self.namespace,
                 "-l", f"app=omb-worker",
                 "-o", "json"],
                capture_output=True,
                text=True,
                check=False
            )

            if result.returncode == 0:
                pods = json.loads(result.stdout)
                pod_items = pods.get('items', [])

                if len(pod_items) == expected_count:
                    # Check if all are ready
                    ready_count = 0
                    for pod in pod_items:
                        conditions = pod.get('status', {}).get('conditions', [])
                        for condition in conditions:
                            if condition['type'] == 'Ready' and condition['status'] == 'True':
                                ready_count += 1
                                break

                    if ready_count == expected_count:
                        return
                    else:
                        logger.debug(f"Workers ready: {ready_count}/{expected_count}")
                else:
                    logger.debug(f"Workers created: {len(pod_items)}/{expected_count}")

            time.sleep(5)

        raise TimeoutError(f"Timeout waiting for {expected_count} workers to be ready")

    def cleanup_workers(self) -> None:
        """Delete the worker StatefulSet and Service."""
        logger.info("Cleaning up workers...")

        subprocess.run(
            ["kubectl", "delete", "statefulset", self.STATEFULSET_NAME,
             "-n", self.namespace],
            check=False
        )

        subprocess.run(
            ["kubectl", "delete", "service", self.SERVICE_NAME,
             "-n", self.namespace],
            check=False
        )

        logger.info("✓ Workers cleaned up")

    def get_worker_addresses(self, count: int) -> list[str]:
        """
        Get the addresses of worker pods for driver configuration.

        Args:
            count: Number of workers to include

        Returns:
            List of worker HTTP URLs
        """
        addresses = []
        for i in range(count):
            url = f"http://{self.STATEFULSET_NAME}-{i}.{self.SERVICE_NAME}.{self.namespace}.svc.cluster.local:8080"
            addresses.append(url)
        return addresses

    def _generate_worker_manifests(self, replicas: int) -> str:
        """Generate Kubernetes manifests for workers (Service + StatefulSet)."""
        return f"""apiVersion: v1
kind: Service
metadata:
  name: {self.SERVICE_NAME}
  namespace: {self.namespace}
spec:
  clusterIP: None  # Headless service for StatefulSet DNS
  selector:
    app: omb-worker
  ports:
  - name: http
    port: 8080
    targetPort: 8080
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: {self.STATEFULSET_NAME}
  namespace: {self.namespace}
  labels:
    app: omb-worker
    managed-by: pulsar-aws-lab
spec:
  serviceName: {self.SERVICE_NAME}
  replicas: {replicas}
  podManagementPolicy: Parallel  # Create all pods in parallel
  selector:
    matchLabels:
      app: omb-worker
  template:
    metadata:
      labels:
        app: omb-worker
    spec:
      containers:
      - name: worker
        image: {self.omb_image}
        imagePullPolicy: Always
        env:
        - name: HEAP_OPTS
          value: "-Xms12G -Xmx12G -XX:MaxDirectMemorySize=4G"
        command: ["/bin/bash", "-c"]
        args:
          - |
            set -x
            echo "Starting OMB worker on $(hostname)"
            echo "Worker will listen on 0.0.0.0:8080"
            echo "JVM Heap: $HEAP_OPTS"

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
        resources:
          requests:
            memory: "8Gi"
            cpu: "1000m"
          limits:
            memory: "24Gi"
            cpu: "4000m"
"""
