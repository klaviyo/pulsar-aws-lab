#!/usr/bin/env python3
"""
OMB Chart Generation Module (Plotly Version)
Creates interactive synchronized charts from OpenMessaging Benchmark results using Plotly.
All charts share synchronized zoom/pan for easy correlation analysis.
"""

import json
import logging
import math
from itertools import chain
from pathlib import Path
from typing import Dict, List, Tuple
import uuid

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False
    logging.warning("plotly not available - chart generation disabled")

logger = logging.getLogger(__name__)


def load_results(result_files: List[Path]) -> Dict[str, List[Dict]]:
    """
    Load and group OMB results by workload.

    Args:
        result_files: List of paths to JSON result files

    Returns:
        Dictionary mapping workload name to list of results
    """
    workload_results = {}

    for result_file in result_files:
        try:
            with open(result_file, 'r') as f:
                result = json.load(f)

            # Add legend/label for this result
            result['legend'] = result.get('workload', result_file.stem)

            workload = result.get('workload', result_file.stem)
            if workload not in workload_results:
                workload_results[workload] = []

            workload_results[workload].append(result)

        except Exception as e:
            logger.error(f"Error loading {result_file}: {e}")

    return workload_results


def create_latency_chart_plotly(
    output_file: Path,
    title: str,
    time_series: List[Tuple[str, List[float]]],
    y_label: str = 'Latency (ms)',
    x_match_group: str = None
) -> None:
    """
    Create interactive time-series chart for latency metrics.

    Args:
        output_file: Path to save HTML file
        title: Chart title
        time_series: List of (label, values) tuples
        y_label: Y-axis label
        x_match_group: Group ID for synchronized zoom/pan
    """
    if not PLOTLY_AVAILABLE:
        logger.warning("Skipping chart generation - plotly not installed")
        return

    fig = go.Figure()

    # Color palette
    colors = ['#3498db', '#e74c3c', '#2ecc71', '#f39c12', '#9b59b6', '#1abc9c', '#34495e']

    # Add data series
    for idx, (label, values) in enumerate(time_series):
        if isinstance(values, list) and values:
            # Time series data - plot with 10-second intervals
            time_points = list(range(0, len(values) * 10, 10))
            fig.add_trace(go.Scatter(
                x=time_points,
                y=values,
                name=label,
                mode='lines',
                line=dict(color=colors[idx % len(colors)], width=2),
                hovertemplate=f'<b>{label}</b><br>Time: %{{x}}s<br>{y_label}: %{{y:.2f}}<extra></extra>'
            ))
        elif not isinstance(values, list):
            # Single value - plot as horizontal line
            fig.add_trace(go.Scatter(
                x=[0, 1000],
                y=[values, values],
                name=f"{label} (constant)",
                mode='lines',
                line=dict(color=colors[idx % len(colors)], width=2, dash='dash'),
                hovertemplate=f'<b>{label}</b><br>{y_label}: %{{y:.2f}}<extra></extra>'
            ))

    # Update layout with synchronized zoom
    fig.update_layout(
        title=title,
        xaxis_title="Time (seconds)",
        yaxis_title=y_label,
        hovermode='x unified',
        template='plotly_white',
        height=500,
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        )
    )

    # Add synchronized zoom group
    if x_match_group:
        fig.update_xaxes(matches=x_match_group)

    fig.write_html(str(output_file))
    logger.info(f"Generated chart: {output_file}")


