#!/usr/bin/env python3
"""
Report Generation Module
Generates comprehensive HTML reports from benchmark results
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from jinja2 import Environment, FileSystemLoader

# Import chart generation module
try:
    from omb_charts import generate_all_charts
    CHARTS_AVAILABLE = True
except ImportError:
    CHARTS_AVAILABLE = False
    logging.warning("omb_charts module not available - chart generation disabled")

logger = logging.getLogger(__name__)

# Template directory
TEMPLATE_DIR = Path(__file__).parent.parent / "reporting" / "templates"


class ReportGenerator:
    """Generate comprehensive experiment reports"""

    def __init__(self, experiment_dir: Path, experiment_id: Optional[str] = None):
        """Initialize report generator"""
        self.experiment_dir = experiment_dir
        self.experiment_id = experiment_id or experiment_dir.name
        # Only load Jinja2 templates if template directory exists
        if TEMPLATE_DIR.exists():
            self.env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
        else:
            self.env = None
            logger.warning(f"Template directory not found: {TEMPLATE_DIR}")

    def load_benchmark_results(self, results_file: Path) -> Dict:
        """Load benchmark results from JSON file"""
        logger.info(f"Loading benchmark results from {results_file}")

        with open(results_file, 'r') as f:
            return json.load(f)

    def parse_benchmark_metrics(self, results: Dict, test_name: str = "test") -> Dict:
        """
        Parse OpenMessaging Benchmark JSON results.

        Args:
            results: OMB JSON output dictionary
            test_name: Name to use for this test in metrics

        Returns:
            Metrics dictionary with throughput, latency, and error data
        """
        metrics = {
            'throughput': {},
            'latency': {},
            'errors': {}
        }

        # Extract throughput metrics
        # OMB stores rates as arrays of periodic measurements, use average
        publish_rates = results.get('publishRate', [])
        consume_rates = results.get('consumeRate', [])

        avg_publish_rate = sum(publish_rates) / len(publish_rates) if publish_rates else 0
        avg_consume_rate = sum(consume_rates) / len(consume_rates) if consume_rates else 0
        max_publish_rate = max(publish_rates) if publish_rates else 0
        max_consume_rate = max(consume_rates) if consume_rates else 0

        metrics['throughput'][test_name] = {
            'publish_rate': avg_publish_rate,
            'consume_rate': avg_consume_rate,
            'max_publish_rate': max_publish_rate,
            'max_consume_rate': max_consume_rate
        }

        # Extract latency metrics (in milliseconds)
        # Use aggregated values (single numbers) instead of time-series arrays
        metrics['latency'][test_name] = {
            'p50': results.get('aggregatedPublishLatency50pct', 0),
            'p95': results.get('aggregatedPublishLatency95pct', 0),
            'p99': results.get('aggregatedPublishLatency99pct', 0),
            'p999': results.get('aggregatedPublishLatency999pct', 0),
            'max': results.get('aggregatedPublishLatencyMax', 0)
        }

        # Extract error metrics
        # OMB doesn't explicitly track errors in JSON
        metrics['errors'][test_name] = {
            'publish_errors': 0,
            'consume_errors': 0
        }

        return metrics

    def calculate_summary_stats(self, metrics: Dict) -> Dict:
        """
        Calculate summary statistics across all tests.

        Returns:
            Dictionary with:
            - avg_throughput: Overall mean throughput across all test measurements
            - peak_throughput: Highest instantaneous rate achieved in any test
            - avg_p99_latency: Mean p99 latency across all tests
        """
        summary = {
            'total_tests': len(metrics.get('throughput', {})),
            'avg_throughput': 0.0,
            'peak_throughput': 0.0,
            'avg_p99_latency': 0.0,
            'total_errors': 0
        }

        # Collect per-test averages and peaks
        avg_throughputs = []
        peak_throughputs = []
        latencies = []

        for test_name in metrics.get('throughput', {}).keys():
            avg_throughput = metrics['throughput'][test_name].get('publish_rate', 0)
            peak_throughput = metrics['throughput'][test_name].get('max_publish_rate', 0)
            avg_throughputs.append(avg_throughput)
            peak_throughputs.append(peak_throughput)

            latency = metrics['latency'][test_name].get('p99', 0)
            latencies.append(latency)

            summary['total_errors'] += metrics['errors'][test_name].get('publish_errors', 0)
            summary['total_errors'] += metrics['errors'][test_name].get('consume_errors', 0)

        # Overall average throughput: mean of all per-test averages
        if avg_throughputs:
            summary['avg_throughput'] = sum(avg_throughputs) / len(avg_throughputs)

        # Peak throughput: highest instantaneous rate across all tests
        if peak_throughputs:
            summary['peak_throughput'] = max(peak_throughputs)

        if latencies:
            summary['avg_p99_latency'] = sum(latencies) / len(latencies)

        return summary

    def generate_html_report(
        self,
        metrics: Dict,
        cost_data: Optional[Dict] = None,
        config: Optional[Dict] = None,
        charts: Optional[List[Path]] = None,
        grafana_dashboards: Optional[Dict[str, str]] = None
    ) -> str:
        """Generate HTML report using Jinja2 templates"""
        if not self.env:
            raise RuntimeError("Jinja2 templates not available")

        logger.info("Generating HTML report")

        # Calculate summary stats
        summary = self.calculate_summary_stats(metrics)

        # Prepare template context
        context = {
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'experiment_dir': str(self.experiment_dir),
            'summary': summary,
            'metrics': metrics,
            'cost_data': cost_data or {},
            'config': config or {},
            'charts': charts or [],
            'grafana_dashboards': grafana_dashboards or {},
        }

        # Render template
        template = self.env.get_template('report.html')
        return template.render(**context)

    def generate_csv_export(self, metrics: Dict, output_file: Path) -> None:
        """Export metrics to CSV"""
        logger.info(f"Generating CSV export: {output_file}")

        # Convert metrics to DataFrame
        rows = []
        for test_name in metrics.get('throughput', {}).keys():
            row = {
                'test_name': test_name,
                'avg_publish_rate_msgs_sec': metrics['throughput'][test_name].get('publish_rate', 0),
                'max_publish_rate_msgs_sec': metrics['throughput'][test_name].get('max_publish_rate', 0),
                'avg_consume_rate_msgs_sec': metrics['throughput'][test_name].get('consume_rate', 0),
                'max_consume_rate_msgs_sec': metrics['throughput'][test_name].get('max_consume_rate', 0),
                'latency_p50_ms': metrics['latency'][test_name].get('p50', 0),
                'latency_p95_ms': metrics['latency'][test_name].get('p95', 0),
                'latency_p99_ms': metrics['latency'][test_name].get('p99', 0),
                'latency_p999_ms': metrics['latency'][test_name].get('p999', 0),
                'latency_max_ms': metrics['latency'][test_name].get('max', 0),
                'publish_errors': metrics['errors'][test_name].get('publish_errors', 0),
                'consume_errors': metrics['errors'][test_name].get('consume_errors', 0),
            }
            rows.append(row)

        df = pd.DataFrame(rows)
        df.to_csv(output_file, index=False)

        logger.info(f"CSV export complete: {output_file}")

    def generate_json_export(self, metrics: Dict, output_file: Path) -> None:
        """Export metrics to JSON"""
        logger.info(f"Generating JSON export: {output_file}")

        with open(output_file, 'w') as f:
            json.dump(metrics, f, indent=2)

        logger.info(f"JSON export complete: {output_file}")

    def load_workload_configs(self, results_files: List[Path]) -> Dict[str, Dict]:
        """
        Load workload configurations for each test.

        Args:
            results_files: List of benchmark result files

        Returns:
            Dictionary mapping test names to workload configurations
        """
        workload_configs = {}

        for results_file in results_files:
            test_name = results_file.stem
            # Look for workload config file
            workload_file = results_file.parent / f"{test_name}_workload.json"

            if workload_file.exists():
                try:
                    with open(workload_file, 'r') as f:
                        config = json.load(f)
                        workload_configs[test_name] = config
                        logger.info(f"Loaded workload config for {test_name}")
                except Exception as e:
                    logger.warning(f"Failed to load workload config for {test_name}: {e}")

        return workload_configs

    def create_report_package(
        self,
        results_files: List[Path],
        cost_data: Optional[Dict] = None,
        config: Optional[Dict] = None,
        include_raw_data: bool = True,
        grafana_dashboards: Optional[Dict[str, str]] = None
    ) -> Path:
        """
        Create complete offline report package

        Args:
            results_files: List of benchmark result files
            cost_data: Cost tracking data
            config: Experiment configuration
            include_raw_data: Include raw benchmark data in package
            grafana_dashboards: Dict of dashboard names to URLs

        Returns:
            Path to report package directory
        """
        logger.info("Creating report package")

        # Create report directory
        report_dir = self.experiment_dir / "report"
        report_dir.mkdir(exist_ok=True)

        # Load workload configurations
        workload_configs = self.load_workload_configs(results_files)

        # Aggregate metrics from all results
        all_metrics = {
            'throughput': {},
            'latency': {},
            'errors': {},
            'workload_configs': workload_configs
        }

        for results_file in results_files:
            test_name = results_file.stem  # Filename without extension
            results = self.load_benchmark_results(results_file)
            metrics = self.parse_benchmark_metrics(results, test_name=test_name)

            # Merge metrics
            for metric_type in ['throughput', 'latency', 'errors']:
                all_metrics[metric_type].update(metrics[metric_type])

            # Copy raw data if requested
            if include_raw_data:
                raw_dir = report_dir / "raw_data"
                raw_dir.mkdir(exist_ok=True)
                import shutil
                shutil.copy(results_file, raw_dir / results_file.name)

        # Generate charts from results
        charts = []
        if CHARTS_AVAILABLE and results_files:
            try:
                charts_dir = report_dir / "charts"
                logger.info(f"Generating charts from {len(results_files)} result file(s)...")
                generated_charts = generate_all_charts(results_files, charts_dir)

                # Convert absolute paths to relative paths for HTML embedding
                charts = [chart.relative_to(report_dir) for chart in generated_charts]
                logger.info(f"Generated {len(charts)} chart(s)")
            except Exception as e:
                logger.error(f"Chart generation failed: {e}")
                charts = []

        # Generate HTML report
        html_content = self.generate_html_report(
            all_metrics,
            cost_data,
            config,
            charts=charts,
            grafana_dashboards=grafana_dashboards
        )
        html_file = report_dir / "index.html"
        with open(html_file, 'w') as f:
            f.write(html_content)

        # Generate CSV export
        self.generate_csv_export(all_metrics, report_dir / "metrics.csv")

        # Generate JSON export
        self.generate_json_export(all_metrics, report_dir / "metrics.json")

        # Copy configuration files
        config_dir = report_dir / "config"
        config_dir.mkdir(exist_ok=True)
        if config:
            with open(config_dir / "experiment_config.json", 'w') as f:
                json.dump(config, f, indent=2)

        # Add cost data
        if cost_data:
            with open(report_dir / "costs.json", 'w') as f:
                json.dump(cost_data, f, indent=2)

        logger.info(f"Report package created: {report_dir}")
        return report_dir


if __name__ == "__main__":
    # Example usage
    import sys

    if len(sys.argv) < 3:
        print("Usage: report_generator.py <experiment_dir> <results_file>")
        sys.exit(1)

    experiment_dir = Path(sys.argv[1])
    results_file = Path(sys.argv[2])

    generator = ReportGenerator(experiment_dir)
    results = generator.load_benchmark_results(results_file)
    metrics = generator.parse_benchmark_metrics(results)

    html = generator.generate_html_report(metrics)
    output_file = experiment_dir / "report.html"

    with open(output_file, 'w') as f:
        f.write(html)

    print(f"Report generated: {output_file}")
