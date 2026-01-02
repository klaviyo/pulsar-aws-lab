# Running Load Tests

This guide covers running OMB load tests against a Pulsar cluster.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│ Load Testing Framework                                               │
│                                                                      │
│  ┌──────────────┐    ┌───────────────┐    ┌────────────────────┐   │
│  │ orchestrator │───▶│ K8s Jobs/Pods │───▶│ Pulsar Cluster     │   │
│  │   (Python)   │    │  (OMB Workers)│    │ (External)         │   │
│  └──────────────┘    └───────────────┘    └────────────────────┘   │
│         │                    │                                       │
│         ▼                    ▼                                       │
│  ┌──────────────┐    ┌───────────────┐                              │
│  │ Test Config  │    │ Benchmark     │                              │
│  │ (YAML)       │    │ Results (JSON)│                              │
│  └──────────────┘    └───────────────┘                              │
└─────────────────────────────────────────────────────────────────────┘
```

The orchestrator creates Kubernetes Jobs that run OMB worker pods. Workers connect directly to the Pulsar cluster (`pulsar-broker.pulsar.svc.cluster.local:6650`) and execute the defined workloads. Results are collected and transformed into HTML reports.

## Environment Setup

### Prerequisites

- Nix package manager with flakes enabled
- [direnv](https://direnv.net/) installed and hooked into your shell
- AWS credentials for TeamSRE (or really any) account
- Access to the target EKS cluster

### 1. Enter Development Environment

```bash
cd pulsar-aws-lab
direnv allow  # First time only
```

direnv automatically loads the nix environment when you enter the directory. This provides: Python 3.13 with dependencies, kubectl, helm, awscli, and other tooling.

### 2. Authenticate to AWS

Select the appropriate role (TeamSRE-dev if you want to run against Typhon dev)
```bash
s2a-login --session-duration=28800
```

### 3. Configure kubectl Context

```bash
# List available clusters
aws eks list-clusters --region us-east-1

# Set context to target cluster (typhon in our case)
aws eks update-kubeconfig \
  --region us-east-1 \
  --name typhon

# Verify connectivity
kubectl get nodes
kubectl get pods -n pulsar
```

## Test Configuration

Test plans are defined in `config/test-plans/*.yaml`.

### Schema Reference

```yaml
# Test plan metadata
name: "test-name"                    # Unique identifier
description: "Test description"       # Human-readable description

# Optional: Auto-stop when throughput plateaus
plateau_detection:
  enabled: true                       # Enable/disable detection
  allowed_deviation: 10.0             # Max % deviation from target before flagging
  consecutive_fails_allowed: 2       # Steps below threshold before stopping

# Base workload parameters (defaults for all test runs)
base_workload:
  name: "workload-name"
  topics: 1                           # Number of topics to create
  partitions_per_topic: 16            # Partitions per topic
  message_size: 1024                  # Message payload size in bytes
  subscriptions_per_topic: 1          # Subscriptions per topic
  producers_per_topic: 1              # Producer count per topic
  consumers_per_topic: 1              # Consumer count per topic
  consumer_backlog_size_gb: 0         # Pre-populate backlog (0 = none)
  test_duration_minutes: 5            # Duration of measurement phase
  warmup_duration_minutes: 1          # Warmup before measurement

# Individual test stages
test_runs:
  - name: "stage-name"                # Unique stage identifier
    description: "Stage description"
    type: "fixed_rate"                # "fixed_rate" or "max_rate"
    producer_rate: 100000             # Target msgs/sec (fixed_rate only)
    num_workers: 5                    # OMB worker pods for load generation

    # Optional: Override base_workload values for this stage
    workload_overrides:
      topics: 10
      partitions_per_topic: 8
      message_size: 512
      test_duration_minutes: 3

# Report configuration
reporting:
  output_format: ["html", "json"]
  include_raw_data: true
  metrics_to_highlight:
    - "throughput"
    - "p99_latency"
```

### Test Types

| Type | Description |
|------|-------------|
| `fixed_rate` | Producers target a specific msgs/sec rate |
| `max_rate` | Producers send at maximum possible rate (saturation test) |

### Example: Ramping Throughput Test

```yaml
name: "ramp-100k"
description: "Ramp from 100k to 500k msgs/sec"

plateau_detection:
  enabled: true
  allowed_deviation: 10.0
  consecutive_fails_allowed: 2

base_workload:
  name: "ramp-test"
  topics: 100
  partitions_per_topic: 8
  message_size: 512
  test_duration_minutes: 2
  warmup_duration_minutes: 1

test_runs:
  - name: "rate-100k"
    type: "fixed_rate"
    producer_rate: 100000
    num_workers: 5

  - name: "rate-200k"
    type: "fixed_rate"
    producer_rate: 200000
    num_workers: 5

  - name: "rate-300k"
    type: "fixed_rate"
    producer_rate: 300000
    num_workers: 5
```

## Running Tests

### Execute a Test Plan

```bash
cd scripts
python orchestrator.py run --test-plan ../config/test-plans/poc.yaml
```

Optional flags:
- `--experiment-id <id>`: Custom experiment ID (default: auto-generated timestamp)

### Monitor Progress

The orchestrator displays a live TUI showing:
- Current test stage and progress
- Worker pod status
- Detected Pulsar namespace
- Current throughput rates

### Generate Report for Existing Experiment

```bash
python orchestrator.py report --experiment-id latest
# or
python orchestrator.py report --experiment-id exp-20260102-083340
```

### List All Experiments

```bash
python orchestrator.py list
```

### Cleanup Commands

```bash
# Delete persistent worker pods
python orchestrator.py cleanup-workers --namespace omb

# Delete Pulsar test namespaces
python orchestrator.py cleanup-pulsar --pattern "omb-test-*" --dry-run
python orchestrator.py cleanup-pulsar --pattern "omb-test-*"
```

## Results Structure

Each experiment creates a directory under `results/<experiment-id>/`:

```
results/exp-20260102-083340/
├── orchestrator.log           # Full execution log
├── overview.md                # Quick summary with metrics table
├── benchmark_results/         # Raw OMB output per test stage
│   ├── 001-rate-100k.json
│   ├── 001-rate-100k_workload.json
│   ├── 002-rate-200k.json
│   └── ...
├── metrics/                   # Infrastructure metrics during test
│   └── plot_data.json
├── report/                    # Generated report package
│   ├── index.html             # Full HTML report with charts
│   ├── metrics.csv            # Metrics export
│   ├── metrics.json           # Metrics export
│   └── charts/                # Interactive Plotly charts
│       ├── 001-rate-100k - Throughput.html
│       ├── 001-rate-100k - Latency.html
│       └── ...
├── batch_configmap_*.yaml     # Generated K8s manifests
└── batch_job_*.yaml
```

### Results Symlink

`results/latest` always points to the most recent experiment directory.

### Key Metrics in Reports

| Metric | Description |
|--------|-------------|
| `publish_rate` | Average messages/sec sent by producers |
| `consume_rate` | Average messages/sec received by consumers |
| `p50/p95/p99/p999` | Publish latency percentiles (ms) |
| `max_latency` | Maximum observed publish latency |

### Overview File

`overview.md` provides a quick results summary:

```markdown
| Phase | Target Rate | Achieved Rate | Deviation | Status |
|-------|-------------|---------------|-----------|--------|
| 001-rate-100k | 100,000 | 99,850 | -0.2% | OK |
| 002-rate-200k | 200,000 | 198,500 | -0.8% | OK |
| 003-rate-300k | 300,000 | 285,000 | -5.0% | WARN |
```