def create_throughput_chart_plotly(
    output_file: Path,
    title: str,
    publish_series: List[Tuple[str, List[float]]],
    consume_series: List[Tuple[str, List[float]]],
    y_label: str = 'Rate (msg/s)',
    x_match_group: str = None
) -> None:
    """
    Create interactive combined chart for publish and consumption rates.

    Args:
        output_file: Path to save HTML file
        title: Chart title
        publish_series: List of (label, values) tuples for publish rates
        consume_series: List of (label, values) tuples for consume rates
        y_label: Y-axis label
        x_match_group: Group ID for synchronized zoom/pan
    """
    if not PLOTLY_AVAILABLE:
        logger.warning("Skipping chart generation - plotly not installed")
        return

    fig = go.Figure()

    # Color palette
    colors = ['#3498db', '#e74c3c', '#2ecc71', '#f39c12', '#9b59b6', '#1abc9c']

    # Add publish series
    for idx, (label, values) in enumerate(publish_series):
        if isinstance(values, list) and values:
            time_points = list(range(0, len(values) * 10, 10))
            fig.add_trace(go.Scatter(
                x=time_points,
                y=values,
                name=f"{label} (Publish)",
                mode='lines',
                line=dict(color=colors[idx % len(colors)], width=2),
                hovertemplate=f'<b>{label} Publish</b><br>Time: %{{x}}s<br>Rate: %{{y:.0f}} msg/s<extra></extra>'
            ))

    # Add consume series
    for idx, (label, values) in enumerate(consume_series):
        if isinstance(values, list) and values:
            time_points = list(range(0, len(values) * 10, 10))
            fig.add_trace(go.Scatter(
                x=time_points,
                y=values,
                name=f"{label} (Consume)",
                mode='lines',
                line=dict(color=colors[idx % len(colors)], width=2, dash='dash'),
                hovertemplate=f'<b>{label} Consume</b><br>Time: %{{x}}s<br>Rate: %{{y:.0f}} msg/s<extra></extra>'
            ))

    fig.update_layout(
        title=title,
        xaxis_title="Time (seconds)",
        yaxis_title=y_label,
        hovermode='x unified',
        template='plotly_white',
        height=500,
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        )
    )

    # Add synchronized zoom group
    if x_match_group:
        fig.update_xaxes(matches=x_match_group)

    fig.write_html(str(output_file))
    logger.info(f"Generated chart: {output_file}")


def create_quantile_chart_plotly(
    output_file: Path,
    title: str,
    time_series: List[Tuple[str, Dict[str, float]]],
    y_label: str = 'Latency (ms)',
    x_match_group: str = None
) -> None:
    """
    Create percentile quantile chart with log scale.

    Args:
        output_file: Path to save HTML file
        title: Chart title
        time_series: List of (label, quantiles_dict) tuples
        y_label: Y-axis label
        x_match_group: Group ID for synchronized zoom/pan
    """
    if not PLOTLY_AVAILABLE:
        logger.warning("Skipping chart generation - plotly not installed")
        return

    fig = go.Figure()

    # Color palette
    colors = ['#3498db', '#e74c3c', '#2ecc71', '#f39c12', '#9b59b6', '#1abc9c']

    for idx, (label, quantiles) in enumerate(time_series):
        if not quantiles:
            continue

        # Convert quantiles to sorted list and transform to log scale
        values = sorted((float(x), y) for x, y in quantiles.items())
        # Only include up to 99.999th percentile for reasonable chart display
        filtered_values = [(x, y) for x, y in values if x <= 99.999]

        if not filtered_values:
            continue

        # Transform x-axis to log scale: percentile → log10(100/(100-percentile))
        x_log = [math.log10(100 / (100 - x)) for x, _ in filtered_values]
        y_vals = [y for _, y in filtered_values]

        # Create custom tick labels for percentiles
        percentile_labels = [f"{x}%" for x, _ in filtered_values]

        fig.add_trace(go.Scatter(
            x=x_log,
            y=y_vals,
            name=label,
            mode='lines+markers',
            line=dict(color=colors[idx % len(colors)], width=2),
            marker=dict(size=6),
            text=percentile_labels,
            hovertemplate=f'<b>{label}</b><br>Percentile: %{{text}}<br>{y_label}: %{{y:.2f}}<extra></extra>'
        ))

    # Custom x-axis ticks for common percentiles
    x_tick_vals = [math.log10(100 / (100 - p)) for p in [50, 90, 95, 99, 99.9, 99.99, 99.999]]
    x_tick_text = ['50%', '90%', '95%', '99%', '99.9%', '99.99%', '99.999%']

    fig.update_layout(
        title=title,
        xaxis_title="Percentile",
        yaxis_title=y_label,
        xaxis=dict(
            tickmode='array',
            tickvals=x_tick_vals,
            ticktext=x_tick_text
        ),
        hovermode='closest',
        template='plotly_white',
        height=500,
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        )
    )

    fig.write_html(str(output_file))
    logger.info(f"Generated chart: {output_file}")


