#!/usr/bin/env python3
"""
Interactive Chart Generation with Plotly
Creates interactive HTML charts for OMB results and infrastructure health metrics
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    import plotly.express as px
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False
    logging.warning("plotly not available - interactive chart generation disabled")

logger = logging.getLogger(__name__)


class InteractiveChartGenerator:
    """Generate interactive charts using Plotly for comprehensive test reporting."""

    def __init__(self, output_dir: Path):
        """
        Initialize chart generator.

        Args:
            output_dir: Directory to save generated chart HTML files
        """
        if not PLOTLY_AVAILABLE:
            raise ImportError("plotly is required for interactive charts. Install with: pip install plotly")

        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_throughput_health_chart(
        self,
        omb_results: Dict,
        health_metrics: Optional[Dict] = None,
        test_name: str = "test"
    ) -> Path:
        """
        Create combined chart showing OMB throughput with broker/bookie health overlay.

        Args:
            omb_results: OMB benchmark results
            health_metrics: Infrastructure health timeseries
            test_name: Name of the test

        Returns:
            Path to generated HTML file
        """
        logger.info(f"Generating throughput + health chart for {test_name}")

        # Create figure with secondary y-axis
        fig = make_subplots(
            rows=2, cols=1,
            subplot_titles=('Throughput with JVM Memory', 'GC Activity'),
            vertical_spacing=0.12,
            specs=[[{"secondary_y": True}], [{"secondary_y": False}]]
        )

        # Extract OMB throughput data
        publish_rates = omb_results.get('publishRate', [])
        consume_rates = omb_results.get('consumeRate', [])

        if publish_rates:
            time_points = list(range(0, len(publish_rates) * 10, 10))  # 10-second intervals

            # Add publish rate
            fig.add_trace(
                go.Scatter(
                    x=time_points,
                    y=publish_rates,
                    name="Publish Rate",
                    line=dict(color='#3498db', width=2),
                    hovertemplate='<b>Publish Rate</b><br>Time: %{x}s<br>Rate: %{y:.0f} msg/s<extra></extra>'
                ),
                row=1, col=1, secondary_y=False
            )

            # Add consume rate
            fig.add_trace(
                go.Scatter(
                    x=time_points,
                    y=consume_rates,
                    name="Consume Rate",
                    line=dict(color='#2ecc71', width=2, dash='dash'),
                    hovertemplate='<b>Consume Rate</b><br>Time: %{x}s<br>Rate: %{y:.0f} msg/s<extra></extra>'
                ),
                row=1, col=1, secondary_y=False
            )

        # Add health metrics if available
        if health_metrics and health_metrics.get('brokers'):
            # Add broker JVM heap usage on secondary y-axis
            for broker_name, broker_data in health_metrics['brokers'].items():
                heap_mb = broker_data.get('heap_used_mb', [])
                if heap_mb and any(v is not None for v in heap_mb):
                    # Convert timestamps to seconds
                    timestamps = health_metrics.get('timestamps', [])
                    time_seconds = self._timestamps_to_seconds(timestamps)

                    fig.add_trace(
                        go.Scatter(
                            x=time_seconds,
                            y=heap_mb,
                            name=f"{broker_name} Heap",
                            line=dict(width=1.5, dash='dot'),
                            hovertemplate=f'<b>{broker_name}</b><br>Time: %{{x}}s<br>Heap: %{{y:.0f}} MB<extra></extra>'
                        ),
                        row=1, col=1, secondary_y=True
                    )

        # Add GC activity subplot
        if health_metrics and health_metrics.get('brokers'):
            timestamps = health_metrics.get('timestamps', [])
            time_seconds = self._timestamps_to_seconds(timestamps)

            for broker_name, broker_data in health_metrics['brokers'].items():
                gc_times = broker_data.get('gc_time_seconds', [])
                if gc_times and any(v is not None for v in gc_times):
                    # Calculate GC rate (diff between consecutive values)
                    gc_rates = [0] + [
                        (gc_times[i] - gc_times[i-1]) if gc_times[i] is not None and gc_times[i-1] is not None else 0
                        for i in range(1, len(gc_times))
                    ]

                    fig.add_trace(
                        go.Bar(
                            x=time_seconds,
                            y=gc_rates,
                            name=f"{broker_name} GC",
                            hovertemplate=f'<b>{broker_name}</b><br>Time: %{{x}}s<br>GC: %{{y:.3f}}s<extra></extra>'
                        ),
                        row=2, col=1
                    )

        # Update layout
        fig.update_xaxes(title_text="Time (seconds)", row=1, col=1)
        fig.update_xaxes(title_text="Time (seconds)", row=2, col=1)
        fig.update_yaxes(title_text="Throughput (msg/s)", row=1, col=1, secondary_y=False)
        fig.update_yaxes(title_text="JVM Heap (MB)", row=1, col=1, secondary_y=True)
        fig.update_yaxes(title_text="GC Time (seconds)", row=2, col=1)

        fig.update_layout(
            title_text=f"Throughput and Infrastructure Health - {test_name}",
            height=800,
            hovermode='x unified',
            template='plotly_white',
            showlegend=True,
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1
            )
        )

        # Save to HTML
        output_file = self.output_dir / f"{test_name}_throughput_health.html"
        fig.write_html(str(output_file))
        logger.info(f"Generated: {output_file}")

        return output_file

    def generate_latency_chart(
        self,
        omb_results: Dict,
        test_name: str = "test"
    ) -> Path:
        """
        Create interactive latency percentile chart.

        Args:
            omb_results: OMB benchmark results
            test_name: Name of the test

        Returns:
            Path to generated HTML file
        """
        logger.info(f"Generating latency chart for {test_name}")

        fig = go.Figure()

        # Extract latency percentile timeseries
        latency_metrics = {
            'p50': omb_results.get('publishLatency50pct', []),
            'p95': omb_results.get('publishLatency95pct', []),
            'p99': omb_results.get('publishLatency99pct', []),
            'p99.9': omb_results.get('publishLatency999pct', []),
            'max': omb_results.get('publishLatencyMax', [])
        }

        colors = {
            'p50': '#3498db',
            'p95': '#f39c12',
            'p99': '#e74c3c',
            'p99.9': '#9b59b6',
            'max': '#95a5a6'
        }

        for percentile, values in latency_metrics.items():
            if values and len(values) > 0:
                time_points = list(range(0, len(values) * 10, 10))
                fig.add_trace(go.Scatter(
                    x=time_points,
                    y=values,
                    name=f"{percentile}",
                    line=dict(color=colors.get(percentile, '#000000'), width=2),
                    hovertemplate=f'<b>{percentile}</b><br>Time: %{{x}}s<br>Latency: %{{y:.2f}} ms<extra></extra>'
                ))

        fig.update_layout(
            title=f"Publish Latency Percentiles - {test_name}",
            xaxis_title="Time (seconds)",
            yaxis_title="Latency (ms)",
            hovermode='x unified',
            template='plotly_white',
            height=500,
            showlegend=True
        )

        output_file = self.output_dir / f"{test_name}_latency.html"
        fig.write_html(str(output_file))
        logger.info(f"Generated: {output_file}")

        return output_file

    def generate_broker_health_heatmap(
        self,
        health_metrics: Dict,
        test_name: str = "test"
    ) -> Optional[Path]:
        """
        Create health status heatmap for brokers over time.

        Args:
            health_metrics: Infrastructure health timeseries
            test_name: Name of the test

        Returns:
            Path to generated HTML file or None if no data
        """
        if not health_metrics or not health_metrics.get('brokers'):
            logger.warning("No broker health metrics available for heatmap")
            return None

        logger.info(f"Generating broker health heatmap for {test_name}")

        # Prepare data for heatmap
        brokers = list(health_metrics['brokers'].keys())
        timestamps = health_metrics.get('timestamps', [])
        time_labels = [self._format_timestamp(ts) for ts in timestamps]

        # Create health score matrix (based on heap utilization %)
        health_scores = []
        for broker_name in brokers:
            broker_data = health_metrics['brokers'][broker_name]
            heap_used = broker_data.get('heap_used_mb', [])

            # Calculate health scores (0-100, where 100 = healthy, 0 = critical)
            scores = []
            for heap_val in heap_used:
                if heap_val is None:
                    scores.append(None)
                else:
                    # Assume 80% heap is yellow, 90% is red
                    # This is simplified - adjust based on actual max heap
                    if heap_val < 8000:  # < 8GB = healthy
                        scores.append(100)
                    elif heap_val < 10000:  # 8-10GB = warning
                        scores.append(70)
                    else:  # > 10GB = critical
                        scores.append(30)

            health_scores.append(scores)

        fig = go.Figure(data=go.Heatmap(
            z=health_scores,
            x=time_labels,
            y=brokers,
            colorscale=[
                [0, '#e74c3c'],    # Red (critical)
                [0.5, '#f39c12'],  # Orange (warning)
                [1, '#2ecc71']     # Green (healthy)
            ],
            hovertemplate='<b>%{y}</b><br>Time: %{x}<br>Health: %{z}<extra></extra>'
        ))

        fig.update_layout(
            title=f"Broker Health Status - {test_name}",
            xaxis_title="Time",
            yaxis_title="Broker",
            height=max(300, len(brokers) * 50),
            template='plotly_white'
        )

        output_file = self.output_dir / f"{test_name}_broker_heatmap.html"
        fig.write_html(str(output_file))
        logger.info(f"Generated: {output_file}")

        return output_file

    def generate_resource_utilization_chart(
        self,
        health_metrics: Dict,
        component: str = "brokers",
        test_name: str = "test"
    ) -> Optional[Path]:
        """
        Create resource utilization chart (CPU/Memory) for brokers or bookies.

        Args:
            health_metrics: Infrastructure health timeseries
            component: "brokers" or "bookies"
            test_name: Name of the test

        Returns:
            Path to generated HTML file or None if no data
        """
        if not health_metrics or not health_metrics.get(component):
            logger.warning(f"No {component} health metrics available")
            return None

        logger.info(f"Generating resource utilization chart for {component}")

        fig = make_subplots(
            rows=2, cols=1,
            subplot_titles=(f'{component.title()} CPU Usage', f'{component.title()} Memory Usage'),
            vertical_spacing=0.12
        )

        timestamps = health_metrics.get('timestamps', [])
        time_seconds = self._timestamps_to_seconds(timestamps)

        for pod_name, pod_data in health_metrics[component].items():
            # CPU data (convert from "123m" format to numeric)
            cpu_values = [self._parse_cpu(v) for v in pod_data.get('cpu', [])]

            if any(v is not None for v in cpu_values):
                fig.add_trace(
                    go.Scatter(
                        x=time_seconds,
                        y=cpu_values,
                        name=f"{pod_name}",
                        mode='lines',
                        hovertemplate=f'<b>{pod_name}</b><br>Time: %{{x}}s<br>CPU: %{{y:.0f}}m<extra></extra>'
                    ),
                    row=1, col=1
                )

            # Memory data (convert from "456Mi" format to numeric MB)
            memory_values = [self._parse_memory(v) for v in pod_data.get('memory', [])]

            if any(v is not None for v in memory_values):
                fig.add_trace(
                    go.Scatter(
                        x=time_seconds,
                        y=memory_values,
                        name=f"{pod_name}",
                        mode='lines',
                        showlegend=False,
                        hovertemplate=f'<b>{pod_name}</b><br>Time: %{{x}}s<br>Memory: %{{y:.0f}} MiB<extra></extra>'
                    ),
                    row=2, col=1
                )

        fig.update_xaxes(title_text="Time (seconds)", row=1, col=1)
        fig.update_xaxes(title_text="Time (seconds)", row=2, col=1)
        fig.update_yaxes(title_text="CPU (millicores)", row=1, col=1)
        fig.update_yaxes(title_text="Memory (MiB)", row=2, col=1)

        fig.update_layout(
            title_text=f"{component.title()} Resource Utilization - {test_name}",
            height=700,
            hovermode='x unified',
            template='plotly_white'
        )

        output_file = self.output_dir / f"{test_name}_{component}_resources.html"
        fig.write_html(str(output_file))
        logger.info(f"Generated: {output_file}")

        return output_file

    def generate_comparison_chart(
        self,
        test_results: List[Tuple[str, Dict]],
        metric: str = "throughput"
    ) -> Path:
        """
        Create comparison chart across multiple test runs.

        Args:
            test_results: List of (test_name, omb_results) tuples
            metric: Metric to compare ("throughput", "latency_p99", etc.)

        Returns:
            Path to generated HTML file
        """
        logger.info(f"Generating comparison chart for {metric}")

        fig = go.Figure()

        for test_name, results in test_results:
            if metric == "throughput":
                values = results.get('publishRate', [])
                y_label = "Throughput (msg/s)"
            elif metric == "latency_p99":
                values = results.get('publishLatency99pct', [])
                y_label = "P99 Latency (ms)"
            else:
                continue

            if values:
                time_points = list(range(0, len(values) * 10, 10))
                fig.add_trace(go.Scatter(
                    x=time_points,
                    y=values,
                    name=test_name,
                    mode='lines',
                    hovertemplate=f'<b>{test_name}</b><br>Time: %{{x}}s<br>{y_label}: %{{y:.2f}}<extra></extra>'
                ))

        fig.update_layout(
            title=f"Test Comparison - {metric.replace('_', ' ').title()}",
            xaxis_title="Time (seconds)",
            yaxis_title=y_label,
            hovermode='x unified',
            template='plotly_white',
            height=500
        )

        output_file = self.output_dir / f"comparison_{metric}.html"
        fig.write_html(str(output_file))
        logger.info(f"Generated: {output_file}")

        return output_file

    def _timestamps_to_seconds(self, timestamps: List[str]) -> List[float]:
        """Convert ISO timestamps to seconds from start."""
        if not timestamps:
            return []

        try:
            start_time = datetime.fromisoformat(timestamps[0])
            return [(datetime.fromisoformat(ts) - start_time).total_seconds() for ts in timestamps]
        except Exception as e:
            logger.debug(f"Error converting timestamps: {e}")
            return list(range(len(timestamps)))

    def _format_timestamp(self, timestamp: str) -> str:
        """Format timestamp for display."""
        try:
            dt = datetime.fromisoformat(timestamp)
            return dt.strftime("%H:%M:%S")
        except Exception:
            return timestamp

    def _parse_cpu(self, cpu_str: Optional[str]) -> Optional[float]:
        """Parse CPU value from kubectl format (e.g., '123m' -> 123.0)."""
        if cpu_str is None:
            return None
        try:
            if cpu_str.endswith('m'):
                return float(cpu_str[:-1])
            else:
                return float(cpu_str) * 1000
        except (ValueError, AttributeError):
            return None

    def _parse_memory(self, memory_str: Optional[str]) -> Optional[float]:
        """Parse memory value from kubectl format (e.g., '456Mi' -> 456.0)."""
        if memory_str is None:
            return None
        try:
            if memory_str.endswith('Mi'):
                return float(memory_str[:-2])
            elif memory_str.endswith('Gi'):
                return float(memory_str[:-2]) * 1024
            elif memory_str.endswith('Ki'):
                return float(memory_str[:-2]) / 1024
            else:
                return float(memory_str)
        except (ValueError, AttributeError):
            return None


def generate_all_interactive_charts(
    results_file: Path,
    health_metrics_file: Optional[Path],
    output_dir: Path,
    test_name: str = "test"
) -> List[Path]:
    """
    Generate all interactive charts for a test run.

    Args:
        results_file: Path to OMB results JSON
        health_metrics_file: Path to health metrics JSON (optional)
        output_dir: Directory to save charts
        test_name: Name of the test

    Returns:
        List of generated chart file paths
    """
    if not PLOTLY_AVAILABLE:
        logger.warning("plotly not available - skipping interactive chart generation")
        return []

    generator = InteractiveChartGenerator(output_dir)
    generated_charts = []

    # Load OMB results
    try:
        with open(results_file, 'r') as f:
            omb_results = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load OMB results: {e}")
        return []

    # Load health metrics if available
    health_metrics = None
    if health_metrics_file and health_metrics_file.exists():
        try:
            with open(health_metrics_file, 'r') as f:
                health_metrics = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load health metrics: {e}")

    # Generate charts
    try:
        # Main throughput + health chart
        chart = generator.generate_throughput_health_chart(omb_results, health_metrics, test_name)
        generated_charts.append(chart)

        # Latency chart
        chart = generator.generate_latency_chart(omb_results, test_name)
        generated_charts.append(chart)

        if health_metrics:
            # Broker health heatmap
            chart = generator.generate_broker_health_heatmap(health_metrics, test_name)
            if chart:
                generated_charts.append(chart)

            # Broker resource utilization
            chart = generator.generate_resource_utilization_chart(health_metrics, "brokers", test_name)
            if chart:
                generated_charts.append(chart)

            # Bookie resource utilization
            chart = generator.generate_resource_utilization_chart(health_metrics, "bookies", test_name)
            if chart:
                generated_charts.append(chart)

    except Exception as e:
        logger.error(f"Error generating interactive charts: {e}")

    logger.info(f"Generated {len(generated_charts)} interactive charts")
    return generated_charts


if __name__ == '__main__':
    """Standalone script to generate interactive charts from results."""
    import sys

    if len(sys.argv) < 3:
        print("Usage: interactive_charts.py <results.json> <output_dir> [health_metrics.json]")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    results_file = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    health_file = Path(sys.argv[3]) if len(sys.argv) > 3 else None

    test_name = results_file.stem

    charts = generate_all_interactive_charts(results_file, health_file, output_dir, test_name)

    if charts:
        print(f"\n✓ Generated {len(charts)} interactive charts:")
        for chart in charts:
            print(f"  - {chart.name}")
    else:
        print("\n✗ No charts generated")
