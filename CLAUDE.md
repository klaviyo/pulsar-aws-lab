# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Pulsar OMB Lab** is a specialized load testing framework for Apache Pulsar using the OpenMessaging Benchmark (OMB) framework. This tool runs performance tests against **existing Pulsar clusters** and generates comprehensive reports.

**CRITICAL**: This repository does NOT deploy Pulsar. You must have a running Pulsar cluster accessible at `pulsar://pulsar-broker.pulsar.svc.cluster.local:6650` before using this framework.

## What This Framework Does

1. **Builds Docker Image**: Custom OMB image with Java 21 and benchmark tooling
2. **Creates Kubernetes Jobs**: Dynamically generates ephemeral Jobs to run load tests
3. **Executes Load Tests**: Runs configurable workloads (topics, partitions, rates, message sizes)
4. **Collects Results**: Aggregates metrics from completed Job pods
5. **Generates Reports**: Creates comprehensive HTML reports with performance analysis

## What This Framework Does NOT Do

- Deploy Pulsar clusters
- Provision infrastructure (EKS, EC2, VPC)
- Install Helm charts
- Manage Pulsar configuration
- Create monitoring stacks

## Architecture

### Core Components

1. **Docker Image** (`docker/omb/`)
   - Custom OpenMessaging Benchmark Docker image
   - Multi-stage build: Maven 3.9 + Java 21 LTS
   - Builds OMB from official GitHub repository: https://github.com/openmessaging/benchmark
   - Runtime image includes only JRE for minimal footprint
   - Pre-configured with benchmark binary in PATH
   - Image must be accessible to your Kubernetes cluster

2. **Orchestrator** (`scripts/orchestrator.py`)
   - Python-based CLI for test execution
   - Creates Kubernetes Jobs dynamically from test plans
   - Monitors Job completion and collects pod logs
   - Parses OMB output for metrics extraction
   - Generates HTML reports with performance data
   - Three commands: `run`, `report`, `list`

3. **Configuration System** (`config/`)
   - Test plans: workload matrices and test scenarios
   - Workload definitions: topics, partitions, message sizes, rates, producer/consumer counts
   - No infrastructure or cluster configuration (Pulsar must exist externally)

4. **Test Plans** (`config/test-plans/*.yaml`)
   - Define test scenarios and workload variations
   - Specify which workloads to run and with what parameters
   - Support for test matrices (e.g., varying message sizes, partition counts)
   - Each test creates a separate Kubernetes Job

5. **Test Plans** (`config/test-plans/*.yaml`)
   - Define base workload parameters and test variations
   - Specify benchmark type (fixed_rate, ramp_up, etc.)
   - Configure multiple test runs with different settings
   - Orchestrator generates OMB workload specs from these plans

### How OMB Jobs Work

When you run a test, the orchestrator:

1. **Reads Test Plan**: Parses test plan YAML to determine workload variations
2. **Generates Job Manifests**: Creates Kubernetes Job YAML dynamically for each test
3. **Creates ConfigMaps**: Embeds workload configuration as ConfigMaps mounted to Job pods
4. **Submits Jobs**: Applies Job manifests to Kubernetes cluster
5. **Monitors Completion**: Polls Job status until completion (success/failure)
6. **Collects Logs**: Retrieves logs from completed Job pods
7. **Parses Results**: Extracts throughput, latency, and error metrics from OMB output
8. **Cleans Up**: Deletes completed Jobs and ConfigMaps after log collection

**Job Characteristics:**
- **Namespace**: `pulsar-omb` (created automatically)
- **Naming**: `omb-<workload-name>-<variation-id>-<timestamp>`
- **Restart Policy**: `Never` (Jobs do not restart on failure)
- **Backoff Limit**: 0 (no retries)
- **TTL**: Jobs are cleaned up after log collection
- **Service Account**: Uses default service account with minimal permissions

