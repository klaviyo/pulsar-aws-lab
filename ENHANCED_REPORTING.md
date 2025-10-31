# Enhanced Reporting with Infrastructure Health Metrics

## Overview

The enhanced reporting system now collects and visualizes infrastructure health metrics alongside OMB benchmark results, providing comprehensive insights into both application performance and infrastructure behavior during load tests.

## Key Features

### 1. **Infrastructure Metrics Collection**

During test execution, the orchestrator automatically collects:

#### Broker Metrics
- JVM heap usage (used/max)
- Garbage collection activity
- CPU utilization
- Memory consumption
- Thread counts

#### Bookie Metrics
- JVM heap usage
- Garbage collection activity
- CPU utilization
- Memory consumption
- Disk I/O metrics

#### Collection Timeline
- **Baseline**: Captured before test starts
- **Continuous**: Every 30 seconds during test execution
- **Final**: Captured after test completes

### 2. **Interactive Charts with Plotly**

All reports now include interactive HTML charts that support:
- **Zoom**: Click and drag to zoom into specific time ranges
- **Pan**: Navigate through the timeline
- **Hover**: View detailed metrics at any point
- **Export**: Download charts as PNG images
- **Offline**: Fully self-contained HTML files

#### Chart Types

1. **Throughput + Health Timeline**
   - Combined view of OMB throughput and JVM memory usage
   - Multi-axis chart showing correlation between load and resource consumption
   - GC activity subplot showing garbage collection pauses

2. **Latency Percentiles**
   - Interactive view of p50, p95, p99, p99.9, and max latency
   - Hover to see exact values at any timestamp
   - Identify latency spikes and their timing

3. **Broker Health Heatmap**
   - Visual health status of all brokers over time
   - Color-coded: Green (healthy), Orange (warning), Red (critical)
   - Quick identification of problematic brokers

4. **Resource Utilization**
   - Separate charts for brokers and bookies
   - CPU and memory usage timelines
   - Identify resource bottlenecks

5. **Comparison Charts**
   - Compare multiple test runs side-by-side
   - Visualize performance improvements or regressions
   - Filter and analyze specific metrics

### 3. **Offline Report Archival**

All metrics are embedded in the report for long-term storage:

```
results/<experiment-id>/
├── metrics/
│   ├── baseline_snapshot.json      # Pre-test infrastructure state
│   ├── timeseries.json              # Continuous metrics during test
│   ├── final_snapshot.json          # Post-test infrastructure state
│   └── plot_data.json               # Formatted data for charts
├── report/
│   ├── index.html                   # Self-contained report
│   ├── charts/
│   │   ├── *_throughput_health.html # Interactive charts
│   │   ├── *_latency.html
│   │   ├── *_broker_heatmap.html
│   │   ├── *_brokers_resources.html
│   │   └── *_bookies_resources.html
│   ├── metrics.csv                  # Exportable data
│   └── metrics.json                 # Raw metrics
└── benchmark_results/
    └── *.json                       # OMB results
```

## Installation

### Required Dependencies

```bash
# Install enhanced reporting dependencies
pip install -r scripts/requirements.txt

# Key additions:
# - plotly>=5.18.0 (interactive charts)
# - kaleido>=0.2.1 (static image export)
```

### Optional: Streamlit Dashboard

For advanced interactive analysis:

```bash
# Uncomment in requirements.txt
pip install streamlit>=1.29.0

# Launch dashboard
streamlit run scripts/report_dashboard.py -- --experiment-dir results/
```

## Usage

### Running Tests with Metrics Collection

Metrics collection is **automatic** - no configuration needed:

```bash
# Run test plan (metrics collected automatically)
python scripts/orchestrator.py run --test-plan config/test-plans/poc.yaml
```

The orchestrator will:
1. Collect baseline metrics before test starts
2. Start background collection (every 30s)
3. Run the benchmark
4. Stop collection and save final metrics
5. Generate report with interactive charts

### Viewing Reports

```bash
# Open the generated report
open results/latest/report/index.html
```

The report includes:
- **Summary statistics** with key metrics
- **Grafana dashboard links** (time-scoped to test execution)
- **Interactive charts** with infrastructure health overlays
- **Detailed test results** table
- **Workload configurations** for reproducibility

### Analyzing Health Metrics

#### In the Report

1. **Click any chart** to view full-size with interactive controls
2. **Hover over timelines** to see exact values
3. **Zoom into spikes** to correlate latency with GC pauses
4. **Compare broker/bookie health** across the cluster

#### Programmatic Access

```python
from pathlib import Path
import json

# Load metrics
metrics_dir = Path("results/latest/metrics")

with open(metrics_dir / "timeseries.json") as f:
    timeseries = json.load(f)

# Access broker metrics at specific snapshot
snapshot = timeseries[0]
broker_metrics = snapshot['brokers']

for broker in broker_metrics:
    print(f"Broker: {broker['pod_name']}")
    print(f"  Heap Used: {broker['jvm']['heap_used_mb']} MB")
    print(f"  CPU: {broker['resources']['cpu']}")
```

## Configuration

### Adjust Collection Interval

