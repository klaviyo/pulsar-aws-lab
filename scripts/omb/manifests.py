"""
Kubernetes manifest generation for OMB jobs and configmaps.
"""

from typing import Dict, List, Tuple

import yaml


def indent_yaml(content: str, spaces: int) -> str:
    """
    Indent YAML content for embedding in ConfigMap.

    Args:
        content: YAML content string
        spaces: Number of spaces to indent

    Returns:
        Indented YAML content
    """
    indent = ' ' * spaces
    lines = content.split('\n')
    return '\n'.join(indent + line if line else line for line in lines)


class ManifestBuilder:
    """
    Builds Kubernetes manifest YAML for OMB workloads and jobs.
    """

    def __init__(
        self,
        namespace: str,
        pulsar_service_url: str,
        pulsar_http_url: str,
        pulsar_tenant_namespace: str,
        omb_image: str,
        experiment_id: str,
        worker_manager
    ):
        """
        Initialize manifest builder.

        Args:
            namespace: Kubernetes namespace for OMB resources
            pulsar_service_url: Pulsar binary protocol URL
            pulsar_http_url: Pulsar HTTP admin URL
            pulsar_tenant_namespace: Pulsar tenant/namespace prefix for test topics
            omb_image: Docker image for OMB
            experiment_id: Unique experiment identifier
            worker_manager: WorkerManager instance for getting worker addresses
        """
        self.namespace = namespace
        self.pulsar_service_url = pulsar_service_url
        self.pulsar_http_url = pulsar_http_url
        self.pulsar_tenant_namespace = pulsar_tenant_namespace
        self.omb_image = omb_image
        self.experiment_id = experiment_id
        self.worker_manager = worker_manager

    def build_workload_configmap(self, test_name: str, workload: Dict) -> str:
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
      batchingMaxPublishDelayMs: 5
      blockIfQueueFull: true
      pendingQueueSize: 50000
    consumer:
      subscriptionType: Shared
"""

    def build_driver_job(self, test_name: str, num_workers: int = 3) -> str:
        """Generate Kubernetes Job YAML for OMB driver"""
        # Get worker addresses from persistent worker pool
        worker_addresses = self.worker_manager.get_worker_addresses(num_workers)
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
      nodeSelector:
        klaviyo.com/pool-name: loadgen
      tolerations:
      - key: "loadgen"
        operator: "Equal"
        value: "true"
        effect: "NoSchedule"
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
            nslookup pulsar-broker.pulsar.svc.cluster.local || echo "DNS lookup failed"
            echo ""

            echo "===== Network Connectivity ====="
            echo "Testing binary protocol port (6650)..."
            timeout 5 nc -zv pulsar-broker.pulsar.svc.cluster.local 6650 || echo "Port 6650 not reachable"
            echo "Testing HTTP port (8080)..."
            timeout 5 nc -zv pulsar-broker.pulsar.svc.cluster.local 8080 || echo "Port 8080 not reachable"
            echo ""

            echo "===== HTTP Endpoint Tests ====="
            echo "Testing /admin/v2/brokers/health..."
            curl -v -m 10 http://pulsar-broker.pulsar.svc.cluster.local:8080/admin/v2/brokers/health || echo "Health check failed"
            echo ""
            echo "Testing /admin/v2/namespaces/public/default..."
            curl -v -m 10 http://pulsar-broker.pulsar.svc.cluster.local:8080/admin/v2/namespaces/public/default || echo "Namespace check failed"
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

            # Create experiment-specific directory
            mkdir -p /results/{self.experiment_id}

            echo "===== Starting OMB Benchmark (Driver Mode) ====="
            /app/bin/benchmark \\
              --drivers /workload/driver.yaml \\
              --workers {workers_list} \\
              --output /results/{self.experiment_id}/{test_name}.json \\
              /workload/workload.yaml

            EXIT_CODE=$?
            echo ""
            echo "===== Benchmark Exit Code: $EXIT_CODE ====="
            if [ $EXIT_CODE -eq 0 ]; then
              echo "Results saved to /results/{self.experiment_id}/{test_name}.json"
              cat /results/{self.experiment_id}/{test_name}.json

              # Sleep to keep pod alive for results collection
              echo "Sleeping 60 seconds to allow results collection..."
              sleep 60
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

    def build_batch_configmap(
        self,
        batch_name: str,
        workloads: List[Tuple[str, Dict, int]]
    ) -> str:
        """
        Generate Kubernetes ConfigMap YAML containing all batch workloads.

        ConfigMap structure:
          - driver.yaml: Pulsar driver configuration
          - stages.txt: List of stage_id,target_rate pairs
          - workload-{stage_id}.yaml: Workload for each stage

        Args:
            batch_name: Name for this batch run
            workloads: List of (stage_id, workload_dict, target_rate) tuples

        Returns:
            ConfigMap YAML string
        """
        # Build stages.txt content
        stages_content = "\n".join(
            f"{stage_id},{target_rate}"
            for stage_id, _, target_rate in workloads
        )

        # Build driver.yaml content
        driver_content = f"""name: Pulsar
driverClass: io.openmessaging.benchmark.driver.pulsar.PulsarBenchmarkDriver
client:
  serviceUrl: {self.pulsar_service_url}
  httpUrl: {self.pulsar_http_url}
  namespacePrefix: {self.pulsar_tenant_namespace}
producer:
  batchingEnabled: true
  batchingMaxPublishDelayMs: 5
  blockIfQueueFull: true
  pendingQueueSize: 50000
consumer:
  subscriptionType: Shared"""

        # Start ConfigMap
        cm_yaml = f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: omb-batch-{batch_name}
  namespace: {self.namespace}
data:
  driver.yaml: |
{indent_yaml(driver_content, 4)}
  stages.txt: |
{indent_yaml(stages_content, 4)}
"""

        # Add each workload
        for stage_id, workload_dict, _ in workloads:
            workload_content = yaml.dump(workload_dict, default_flow_style=False)
            cm_yaml += f"""  workload-{stage_id}.yaml: |
{indent_yaml(workload_content, 4)}
"""

        return cm_yaml

    def build_batch_job(
        self,
        batch_name: str,
        num_workers: int,
        bash_script: str
    ) -> str:
        """
        Generate Kubernetes Job YAML for batch mode execution.

        Args:
            batch_name: Name for this batch run
            num_workers: Number of workers to use
            bash_script: Bash script to execute in the container

        Returns:
            Job YAML string
        """
        return f"""apiVersion: batch/v1
kind: Job
metadata:
  name: omb-batch-{batch_name}
  namespace: {self.namespace}
  labels:
    app: omb-driver
    mode: batch
    test: {batch_name}
spec:
  backoffLimit: 0
  template:
    metadata:
      labels:
        app: omb-driver
        mode: batch
        test: {batch_name}
    spec:
      restartPolicy: Never
      nodeSelector:
        klaviyo.com/pool-name: loadgen
      tolerations:
      - key: "loadgen"
        operator: "Equal"
        value: "true"
        effect: "NoSchedule"
      containers:
      - name: omb-batch
        image: {self.omb_image}
        imagePullPolicy: Always
        command: ["/bin/bash", "-c"]
        args:
          - |
{indent_yaml(bash_script, 12)}
        volumeMounts:
        - name: workload
          mountPath: /workload
        - name: results
          mountPath: /results
      volumes:
      - name: workload
        configMap:
          name: omb-batch-{batch_name}
      - name: results
        emptyDir: {{}}
"""