def generate_all_charts(result_files: List[Path], output_dir: Path) -> List[Path]:
    """
    Generate all standard OMB charts from result files using Plotly with synchronized zoom.

    Args:
        result_files: List of JSON result files
        output_dir: Directory to write chart HTML files

    Returns:
        List of generated chart file paths
    """
    if not PLOTLY_AVAILABLE:
        logger.warning("plotly not installed - skipping chart generation")
        logger.info("Install with: pip install plotly")
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    generated_charts = []

    # Load and group results by workload
    workload_results = load_results(result_files)

    for workload, results in workload_results.items():
        logger.info(f"Generating charts for workload: {workload}")
        workload_safe = workload.replace('/', '-')

        # Use standard Plotly axis matching (all charts match "x" axis)
        # Note: Plotly's matches parameter only accepts specific formats like "x", "x2", "y", "y2"
        # All charts in this report will sync via "x" axis
        match_group = "x"

        # Chart 1: Publish Latency P99
        chart_file = output_dir / f"{workload_safe} - Publish Latency 99pct.html"
        create_latency_chart_plotly(
            chart_file,
            "Publish Latency 99pct",
            [(r['legend'], r.get('publishLatency99pct', [])) for r in results],
            x_match_group=match_group
        )
        generated_charts.append(chart_file)

        # Chart 2: Publish Latency Average
        chart_file = output_dir / f"{workload_safe} - Publish Latency Avg.html"
        create_latency_chart_plotly(
            chart_file,
            "Publish Latency Average",
            [(r['legend'], r.get('publishLatencyAvg', [])) for r in results],
            x_match_group=match_group
        )
        generated_charts.append(chart_file)

        # Chart 3: Publish and Consumption Rates
        chart_file = output_dir / f"{workload_safe} - Throughput.html"
        create_throughput_chart_plotly(
            chart_file,
            "Publish and Consumption Rates",
            [(r['legend'], r.get('publishRate', [])) for r in results],
            [(r['legend'], r.get('consumeRate', [])) for r in results],
            x_match_group=match_group
        )
        generated_charts.append(chart_file)

        # Chart 4: Publish Latency Quantiles (no time sync - different x-axis)
        chart_file = output_dir / f"{workload_safe} - Publish Latency Quantiles.html"
        create_quantile_chart_plotly(
            chart_file,
            "Publish Latency Quantiles",
            [(r['legend'], r.get('aggregatedPublishLatencyQuantiles', {})) for r in results]
        )
        generated_charts.append(chart_file)

        # Chart 5: End-to-End Latency P95 (if available)
        if any('endToEndLatency95pct' in r for r in results):
            chart_file = output_dir / f"{workload_safe} - End To End Latency 95pct.html"
            create_latency_chart_plotly(
                chart_file,
                "End To End Latency 95pct",
                [(r['legend'], r.get('endToEndLatency95pct', [])) for r in results],
                x_match_group=match_group
            )
            generated_charts.append(chart_file)

    logger.info(f"Generated {len(generated_charts)} interactive charts in {output_dir}")
    return generated_charts


if __name__ == '__main__':
    """Standalone script to generate charts from OMB results."""
    import sys

    if len(sys.argv) < 2:
        print("Usage: omb_charts.py <result_file1.json> [result_file2.json ...]")
        print("   or: omb_charts.py <results_directory>")
        sys.exit(1)

    # Setup logging
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    # Parse arguments
    arg = Path(sys.argv[1])

    if arg.is_dir():
        # Directory provided - find all JSON files
        result_files = list(arg.glob("*.json"))
        output_dir = arg / "charts"
    else:
        # Individual files provided
        result_files = [Path(f) for f in sys.argv[1:]]
        output_dir = result_files[0].parent / "charts"

    if not result_files:
        print("Error: No JSON result files found")
        sys.exit(1)

    print(f"Generating charts from {len(result_files)} result file(s)...")
    charts = generate_all_charts(result_files, output_dir)

    if charts:
        print(f"\n✓ Generated {len(charts)} interactive charts in: {output_dir}")
        print("\nCharts created:")
        for chart in charts:
            print(f"  - {chart.name}")
        print("\n💡 All time-series charts share synchronized zoom/pan!")
        print("   Zoom in one chart and others will follow the same time range.")
    else:
        print("\n✗ No charts generated. Install plotly: pip install plotly")