Edit `orchestrator.py` to change collection frequency:

```python
# Default: 30 seconds
self.metrics_collector.start_background_collection(interval_seconds=30)

# More frequent: 10 seconds (more data, larger files)
self.metrics_collector.start_background_collection(interval_seconds=10)

# Less frequent: 60 seconds (less overhead)
self.metrics_collector.start_background_collection(interval_seconds=60)
```

### Disable Metrics Collection

If you want to skip metrics collection:

```python
# In orchestrator.py, comment out:
# self.metrics_collector.collect_baseline_metrics()
# self.metrics_collector.start_background_collection(...)
# self.metrics_collector.stop_background_collection()
```

## Interpreting Health Metrics

### Normal Patterns

- **Heap usage**: Gradual sawtooth pattern (allocate → GC → reset)
- **GC pauses**: Short, infrequent spikes (< 100ms)
- **CPU**: Steady during steady load, spikes during warmup/cooldown
- **Memory**: Stable, not continuously increasing

### Warning Signs

- **High heap usage** (> 80%): Risk of GC thrashing
- **Frequent GC pauses** (> 200ms): Can cause latency spikes
- **CPU at 100%**: Saturated, may drop messages
- **Memory leak**: Continuous increase without stabilization

### Correlations to Look For

1. **Latency spikes + GC pauses**: JVM garbage collection causing delays
2. **Throughput drops + CPU saturation**: Compute bottleneck
3. **Latency spikes + all brokers**: Likely BookKeeper/storage issue
4. **Latency spikes + one broker**: Single broker overloaded

## Advanced: Custom Metrics

### Add Custom Prometheus Queries

Edit `metrics_collector.py` to add custom metrics:

```python
def _collect_custom_metrics(self, pod_name: str) -> Dict:
    """Collect custom Prometheus metrics."""
    result = self.run_command(
        ["kubectl", "exec", "-n", "pulsar", pod_name, "--",
         "curl", "-s", "http://localhost:8080/metrics"],
        f"Get custom metrics for {pod_name}",
        capture_output=True,
        check=False
    )

    # Parse custom metrics from Prometheus format
    metrics = {}
    for line in result.stdout.split('\n'):
        if 'my_custom_metric' in line:
            value = float(line.split()[-1])
            metrics['custom_metric'] = value

    return metrics
```

### Create Custom Charts

Use `interactive_charts.py` as a base:

```python
from interactive_charts import InteractiveChartGenerator
from pathlib import Path

generator = InteractiveChartGenerator(Path("results/latest/report/charts"))

# Create custom chart
fig = go.Figure()
fig.add_trace(go.Scatter(x=[1,2,3], y=[4,5,6], name="Custom"))
fig.write_html("custom_chart.html")
```

## Benefits

### Comprehensive Root Cause Analysis

Traditional OMB reports show **what happened** (latency spike), but the enhanced reports show **why it happened** (GC pause, CPU saturation, disk bottleneck).

### Reproducible Results

All infrastructure state is captured, so you can:
- Compare test runs on identical infrastructure
- Identify infrastructure drift over time
- Validate that tests ran under expected conditions

### Offline Analysis

No need for live Grafana access:
- Reports are fully self-contained
- Can be archived indefinitely
- Shareable with stakeholders
- Works without Kubernetes/Prometheus access

### Correlation Analysis

Easily identify relationships:
- Does throughput correlate with heap usage?
- Do latency spikes coincide with GC pauses?
- Are resource limits causing performance issues?

## Troubleshooting

### "plotly not available" Warning

```bash
# Install plotly
pip install plotly kaleido
```

### Metrics Collection Fails

Check kubectl access:

```bash
# Verify access to Pulsar namespace
kubectl get pods -n pulsar

# Test metrics endpoint
kubectl exec -n pulsar pulsar-broker-0 -- curl -s http://localhost:8080/metrics
```

### Interactive Charts Not Rendering

- Ensure browser supports HTML5 and JavaScript
- Check browser console for errors (F12)
- Try opening in a different browser
- Verify chart files exist: `results/latest/report/charts/`

### Large Report Files

If reports are too large (> 50MB):

1. Reduce collection frequency (60s instead of 30s)
2. Limit test duration
3. Use static charts instead of interactive
4. Export metrics to separate files

## Future Enhancements

Potential future additions:

1. **Prometheus Integration**: Direct Prometheus API queries (no kubectl exec)
2. **Predictive Analysis**: ML-based anomaly detection
3. **Streaming Dashboards**: Real-time charts during test execution
4. **Cost Tracking**: Per-test infrastructure cost breakdown
5. **Comparison Tool**: Multi-experiment analysis UI
6. **Alerting**: Real-time alerts on health metric thresholds

## Related Documentation

- [CLAUDE.md](CLAUDE.md) - Project overview and usage
- [orchestrator.py](scripts/orchestrator.py) - Test execution
- [metrics_collector.py](scripts/metrics_collector.py) - Metrics collection
- [interactive_charts.py](scripts/interactive_charts.py) - Chart generation
- [report_generator.py](scripts/report_generator.py) - Report assembly
