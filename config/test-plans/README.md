# Test Plans

Test plans define the workload configurations and test scenarios for OMB load testing.

## Available Test Plans

### Quick Tests (< 5 minutes)

**poc.yaml** - Proof of concept validation
- **Duration**: 2 minutes
- **Load**: 20k msgs/sec
- **Use case**: Quick validation that everything works
- **Run**: `python scripts/orchestrator.py run --test-plan config/test-plans/poc.yaml`

**simple.yaml** - Basic moderate load
- **Duration**: 5 minutes
- **Load**: 10k msgs/sec
- **Topics**: 1 (16 partitions)
- **Use case**: Baseline testing, configuration validation

**latency.yaml** - Latency characterization
- **Duration**: 5 minutes
- **Load**: 1k msgs/sec (low load)
- **Topics**: 1 (1 partition)
- **Use case**: Measure minimum latency, p99/p99.9 latency metrics

### Performance Tests (5-10 minutes)

**high-throughput.yaml** - Stress test
- **Duration**: 5 minutes
- **Load**: 100k msgs/sec
- **Producers/Consumers**: 4 each
- **Use case**: Maximum throughput testing, resource saturation

**large-messages.yaml** - Large payload test
- **Duration**: 5 minutes
- **Load**: 5k msgs/sec (64 KB messages)
- **Topics**: 1 (8 partitions)
- **Use case**: Test with large payloads, network bandwidth testing

**multi-topic.yaml** - Distributed load
- **Duration**: 5 minutes
- **Load**: 25k msgs/sec across 10 topics
- **Topics**: 10 (4 partitions each)
- **Use case**: Multi-tenancy simulation, topic distribution

### Comprehensive Tests (> 10 minutes)

**baseline.yaml** - Full test matrix
- **Duration**: ~30 minutes (multiple test runs)
- **Variations**: Multiple message sizes, rates, and configurations
- **Use case**: Comprehensive performance characterization

## Test Plan Structure

Each test plan contains:

```yaml
name: "test-name"
description: "What this test does"

# Base workload - defaults for all test runs
base_workload:
  name: "workload-name"
  topics: 1
  partitions_per_topic: 16
  message_size: 1024          # bytes
  subscriptions_per_topic: 1
  producers_per_topic: 1
  consumers_per_topic: 1
  consumer_backlog_size_gb: 0
  test_duration_minutes: 5
  warmup_duration_minutes: 1

# Individual test runs (can override base settings)
test_runs:
  - name: "test-1"
    description: "What this specific test does"
    type: "fixed_rate"
    producer_rate: 10000      # msgs/sec

# Optional reporting configuration
reporting:
  output_format:
    - "html"
    - "json"
  include_raw_data: true
  metrics_to_highlight:
    - "throughput"
    - "p99_latency"
```

## Usage Examples

### Run a specific test plan
```bash
python scripts/orchestrator.py run --test-plan config/test-plans/simple.yaml
```

### Run with custom experiment ID
```bash
python scripts/orchestrator.py run \
  --test-plan config/test-plans/high-throughput.yaml \
  --experiment-id throughput-test-001
```

### Generate report after test
```bash
python scripts/orchestrator.py report --experiment-id latest
```

## Creating Custom Test Plans

1. Copy an existing test plan as a template:
   ```bash
   cp config/test-plans/simple.yaml config/test-plans/my-custom-test.yaml
   ```

2. Modify the workload parameters:
   - Adjust message sizes, topic counts, partition counts
   - Change producer/consumer counts
   - Set appropriate test duration

3. Define test runs with different rates or configurations

4. Run your custom test:
   ```bash
   python scripts/orchestrator.py run --test-plan config/test-plans/my-custom-test.yaml
   ```

## Test Types

Currently supported:
- **fixed_rate**: Run at a constant message rate

Future (not yet implemented):
- **ramp_up**: Gradually increase load
- **scale_to_failure**: Push until saturation
- **latency_sensitivity**: Measure latency at various loads

## Workload Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `topics` | Number of topics to create | 1 |
| `partitions_per_topic` | Partitions per topic | 16 |
| `message_size` | Message payload size (bytes) | 1024 |
| `subscriptions_per_topic` | Subscriptions per topic | 1 |
| `producers_per_topic` | Producers per topic | 1 |
| `consumers_per_topic` | Consumers per subscription | 1 |
| `consumer_backlog_size_gb` | Initial backlog size | 0 |
| `test_duration_minutes` | Test run duration | 5 |
| `warmup_duration_minutes` | Warmup before metrics | 1 |
| `producer_rate` | Target msgs/sec | Required |

## Tips

### Choosing the Right Test

1. **First time setup**: Start with `poc.yaml` (2 min validation)
2. **Configuration changes**: Use `simple.yaml` (quick baseline)
3. **Performance tuning**: Use `baseline.yaml` (comprehensive)
4. **Latency SLAs**: Use `latency.yaml` (low load, detailed metrics)
5. **Capacity planning**: Use `high-throughput.yaml` (stress test)

### Test Duration Guidelines

- **Development/CI**: 2-5 minutes (poc, simple)
- **Pre-production**: 10-15 minutes (high-throughput, multi-topic)
- **Production validation**: 30+ minutes (baseline with multiple runs)

### Interpreting Results

Results are saved to `results/<experiment-id>/`:
- `test_report.html` - Interactive HTML report with Grafana links
- `benchmark_results/*.log` - OMB test results
- `orchestrator.log` - Detailed execution log

Key metrics to watch:
- **Throughput**: msgs/sec and MB/sec
- **Latency**: p50, p95, p99, p99.9, max
- **Errors**: Should be 0 for healthy clusters
- **Resource usage**: CPU, memory, network (if collected)
