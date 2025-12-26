# Pulsar OMB Load Testing Framework

A systematic Apache Pulsar load testing framework using OpenMessaging Benchmark (OMB) with automated test execution, comprehensive reporting, and cost tracking.

**What it does:** Run performance benchmarks against your Pulsar cluster
**What it doesn't do:** Deploy or manage Pulsar infrastructure (your cluster must already exist)

> **⚠️ IMPORTANT:** This framework assumes you have a **running Pulsar cluster** accessible via kubectl. For infrastructure setup, deployment guides, and troubleshooting, see [CLAUDE.md](CLAUDE.md).

## Quick Start

### Prerequisites

Your environment needs:
- **Running Pulsar cluster** (EKS, self-hosted, etc.) accessible via kubectl
- kubectl configured and connected to your cluster
- Python 3.8+
- AWS credentials (for cost tracking only)

```bash
# Verify cluster access
kubectl get pods -n pulsar

# Install Python dependencies
pip install -r scripts/requirements.txt
```

### Run Your First Test

```bash
# Quick proof-of-concept test (20k msgs/sec, 2 minutes)
python scripts/orchestrator.py run \
  --test-plan config/test-plans/poc.yaml

# View results
python scripts/orchestrator.py list
python scripts/orchestrator.py report --experiment-id latest
```

Results are saved to `results/latest/` with detailed metrics, latency percentiles, and HTML reports with Grafana links.

## Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/klaviyo/pulsar-aws-lab.git
   cd pulsar-aws-lab
   ```

2. **Install dependencies:**
   ```bash
   pip install -r scripts/requirements.txt
   ```

3. **Configure kubectl:**
   ```bash
   # For EKS clusters
   aws eks update-kubeconfig --region <region> --name <cluster-name>

   # Verify connection
   kubectl cluster-info
   kubectl get pods -n pulsar
   ```

4. **Configure AWS credentials** (optional, for cost tracking):
   ```bash
   aws configure
   # Or export environment variables
   export AWS_ACCESS_KEY_ID=your_access_key
   export AWS_SECRET_ACCESS_KEY=your_secret_key
   export AWS_DEFAULT_REGION=us-east-1
   ```

## Usage

The orchestrator provides three main commands:

### 1. Run Tests

Execute a test plan against your Pulsar cluster:

```bash
# Run proof-of-concept test (2 minutes, 20k msgs/sec)
python scripts/orchestrator.py run \
  --test-plan config/test-plans/poc.yaml

# Run baseline tests (5 test variations, ~15 minutes total)
python scripts/orchestrator.py run \
  --test-plan config/test-plans/baseline.yaml

# Custom experiment ID for organization
python scripts/orchestrator.py run \
  --test-plan config/test-plans/my-tests.yaml \
  --experiment-id latency-tuning-v3
```

**What happens during a test run:**
1. Validates Pulsar cluster is accessible
2. Creates OMB producer/consumer Jobs in Kubernetes
3. Runs each test variation sequentially
4. Collects metrics: throughput, latency (p50/p95/p99/p99.9/max), errors
5. Saves results to `results/<experiment-id>/`

### 2. Generate Reports

Create comprehensive HTML reports with charts and analysis:

```bash
# Report for latest experiment
python scripts/orchestrator.py report --experiment-id latest

# Report for specific experiment
python scripts/orchestrator.py report --experiment-id my-experiment-123
```

Reports include:
- Throughput trends (msgs/sec, MB/sec)
- Latency percentiles and distribution
- Cost breakdown per test variation
- Configuration details and metadata
- Raw data files for further analysis

### 3. List Experiments

View all experiments and their results:

```bash
python scripts/orchestrator.py list
```

Output shows experiment IDs, timestamps, test plans used, and result locations.

## Configuration

### Test Plans

Test plans define what benchmarks to run. Examples in `config/test-plans/`:

**`poc.yaml`** - Quick validation (2 minutes)
```yaml
name: "proof-of-concept"
description: "Quick test to validate cluster"

base_workload:
  topics: 1
  partitions_per_topic: 8
  message_size: 1024
  test_duration_minutes: 2

test_runs:
  - name: "baseline"
    type: "fixed_rate"
    producer_rate: 20000  # 20k msgs/sec
```

**`baseline.yaml`** - Systematic exploration (5 tests, ~15 minutes)
```yaml
name: "baseline"
description: "Standard performance baseline"

variations:
  - message_size: [256, 1024, 4096]
  - producer_rate: [10000, 50000]
  - partitions_per_topic: [8, 16]
```

**Create custom test plans:**
```yaml
name: "my-custom-tests"
description: "Explore high-throughput scenarios"

base_workload:
  topics: 1
  partitions_per_topic: 16
  message_size: 1024
  producers_per_topic: 4
  consumers_per_topic: 4
  test_duration_minutes: 5

test_runs:
  - name: "moderate-load"
    type: "fixed_rate"
    producer_rate: 50000

  - name: "high-load"
    type: "fixed_rate"
    producer_rate: 100000

  - name: "max-throughput"
    type: "ramp_up"
    initial_rate: 50000
    rate_increment: 10000
    increment_interval_minutes: 1
