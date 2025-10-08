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

logger = logging.getLogger(__name__)

# Template directory
TEMPLATE_DIR = Path(__file__).parent.parent / "reporting" / "templates"


class ReportGenerator:
    """Generate comprehensive experiment reports"""

    def __init__(self, experiment_dir: Path):
        """Initialize report generator"""
        self.experiment_dir = experiment_dir
        self.env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))

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

        metrics['throughput'][test_name] = {
            'publish_rate': avg_publish_rate,
            'consume_rate': avg_consume_rate
        }

        # Extract latency metrics (in milliseconds)
        metrics['latency'][test_name] = {
            'p50': results.get('publishLatency50pct', 0),
            'p95': results.get('publishLatency95pct', 0),
            'p99': results.get('publishLatency99pct', 0),
            'p999': results.get('publishLatency999pct', 0),
            'max': results.get('publishLatencyMax', 0)
        }

        # Extract error metrics
        # OMB doesn't explicitly track errors in JSON
        metrics['errors'][test_name] = {
            'publish_errors': 0,
            'consume_errors': 0
        }

        return metrics

    def calculate_summary_stats(self, metrics: Dict) -> Dict:
        """Calculate summary statistics across all tests"""
        summary = {
            'total_tests': len(metrics.get('throughput', {})),
            'avg_throughput': 0.0,
            'max_throughput': 0.0,
            'avg_p99_latency': 0.0,
            'total_errors': 0
        }

        # Calculate averages and totals
        throughputs = []
        latencies = []

        for test_name in metrics.get('throughput', {}).keys():
            throughput = metrics['throughput'][test_name].get('publish_rate', 0)
            throughputs.append(throughput)

            latency = metrics['latency'][test_name].get('p99', 0)
            latencies.append(latency)

            summary['total_errors'] += metrics['errors'][test_name].get('publish_errors', 0)
            summary['total_errors'] += metrics['errors'][test_name].get('consume_errors', 0)

        if throughputs:
            summary['avg_throughput'] = sum(throughputs) / len(throughputs)
            summary['max_throughput'] = max(throughputs)

        if latencies:
            summary['avg_p99_latency'] = sum(latencies) / len(latencies)

        return summary

    def generate_html_report(
        self,
        metrics: Dict,
        cost_data: Optional[Dict] = None,
        config: Optional[Dict] = None
    ) -> str:
        """Generate HTML report"""
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
                'publish_rate_msgs_sec': metrics['throughput'][test_name].get('publish_rate', 0),
                'consume_rate_msgs_sec': metrics['throughput'][test_name].get('consume_rate', 0),
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

    def create_report_package(
        self,
        results_files: List[Path],
        cost_data: Optional[Dict] = None,
        config: Optional[Dict] = None,
        include_raw_data: bool = True
    ) -> Path:
        """
        Create complete offline report package

        Args:
            results_files: List of benchmark result files
            cost_data: Cost tracking data
            config: Experiment configuration
            include_raw_data: Include raw benchmark data in package

        Returns:
            Path to report package directory
        """
        logger.info("Creating report package")

        # Create report directory
        report_dir = self.experiment_dir / "report"
        report_dir.mkdir(exist_ok=True)

        # Aggregate metrics from all results
        all_metrics = {
            'throughput': {},
            'latency': {},
            'errors': {}
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

        # Generate HTML report
        html_content = self.generate_html_report(all_metrics, cost_data, config)
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