**Hardcoded Pulsar Connection:**
- Service URL: `pulsar://pulsar-broker.pulsar.svc.cluster.local:6650`
- HTTP URL: `http://pulsar-broker.pulsar.svc.cluster.local:8080`
- Assumes Pulsar proxy service exists in `pulsar` namespace
- No authentication configured (modify for production use)

### Workflow

```
┌─────────────────────────────────────────────────────────────┐
│ Prerequisites                                               │
│ - Pulsar cluster running in Kubernetes                     │
│ - Pulsar proxy accessible at expected service name         │
│ - kubectl configured with cluster access                   │
│ - OMB Docker image built and pushed to accessible registry │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 1: Run Tests                                           │
│ $ python scripts/orchestrator.py run \                      │
│     --test-plan config/test-plans/poc.yaml                  │
│                                                             │
│ - Creates experiment ID and result directory               │
│ - Reads test plan and workload definitions                 │
│ - Generates Kubernetes Job manifests dynamically           │
│ - Creates ConfigMaps with workload configurations          │
│ - Submits Jobs to cluster                                  │
│ - Monitors Job status (polls every 10s)                    │
│ - Collects logs from completed pods                        │
│ - Deletes Jobs and ConfigMaps after collection             │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 2: Generate Report                                     │
│ $ python scripts/orchestrator.py report --experiment-id ID │
│                                                             │
│ - Parses collected logs for metrics                        │
│ - Extracts throughput, latency percentiles, error rates    │
│ - Generates HTML report with charts                        │
│ - Saves report to experiment directory                     │
│ - Creates summary statistics                               │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 3: Analyze Results                                     │
│ - Open HTML report in browser                              │
│ - Review metrics: throughput, latency, errors              │
│ - Compare test variations                                   │
│ - Export raw data for further analysis                     │
└─────────────────────────────────────────────────────────────┘
```

### Key Design Principles

- **Non-Invasive**: Does not modify or deploy Pulsar, only runs tests against it
- **Kubernetes-Native**: Uses native Job resources for test execution
- **Ephemeral**: Jobs are created, run, and deleted automatically
- **Reproducible**: All configurations version controlled, deterministic test execution
- **Isolated**: Runs in separate namespace from Pulsar cluster
- **Lightweight**: Minimal resource footprint (Jobs only exist during tests)
- **Transparent**: All logs and metrics captured for offline analysis

## Prerequisites

### Required External Infrastructure

1. **Running Pulsar Cluster**
   - Must be deployed in Kubernetes
   - Proxy service must be accessible at: `pulsar-broker.pulsar.svc.cluster.local:6650`
   - HTTP admin API at: `pulsar-broker.pulsar.svc.cluster.local:8080`
   - Cluster must be healthy and ready to accept connections

2. **Kubernetes Cluster Access**
   - kubectl configured with appropriate context
   - Permission to create Jobs, ConfigMaps, and Namespaces
   - Sufficient cluster resources for test Jobs (CPU, memory)

3. **Docker Registry Access**
   - OMB Docker image must be built and pushed
   - Kubernetes must be able to pull the image
   - Configure image pull secrets if using private registry

### Software Requirements

```bash
# Install Python dependencies
pip install -r scripts/requirements.txt

# Install kubectl (Kubernetes CLI)
# macOS: brew install kubectl
# Linux: curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
# Windows: choco install kubernetes-cli

# Install Docker (for building OMB image)
# macOS: brew install --cask docker
# Linux: See https://docs.docker.com/engine/install/
# Windows: choco install docker-desktop

# Verify kubectl access to cluster
kubectl cluster-info
kubectl get nodes
```

## Development Commands

### Docker Image Management

The OMB Docker image must be built and accessible to your Kubernetes cluster:

```bash
# Build OpenMessaging Benchmark Docker image
cd docker/omb
docker build -t pulsar-omb:latest .

# Tag for container registry (adjust for your registry)
docker tag pulsar-omb:latest <your-registry>/pulsar-omb:latest

# Push to registry
docker push <your-registry>/pulsar-omb:latest

# For local Kubernetes (minikube/kind), load image directly
minikube image load pulsar-omb:latest
# OR for kind:
kind load docker-image pulsar-omb:latest --name <cluster-name>

# Verify image is available
docker images | grep pulsar-omb
```

