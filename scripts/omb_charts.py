#!/usr/bin/env python3
"""
OMB Chart Generation Module
Creates charts from OpenMessaging Benchmark results using pygal.
Adapted from: https://github.com/datastax/openmessaging-benchmark/blob/master/bin/create_charts.py
"""

import json
import logging
import math
from itertools import chain
from pathlib import Path
from typing import Dict, List, Tuple

try:
    import pygal
    PYGAL_AVAILABLE = True
except ImportError:
    PYGAL_AVAILABLE = False
    logging.warning("pygal not available - chart generation disabled")

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

            workload = result.get('workload', 'unknown')
            if workload not in workload_results:
                workload_results[workload] = []

            workload_results[workload].append(result)

        except Exception as e:
            logger.error(f"Error loading {result_file}: {e}")

    return workload_results


def create_latency_chart(
    output_file: Path,
    title: str,
    time_series: List[Tuple[str, List[float]]],
    y_label: str = 'Latency (ms)'
) -> None:
    """Create time-series chart for latency metrics."""
    if not PYGAL_AVAILABLE:
        logger.warning("Skipping chart generation - pygal not installed")
        return

    chart = pygal.XY(
        dots_size=0.3,
        legend_at_bottom=True,
        human_readable=True
    )
    chart.title = title
    chart.y_title = y_label
    chart.x_title = 'Time (seconds)'

    # Add data series
    for label, values in time_series:
        if isinstance(values, list):
            # Time series data - plot with 10-second intervals
            chart.add(label, [(10 * x, y) for x, y in enumerate(values)])
        else:
            # Single value - plot as horizontal line
            chart.add(label, [(0, values), (100, values)])

    # Set Y range with 20% headroom
    max_val = max(chain(*[v if isinstance(v, list) else [v] for (_, v) in time_series]))
    chart.range = (0, max_val * 1.20)

    chart.render_to_file(str(output_file))
    logger.info(f"Generated chart: {output_file}")


def create_throughput_chart(
    output_file: Path,
    title: str,
    publish_series: List[Tuple[str, List[float]]],
    consume_series: List[Tuple[str, List[float]]],
    y_label: str = 'Rate (msg/s)'
) -> None:
    """Create combined chart for publish and consumption rates."""
    if not PYGAL_AVAILABLE:
        logger.warning("Skipping chart generation - pygal not installed")
        return

    chart = pygal.XY(
        dots_size=0.3,
        legend_at_bottom=True,
        human_readable=True
    )
    chart.title = title
    chart.y_title = y_label
    chart.x_title = 'Time (seconds)'

    # Add publish series
    for label, values in publish_series:
        if isinstance(values, list):
            chart.add(f"{label} (Publish)", [(10 * x, y) for x, y in enumerate(values)])
        else:
            chart.add(f"{label} (Publish)", [(0, values), (100, values)])

    # Add consume series
    for label, values in consume_series:
        if isinstance(values, list):
            chart.add(f"{label} (Consume)", [(10 * x, y) for x, y in enumerate(values)])
        else:
            chart.add(f"{label} (Consume)", [(0, values), (100, values)])

    # Calculate range across both series
    all_values = []
    for _, v in publish_series + consume_series:
        if isinstance(v, list):
            all_values.extend(v)
        else:
            all_values.append(v)

    if all_values:
        max_val = max(all_values)
        chart.range = (0, max_val * 1.20)

    chart.render_to_file(str(output_file))
    logger.info(f"Generated chart: {output_file}")


def create_quantile_chart(
    output_file: Path,
    title: str,
    time_series: List[Tuple[str, Dict[str, float]]],
    y_label: str = 'Latency (ms)'
) -> None:
    """Create percentile quantile chart with log scale."""
    if not PYGAL_AVAILABLE:
        logger.warning("Skipping chart generation - pygal not installed")
        return

    chart = pygal.XY(
        legend_at_bottom=True,
        x_value_formatter=lambda x: '{:.3f}%'.format(100.0 - (100.0 / (10 ** x))),
        show_dots=True,
        dots_size=0.3,
        show_x_guides=True,
        human_readable=True
    )
    chart.title = title
    chart.y_title = y_label
    chart.x_title = 'Percentile'
    chart.x_labels = [1, 2, 3, 4, 5]

    for label, quantiles in time_series:
        # Convert quantiles to sorted list and transform to log scale
        values = sorted((float(x), y) for x, y in quantiles.items())
        # Only include up to 99.999th percentile for reasonable chart display
        xy_values = [(math.log10(100 / (100 - x)), y) for x, y in values if x <= 99.999]
        chart.add(label, xy_values)

    chart.render_to_file(str(output_file))
    logger.info(f"Generated chart: {output_file}")


def generate_all_charts(result_files: List[Path], output_dir: Path) -> List[Path]:
    """
    Generate all standard OMB charts from result files.

    Args:
        result_files: List of JSON result files
        output_dir: Directory to write chart SVG files

    Returns:
        List of generated chart file paths
    """
    if not PYGAL_AVAILABLE:
        logger.warning("pygal not installed - skipping chart generation")
        logger.info("Install with: pip install pygal")
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    generated_charts = []

    # Load and group results by workload
    workload_results = load_results(result_files)

    for workload, results in workload_results.items():
        logger.info(f"Generating charts for workload: {workload}")
        workload_safe = workload.replace('/', '-')

        # Chart 1: Publish Latency P99
        create_latency_chart(
            output_dir / f"{workload_safe} - Publish Latency 99pct.svg",
            "Publish Latency 99pct",
            [(r['legend'], r.get('publishLatency99pct', [])) for r in results]
        )
        generated_charts.append(output_dir / f"{workload_safe} - Publish Latency 99pct.svg")

        # Chart 2: Publish Latency Average
        create_latency_chart(
            output_dir / f"{workload_safe} - Publish Latency Avg.svg",
            "Publish Latency Average",
            [(r['legend'], r.get('publishLatencyAvg', [])) for r in results]
        )
        generated_charts.append(output_dir / f"{workload_safe} - Publish Latency Avg.svg")

        # Chart 3: Publish and Consumption Rates
        create_throughput_chart(
            output_dir / f"{workload_safe} - Throughput.svg",
            "Publish and Consumption Rates",
            [(r['legend'], r.get('publishRate', [])) for r in results],
            [(r['legend'], r.get('consumeRate', [])) for r in results]
        )
        generated_charts.append(output_dir / f"{workload_safe} - Throughput.svg")

        # Chart 4: Publish Latency Quantiles
        create_quantile_chart(
            output_dir / f"{workload_safe} - Publish Latency Quantiles.svg",
            "Publish Latency Quantiles",
            [(r['legend'], r.get('aggregatedPublishLatencyQuantiles', {})) for r in results]
        )
        generated_charts.append(output_dir / f"{workload_safe} - Publish Latency Quantiles.svg")

        # Chart 5: End-to-End Latency P95 (if available)
        if any('endToEndLatency95pct' in r for r in results):
            create_latency_chart(
                output_dir / f"{workload_safe} - End To End Latency 95pct.svg",
                "End To End Latency 95pct",
                [(r['legend'], r.get('endToEndLatency95pct', [])) for r in results]
            )
            generated_charts.append(output_dir / f"{workload_safe} - End To End Latency 95pct.svg")

    logger.info(f"Generated {len(generated_charts)} charts in {output_dir}")
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
        print(f"\n✓ Generated {len(charts)} charts in: {output_dir}")
        print("\nCharts created:")
        for chart in charts:
            print(f"  - {chart.name}")
    else:
        print("\n✗ No charts generated. Install pygal: pip install pygal")
