# Synchronized Interactive Charts

## Overview

All charts in the enhanced reporting system now use **Plotly** for interactive visualization with **synchronized zoom and pan** across related charts. When you zoom or pan in one chart, all time-series charts automatically adjust to show the same time range.

## How It Works

### Synchronized Time Axis

All time-based charts share a unique **match group ID** that synchronizes their x-axis (time). This means:

- **Zoom in one chart** → All charts zoom to the same time range
- **Pan left/right** → All charts pan together
- **Reset zoom** (double-click) → All charts reset together

### Which Charts Are Synchronized?

#### Always Synchronized (Same Match Group)
These charts share the same time axis and stay in sync:

1. **Publish Latency P99** (OMB)
2. **Publish Latency Average** (OMB)
3. **Throughput** (OMB - Publish/Consume Rates)
4. **End-to-End Latency P95** (OMB - if available)
5. **Throughput + Health** (Health metrics overlay)
6. **Latency Percentiles** (All percentiles)
7. **Broker Resource Utilization** (CPU/Memory)
8. **Bookie Resource Utilization** (CPU/Memory)

#### Not Synchronized
These charts use different x-axis scales and don't sync:

- **Publish Latency Quantiles** - Uses percentile scale (50%, 99%, 99.9%, etc.), not time
- **Broker Health Heatmap** - Uses discrete time labels, not continuous timeline

## Interactive Features

### Zoom
- **Box Zoom**: Click and drag to select a time range
- **Scroll Zoom**: Scroll wheel on desktop to zoom in/out
- **Double-Click Reset**: Double-click to reset to full view

### Pan
- **Drag**: Click and drag (when zoomed) to pan left/right
- **Arrow Keys**: Use arrow keys to pan when focused

### Hover
- **Unified Hover**: Hover shows values for all series at that time point
- **Individual Hover**: Each line shows exact values on hover

### Export
- **Camera Icon**: Click to download chart as PNG
- **Self-Contained**: Charts work offline without internet

## Usage Examples

### Scenario 1: Correlate Latency with GC Pauses

1. Open the report: `results/latest/report/index.html`
2. Identify a latency spike in **"Latency Percentiles"** chart
3. Note the time (e.g., 120 seconds)
4. The **"Throughput + Health"** chart automatically shows the same time range
5. Look at the GC Activity subplot - see if there's a GC pause at 120s
6. Zoom into 115-125s in either chart - both zoom together
7. Analyze the correlation between latency and GC

### Scenario 2: Analyze Throughput Drop

1. See throughput drop in **"Throughput"** chart at time X
2. All synchronized charts now show time X
3. Check **"Broker Resource Utilization"** - see if CPU spiked
4. Check **"Latency P99"** - see if latency increased
5. Check **"Throughput + Health"** - see if JVM heap was high
6. All charts stay in sync as you zoom/pan

### Scenario 3: Compare Multiple Metrics at Specific Time

1. Zoom into a 30-second window (e.g., 90-120s)
2. All time-series charts zoom to 90-120s
3. Compare:
   - Throughput trends
   - Latency distribution
   - CPU/Memory usage
   - JVM heap patterns
   - GC frequency
4. Identify correlations without manually aligning charts

## Benefits

### 1. **Faster Root Cause Analysis**
- No need to manually align time ranges
- Instantly see correlations across metrics
- Zoom to problematic periods in all charts at once

### 2. **Easier Pattern Detection**
- Identify periodic patterns across metrics
- See if infrastructure changes affect performance
- Spot anomalies that span multiple metrics

### 3. **Efficient Troubleshooting**
- Focus on specific time windows
- Compare before/during/after incidents
- Validate theories by checking multiple metrics

### 4. **Better Communication**
- Screenshot multiple charts showing same time range
- Share insights with synchronized context
- Reproducible analysis across team

## Technical Details

### Implementation

Charts use Plotly's `matches` parameter to synchronize x-axes:

```python
# All charts in the same workload/test get the same match group
match_group = f"x{uuid.uuid4().hex[:8]}"  # e.g., "xa3f8d912"

# Each chart's x-axis is configured to match this group
fig.update_xaxes(matches=match_group)
```