**Important**: Update `config/test-plans/*.yaml` to reference your image location:
```yaml
image: <your-registry>/pulsar-omb:latest
```

### Verify Pulsar Cluster Access

Before running tests, verify your Pulsar cluster is accessible:

```bash
# Check Pulsar proxy service exists
kubectl get svc -n pulsar pulsar-broker

# Expected output should show:
# NAME           TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)
# pulsar-broker   ClusterIP   10.96.xxx.xxx   <none>        6650/TCP,8080/TCP

# Test connectivity from a pod
kubectl run curl-test --image=curlimages/curl -i --rm --restart=Never -- \
  curl -v http://pulsar-broker.pulsar.svc.cluster.local:8080/admin/v2/clusters

# Should return Pulsar cluster information (not connection refused)

# Check Pulsar health
kubectl exec -n pulsar -it <broker-pod-name> -- bin/pulsar-admin brokers healthcheck
```

If these checks fail, your Pulsar cluster is not properly configured or accessible.

### Running Tests

The orchestrator provides three commands: `run`, `report`, and `list`.

#### Run Tests

Execute load tests against your Pulsar cluster:

```bash
# Run tests using a test plan
python scripts/orchestrator.py run --test-plan config/test-plans/poc.yaml

# Specify custom experiment ID
python scripts/orchestrator.py run \
  --test-plan config/test-plans/poc.yaml \
  --experiment-id my-test-2024-01-15

# Specify custom OMB image
python scripts/orchestrator.py run \
  --test-plan config/test-plans/poc.yaml \
  --image my-registry.io/pulsar-omb:v1.2.3

# Dry run (show what would be executed)
python scripts/orchestrator.py run \
  --test-plan config/test-plans/poc.yaml \
  --dry-run
```

**What Happens:**
1. Creates experiment directory: `results/<experiment-id>/`
2. Generates Kubernetes Job manifests for each test in the plan
3. Creates ConfigMaps with workload configurations
4. Submits Jobs to `omb` namespace
5. Monitors Jobs until completion (success or failure)
6. Collects logs from all Job pods
7. Saves logs to experiment directory
8. Deletes Jobs and ConfigMaps
9. Creates symlink: `results/latest` → experiment directory

**Monitoring Progress:**
```bash
# Watch Job status
kubectl get jobs -n pulsar-omb --watch

# View Job pod logs in real-time
kubectl logs -n pulsar-omb -l job-name=omb-workload-001 -f

# Check for failed Jobs
kubectl get jobs -n pulsar-omb --field-selector status.successful=0
```

#### Generate Report

After tests complete, generate an HTML report:

```bash
# Generate report for latest experiment
python scripts/orchestrator.py report

# Generate report for specific experiment
python scripts/orchestrator.py report --experiment-id my-test-2024-01-15

# Specify output format
python scripts/orchestrator.py report --format html
python scripts/orchestrator.py report --format json
```

**Report Contents:**
- **Summary Statistics**: Total throughput, average latency, error rates
- **Per-Test Metrics**: Throughput (msgs/sec, MB/sec), latency percentiles (p50, p95, p99, p99.9, max)
- **Charts**: Throughput over time, latency distributions, test comparisons
- **Raw Data**: Complete logs and parsed metrics for further analysis
- **Test Configurations**: Workload definitions and test plan parameters

**Report Location:**
- HTML report: `results/<experiment-id>/test_report.html`
- Raw data: `results/<experiment-id>/benchmark_results/`
- Logs: `results/<experiment-id>/orchestrator.log`

#### List Experiments

View all experiments and their status:

```bash
# List all experiments
python scripts/orchestrator.py list

# Show detailed information
python scripts/orchestrator.py list --verbose

# Filter by date
python scripts/orchestrator.py list --since 2024-01-01
```

### kubectl Operations for OMB Jobs

Useful commands for debugging and monitoring:

```bash
# List all OMB Jobs
kubectl get jobs -n pulsar-omb

# Watch Job status in real-time
kubectl get jobs -n pulsar-omb --watch

# View Job details
kubectl describe job <job-name> -n pulsar-omb

# Check Job pod logs
kubectl logs -n pulsar-omb -l job-name=<job-name>

# Get logs from failed pods
kubectl logs -n pulsar-omb <pod-name> --previous

# View ConfigMaps created for tests
kubectl get configmaps -n pulsar-omb

# View ConfigMap contents
kubectl describe configmap <configmap-name> -n pulsar-omb

# Check Job events (helpful for debugging)
kubectl get events -n pulsar-omb --sort-by='.lastTimestamp'

# Delete stuck Jobs manually
kubectl delete job <job-name> -n pulsar-omb

# Clean up entire namespace
kubectl delete namespace pulsar-omb
```

## Configuration

### Test Plans

Test plans define which workloads to run and how to vary parameters.

**Location**: `config/test-plans/*.yaml`

**Structure**:
```yaml
name: "POC Test Plan"
description: "Proof of concept load test"

# OMB Docker image to use for Jobs
image: pulsar-omb:latest

# List of workloads to execute
workloads:
  - name: "simple-produce-consume"
    file: "workloads/simple.yaml"
    variations:
      - name: "1kb-messages"
        parameters:
          messageSize: 1024
      - name: "10kb-messages"
        parameters:
          messageSize: 10240

  - name: "high-throughput"
    file: "workloads/max-throughput.yaml"
    variations:
      - name: "baseline"
        parameters: {}

# Job resource requests/limits (optional)
resources:
  requests:
    cpu: "1000m"
    memory: "2Gi"
  limits:
    cpu: "2000m"
    memory: "4Gi"

# Job timeout (optional, default: 3600s)
timeout: 1800
```

### Workload Definitions

Workload files define the actual test scenarios in OpenMessaging Benchmark format.

**Location**: `workloads/*.yaml`

**Structure** (standard OMB format):
```yaml
name: "Simple Producer-Consumer Test"

# Pulsar-specific driver configuration (DO NOT MODIFY serviceUrl)
driverConfig:
  name: "Pulsar"
  serviceUrl: "pulsar://pulsar-broker.pulsar.svc.cluster.local:6650"
  httpUrl: "http://pulsar-broker.pulsar.svc.cluster.local:8080"

# Topic and subscription configuration
topics:
  - name: "test-topic"
    partitions: 10

subscriptions:
  - name: "test-subscription"
    subscriptionType: "Shared"

# Producer configuration
producers:
  - name: "producer-1"
    rate: 10000  # messages per second
    messageSize: 1024  # bytes

# Consumer configuration
consumers:
  - name: "consumer-1"
    subscriptions:
      - "test-subscription"

# Test duration
testDuration: 300  # seconds
warmupDuration: 60  # seconds
```

**Key Parameters**:
- `topics`: Topic names and partition counts
- `subscriptions`: Subscription names and types (Shared, Exclusive, Failover)
- `producers.rate`: Messages per second (0 = max throughput)
- `producers.messageSize`: Message payload size in bytes
- `testDuration`: How long to run the test
- `warmupDuration`: Time to stabilize before collecting metrics

**Parameter Overrides**: Test plan variations can override workload parameters dynamically.

## Test Results

### Result Directory Structure

Each experiment creates a directory at `results/<experiment-id>/`:

```
results/<experiment-id>/
├── orchestrator.log           # Orchestrator execution log
├── test_report.html           # Generated HTML report with Grafana links
├── benchmark_results/
│   ├── poc-20k.log            # Test results from OMB driver
│   └── ...
├── workload_poc-20k.yaml      # Generated workload ConfigMap
├── omb_workers_poc-20k.yaml   # Generated workers StatefulSet
└── omb_job_poc-20k.yaml       # Generated driver Job manifest
```

