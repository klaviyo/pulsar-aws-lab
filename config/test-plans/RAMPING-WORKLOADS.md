# Ramping Workloads in OMB

This document explains how to use OpenMessaging Benchmark's ramping workload feature to automatically discover your cluster's maximum sustainable throughput.

## What is a Ramping Workload?

A ramping workload gradually increases the producer rate to find the maximum throughput your Pulsar cluster can sustain with acceptable latency and no message loss.

## How It Works

When `producer_rate: 0` is set in a workload configuration:

1. **OMB starts at a baseline rate** (default: 10,000 msgs/sec)
2. **Gradually increases the rate** in increments
3. **Monitors system health** (latency, backlog, errors)
4. **Finds the balanced rate** where throughput is maximized while maintaining stability
5. **Stops ramping** when it detects system degradation

## Configuration Options

### Option 1: Auto-Discovery from Default (10k msgs/sec)

```yaml
test_runs:
  - name: "auto-discover-max"
    description: "Find max throughput automatically"
    type: "ramp_up"
    producer_rate: 0  # Start at 10k and ramp up
```

**Best for**: Initial cluster benchmarking when you don't know the capacity

### Option 2: Custom Starting Rate

```yaml
test_runs:
  - name: "ramp-from-50k"
    description: "Ramp up from known baseline"
    type: "ramp_up"
    producer_rate: 0
    initial_rate: 50000  # Start at 50k msgs/sec
```

**Best for**:
- When you know the cluster can handle at least 50k msgs/sec
- Testing improvements after configuration changes
- Faster discovery on high-capacity clusters

### Option 3: Fixed Rate (Not Ramping)

```yaml
test_runs:
  - name: "fixed-100k"
    description: "Fixed load test"
    type: "fixed_rate"
    producer_rate: 100000  # Always produce at 100k msgs/sec
```

**Best for**:
- Testing known workloads
- Reproducible benchmarks
- Stress testing at specific rates

## Test Duration Considerations

Ramping tests require **longer durations** than fixed-rate tests:

- **Minimum recommended**: 10 minutes
- **Warmup**: 2-3 minutes (allows system to stabilize)
- **Ramping phase**: 5-8 minutes (gives time to find max rate)

Example:
```yaml
base_workload:
  test_duration_minutes: 10
  warmup_duration_minutes: 2
```

## Example: Complete Ramping Test Plan

See [ramp-to-max.yaml](./ramp-to-max.yaml) for a complete example with multiple ramping strategies.

## Running a Ramping Test

```bash
# Run the ramping test plan
python scripts/orchestrator.py run --test-plan config/test-plans/ramp-to-max.yaml

# Monitor in real-time
kubectl logs -f -n omb -l job-name=omb-auto-discover-max

# Generate report after completion
python scripts/orchestrator.py report --experiment-id latest
```

## Interpreting Results

After a ramping test completes, look for:

1. **Maximum achieved rate**: The highest throughput before degradation
2. **Latency trends**: When P99 latency starts increasing significantly
3. **Backlog growth**: When consumers can't keep up with producers
4. **Error rates**: Any publish/consume failures

The **optimal sustained rate** is typically 80-90% of the maximum achieved rate, providing headroom for traffic spikes.

## Comparison: Ramping vs Fixed Rate

| Feature | Ramping (`producer_rate: 0`) | Fixed Rate (`producer_rate: N`) |
|---------|------------------------------|----------------------------------|
| **Use Case** | Discover max capacity | Test known workload |
| **Duration** | 10+ minutes | 5 minutes |
| **Starting Point** | Configurable (default 10k) | Immediate at target rate |
| **Output** | Maximum sustainable rate | Performance at fixed rate |
| **Best For** | Initial benchmarking | Reproducible tests |

## Multiple Ramping Strategies in One Test Plan

You can run multiple ramping tests with different starting points:

```yaml
test_runs:
  # Start low - discover from scratch
  - name: "discover-from-10k"
    type: "ramp_up"
    producer_rate: 0

  # Start medium - faster discovery
  - name: "discover-from-50k"
    type: "ramp_up"
    producer_rate: 0
    initial_rate: 50000

  # Start high - test scaling beyond known capacity
  - name: "discover-from-100k"
    type: "ramp_up"
    producer_rate: 0
    initial_rate: 100000
```

## Advanced: Combining Fixed and Ramping Tests

```yaml
test_runs:
  # First, discover maximum
  - name: "discover-max"
    type: "ramp_up"
    producer_rate: 0

  # Then, validate at specific rates
  - name: "validate-50k"
    type: "fixed_rate"
    producer_rate: 50000

  - name: "validate-100k"
    type: "fixed_rate"
    producer_rate: 100000
```

## Tips for Successful Ramping Tests

1. **Start with longer durations**: Ramping needs time to find the optimal rate
2. **Monitor Grafana dashboards**: Watch for resource saturation (CPU, disk I/O)
3. **Use appropriate warmup**: Give the cluster time to stabilize before ramping
4. **Consider cluster size**: Larger clusters need higher initial rates
5. **Test different message sizes**: Max throughput varies with message size

## Troubleshooting

### Ramping Test Never Completes
- **Cause**: Test duration too long, or OMB keeps ramping
- **Solution**: Set a shorter test duration (10 minutes is usually sufficient)

### Ramping Starts Too Low/High
- **Cause**: Default 10k rate is not appropriate for your cluster
- **Solution**: Use `initial_rate` parameter to start closer to expected capacity

### Results Show Early Degradation
- **Cause**: Cluster resource constraints (CPU, disk, network)
- **Solution**: Review Grafana dashboards to identify bottleneck

## Related Files

- [high-throughput-100.yaml](./high-throughput-100.yaml) - Fixed-rate high throughput test
- [poc.yaml](./poc.yaml) - Simple proof-of-concept test
- [ramp-to-max.yaml](./ramp-to-max.yaml) - Ramping workload example