### Match Group Scope

- **Per Test Run**: Each test generates a unique match group
- **Cross-Chart**: All time-series charts for that test share the group
- **Isolated**: Different tests have different groups (no interference)

### Browser Compatibility

Synchronized zoom works in:
- ✅ Chrome 90+
- ✅ Firefox 88+
- ✅ Safari 14+
- ✅ Edge 90+

## Tips & Tricks

### Tip 1: Reset All Charts Quickly
**Double-click any synchronized chart** to reset all charts to full view.

### Tip 2: Precise Time Selection
Use the **zoom box** (click-drag) for precise time range selection. All charts will snap to exactly that range.

### Tip 3: Navigate Large Test Runs
For long tests (>10 minutes):
1. Start with full view to see overall patterns
2. Identify interesting periods
3. Zoom in for detailed analysis
4. All charts maintain alignment

### Tip 4: Compare Pre/Post Event
1. Find event time in any chart (e.g., throughput spike)
2. Zoom to show 30s before and 30s after
3. All charts now show pre/post comparison
4. Analyze impact across all metrics

### Tip 5: Use Hover Mode
- Set to **"x unified"** (default) to see all series at once
- Vertical line shows across all charts at hover position
- Values for all metrics displayed together

## Troubleshooting

### Charts Not Syncing

**Problem**: Zooming one chart doesn't affect others

**Solutions**:
1. Refresh the page (browser caching issue)
2. Check browser console for JavaScript errors (F12)
3. Verify all charts are from the same test run
4. Ensure charts are HTML files (not SVG images)

### Sync Lag or Slow Response

**Problem**: Charts take time to sync after zoom

**Solutions**:
1. Close other browser tabs (reduce memory pressure)
2. Reduce number of open charts (open modal one at a time)
3. Use a modern browser with hardware acceleration
4. Simplify test duration (shorter tests = fewer data points)

### Zoom Not Working

**Problem**: Can't zoom or pan

**Solutions**:
1. Check if chart is in an iframe (modal view has better controls)
2. Click inside chart area first to focus
3. Try different zoom method (scroll, drag box, buttons)
4. Verify JavaScript is enabled in browser

## Advanced: Customizing Sync Behavior

### Disable Sync for Specific Charts

Edit `interactive_charts.py` or `omb_charts.py`:

```python
# Don't pass x_match_group to disable sync for a chart
create_latency_chart_plotly(
    output_file,
    title,
    data,
    x_match_group=None  # This chart won't sync
)
```

### Create Multiple Sync Groups

For complex reports with multiple test phases:

```python
# Phase 1 charts
match_group_phase1 = "x_phase1"

# Phase 2 charts
match_group_phase2 = "x_phase2"

# Charts within each phase sync, but phases don't sync with each other
```

### Cross-Experiment Sync

To compare multiple experiments with synchronized time:

```python
# Use same match group for charts from different experiments
shared_match_group = "x_comparison"

# All experiments will zoom/pan together
```

## Future Enhancements

Potential improvements for synchronized charts:

1. **Sync Y-Axis**: Option to sync y-axis scales (for same-unit metrics)
2. **Annotation Sync**: Add annotations that appear on all charts
3. **Time Markers**: Click to add markers that show on all charts
4. **Comparison Mode**: Side-by-side sync for multiple test runs
5. **Range Selector**: Unified time range selector for all charts

## Related Documentation

- [ENHANCED_REPORTING.md](ENHANCED_REPORTING.md) - Overview of reporting system
- [omb_charts.py](scripts/omb_charts.py) - OMB chart generation with sync
- [interactive_charts.py](scripts/interactive_charts.py) - Health chart generation with sync
- [report_generator.py](scripts/report_generator.py) - Report assembly

---

**Questions?**
- Check Plotly documentation: https://plotly.com/python/
- Look at source code: `scripts/omb_charts.py` line 127-129
- Test sync behavior: Generate a report and try zooming!