### Metrics Collected

For each test, the following metrics are extracted:

**Throughput**:
- Messages per second (msgs/sec)
- Megabytes per second (MB/sec)
- Publish rate, Consume rate

**Latency** (percentiles):
- p50 (median)
- p95
- p99
- p99.9
- max

**Errors**:
- Publish errors
- Consume errors
- Connection failures
- Timeout events

**Resource Utilization** (if available):
- CPU usage
- Memory usage
- Network throughput

## Troubleshooting

### Common Issues

#### Pulsar Connection Failures

**Symptoms**: Jobs fail with "Connection refused" or "Failed to create Pulsar client"

**Diagnosis**:
```bash
# Check Pulsar proxy service exists
kubectl get svc -n pulsar pulsar-broker

# Check service endpoints
kubectl get endpoints -n pulsar pulsar-broker

# Test connectivity from test namespace
kubectl run -n pulsar-omb curl-test --image=curlimages/curl -i --rm --restart=Never -- \
  curl -v http://pulsar-broker.pulsar.svc.cluster.local:8080/admin/v2/clusters
```

**Solutions**:
- Verify Pulsar cluster is running: `kubectl get pods -n pulsar`
- Verify proxy service exists and has endpoints
- Check network policies allow traffic from `pulsar-omb` namespace
- Verify service name matches hardcoded value in workload configs

#### Job ImagePullBackOff

**Symptoms**: Job pods stuck in `ImagePullBackOff` state

**Diagnosis**:
```bash
# Check pod events
kubectl describe pod -n pulsar-omb <pod-name>

# Verify image exists
docker images | grep pulsar-omb
```

**Solutions**:
- Build and push OMB Docker image: `cd docker/omb && docker build -t pulsar-omb:latest .`
- For local clusters, load image: `minikube image load pulsar-omb:latest`
- Update test plan with correct image reference
- Configure imagePullSecrets if using private registry:
  ```bash
  kubectl create secret docker-registry regcred \
    --docker-server=<registry> \
    --docker-username=<username> \
    --docker-password=<password> \
    -n pulsar-omb
  ```

#### Job Timeout or Hangs

**Symptoms**: Jobs run indefinitely or timeout without completing

**Diagnosis**:
```bash
# Check Job pod status
kubectl get pods -n pulsar-omb -l job-name=<job-name>

# View live logs
kubectl logs -n pulsar-omb -l job-name=<job-name> -f

# Check for resource constraints
kubectl top pods -n pulsar-omb
kubectl describe pod -n pulsar-omb <pod-name>
```

**Solutions**:
- Verify Pulsar cluster is healthy and not overloaded
- Check Job resource requests/limits in test plan
- Reduce test duration or workload intensity
- Check for network issues between OMB and Pulsar
- Verify test plan timeout is sufficient

#### Jobs Fail with OMB Errors

**Symptoms**: Jobs complete but logs show OMB errors or failures

**Diagnosis**:
```bash
# View Job logs
kubectl logs -n pulsar-omb <pod-name>

# Common errors to look for:
# - "Topic not found" → Topic creation failed
# - "Partition error" → Invalid partition count
# - "Authorization failed" → Missing permissions
# - "Service not ready" → Pulsar cluster unhealthy
```

**Solutions**:
- Check Pulsar broker logs for errors
- Verify topic auto-creation is enabled or pre-create topics
- Check authentication/authorization if enabled
- Reduce workload intensity (lower rate, fewer partitions)
- Verify workload configuration syntax

#### No Results After Test Completion

**Symptoms**: Tests complete but report generation fails

**Diagnosis**:
```bash
# Check experiment directory
ls -la results/<experiment-id>/

# Check for benchmark results
ls -la results/<experiment-id>/benchmark_results/

# View orchestrator logs
tail -f results/<experiment-id>/orchestrator.log
```