```

Test types:
- `fixed_rate`: Constant throughput test
- `ramp_up`: Gradually increase load to find limits
- `scale_to_failure`: Push until cluster saturates (future)
- `latency_sensitivity`: Measure latency under varying loads (future)

### Workload Definitions

Workload parameters are defined within test plans. The `base_workload` section sets defaults, and individual test runs can override specific values:

```yaml
# config/test-plans/custom.yaml
base_workload:
  name: "custom-baseline"
  topics: 10
  partitions_per_topic: 16
  message_size: 2048
  subscriptions_per_topic: 1
  consumers_per_topic: 4
  producers_per_topic: 4
  consumer_backlog_size_gb: 0
  test_duration_minutes: 10
  warmup_duration_minutes: 1

test_runs:
  - name: "high-load"
    type: "fixed_rate"
    producer_rate: 50000
```

## Results

All experiment data is saved to `results/<experiment-id>/`:

```
results/exp-20241013-120000/
├── test_report.html               # Interactive HTML report with Grafana links
├── benchmark_results/
│   ├── poc-20k.log                # OMB test results
│   └── ...
├── workload_poc-20k.yaml          # Generated workload ConfigMap
├── omb_workers_poc-20k.yaml       # Generated workers StatefulSet
├── omb_job_poc-20k.yaml           # Generated driver Job
└── orchestrator.log               # Execution log
```

**Quick access to latest results:**
```bash
# Latest experiment is symlinked
cd results/latest

# Open report in browser
open test_report.html  # macOS
xdg-open test_report.html  # Linux
```

## Common Workflows

### Test a Configuration Change

```bash
# Baseline before changes
python scripts/orchestrator.py run \
  --test-plan config/test-plans/baseline.yaml \
  --experiment-id before-tuning

# Make config changes to your Pulsar cluster
# (e.g., update JVM settings, replication factor, etc.)

# Test after changes
python scripts/orchestrator.py run \
  --test-plan config/test-plans/baseline.yaml \
  --experiment-id after-tuning

# Compare reports
python scripts/orchestrator.py report --experiment-id before-tuning
python scripts/orchestrator.py report --experiment-id after-tuning
```

### Find Maximum Throughput

```bash
# Use ramp-up test to find saturation point
python scripts/orchestrator.py run \
  --test-plan config/test-plans/max-throughput.yaml
```

### Test Different Message Sizes

```bash
# Create test plan with message size variations
cat > config/test-plans/message-sizes.yaml <<EOF
name: "message-size-exploration"
variations:
  message_size: [128, 512, 1024, 4096, 16384]
  producer_rate: [20000]
EOF

python scripts/orchestrator.py run \
  --test-plan config/test-plans/message-sizes.yaml
```

## Environment Variables

Optional configuration via environment variables:

```bash
# Custom results directory
export PULSAR_LAB_HOME="/data/benchmarks"

# Kubernetes namespace (default: pulsar)
export PULSAR_NAMESPACE="my-pulsar-namespace"

# AWS region for cost tracking
export AWS_DEFAULT_REGION="us-west-2"

# Experiment tags for cost allocation
export EXPERIMENT_TAGS="team=data-platform,owner=jane.doe"
```

## Technical Details & Troubleshooting

For comprehensive documentation, see [CLAUDE.md](CLAUDE.md):

- **Architecture**: How OMB runs in Kubernetes
- **Infrastructure Setup**: Deploying Pulsar on EKS (if needed)
- **Development**: Building custom OMB Docker images
- **Troubleshooting**: Common issues and debugging
- **Advanced Usage**: Helm operations, custom configurations

Quick troubleshooting commands:

```bash
# Check OMB Job status
kubectl get jobs -n pulsar

# View OMB logs
kubectl logs -n pulsar job/omb-<test-name>

# Verify Pulsar broker connectivity
kubectl exec -n pulsar pulsar-broker-0 -- bin/pulsar-admin brokers list pulsar-cluster

# Check cluster health
kubectl get pods -n pulsar
```

## Resources

### Documentation
- [CLAUDE.md](CLAUDE.md) - Complete technical documentation
- [Test Plans](config/test-plans/) - Example test configurations and workload definitions

### External Resources
- [Apache Pulsar Documentation](https://pulsar.apache.org/docs/)
- [OpenMessaging Benchmark](https://openmessaging.cloud/docs/benchmarks/)
- [Pulsar Performance Tuning](https://pulsar.apache.org/docs/performance-pulsar-perf/)

## Contributing

Contributions welcome! Please:
1. Check existing Jira tickets in the PLAT project or create a new one
2. Create a feature branch
3. Submit a pull request with tests

## License

MIT License - see LICENSE file

## Support

- **Issues**: Jira (PLAT project)
- **Pulsar Community**: [Slack](https://pulsar.apache.org/community/) | [Mailing List](mailto:users@pulsar.apache.org)