**Solutions**:
- Check orchestrator logs: `results/<experiment-id>/orchestrator.log`
- View HTML report: `results/<experiment-id>/test_report.html`
- Check benchmark results: `results/<experiment-id>/benchmark_results/`
- Check for empty log files or collection failures

### Stuck Jobs Cleanup

If Jobs get stuck or need manual cleanup:

```bash
# Delete all Jobs in OMB namespace
kubectl delete jobs -n omb --all

# Delete all ConfigMaps
kubectl delete configmaps -n omb --all

# Force delete stuck pods
kubectl delete pods -n omb --all --grace-period=0 --force

# Nuclear option: delete entire namespace
kubectl delete namespace omb

# Recreate namespace for next run
kubectl create namespace omb
```

### Debugging Job Execution

For detailed debugging of a specific Job:

```bash
# Get Job status
kubectl get job <job-name> -n pulsar-omb -o yaml

# Check Job events
kubectl describe job <job-name> -n pulsar-omb

# Get pod name for Job
POD_NAME=$(kubectl get pods -n pulsar-omb -l job-name=<job-name> -o jsonpath='{.items[0].metadata.name}')

# View pod logs
kubectl logs -n pulsar-omb $POD_NAME

# Exec into pod (if still running)
kubectl exec -it -n pulsar-omb $POD_NAME -- bash

# Inside pod, check OMB installation
ls -la /opt/benchmark/
java -version
```

## Important Files and Locations

### Project Structure

```
pulsar-aws-lab/
├── docker/
│   └── omb/                          # OMB Docker image
│       ├── Dockerfile
│       └── entrypoint.sh
├── scripts/
│   ├── orchestrator.py               # Main CLI
│   └── requirements.txt              # Python dependencies
├── config/
│   └── test-plans/                   # Test plan definitions
│       ├── poc.yaml
│       └── performance.yaml
├── workloads/                        # OMB workload specs
│   ├── simple.yaml
│   ├── max-throughput.yaml
│   └── latency-sensitive.yaml
└── CLAUDE.md                         # This file
```

### Runtime Locations

- **Experiment results**: `results/<experiment-id>/`
- **Latest experiment symlink**: `results/latest`
- **Orchestrator logs**: `results/<experiment-id>/orchestrator.log`
- **Test results**: `results/<experiment-id>/benchmark_results/`
- **Generated reports**: `results/<experiment-id>/test_report.html`
- **Job manifests**: `results/<experiment-id>/*.yaml`

### Kubernetes Resources

- **Namespace**: `omb` (created automatically)
- **StatefulSets**: `omb-workers-<test-name>` (3 worker pods by default)
- **Jobs**: `omb-<test-name>` (single driver pod)
- **ConfigMaps**: `omb-workload-<test-name>`
- **Services**: `omb-workers-<test-name>` (headless service for worker discovery)
- **Service Account**: `default` (no special permissions required)

## Development Guidelines

When working with this codebase:

1. **OMB uses driver/worker architecture** - Driver Job + Worker StatefulSet for distributed load
2. **Pulsar is external** - Never include Pulsar deployment code
3. **Config is immutable** - Each test creates new ConfigMaps, doesn't modify existing
4. **Results are local** - Store results in project `results/` directory, not in home directory
5. **Cleanup is automatic** - Jobs/StatefulSets deleted after test completion
6. **Hardcoded connection** - Pulsar URL is fixed in orchestrator constants

## Adding New Features

### Adding a New Workload
1. Create YAML file in `workloads/` directory
2. Define topics, partitions, message sizes, producer/consumer settings
3. Reference in test plan YAML

### Adding a New Test Type
1. Update test plan schema if needed
2. Add variation logic to `_generate_workload()` in orchestrator
3. Document in test plan examples

### Modifying OMB Image
1. Update `docker/omb/Dockerfile`
2. Rebuild and push to registry
3. Update image reference in test plans
