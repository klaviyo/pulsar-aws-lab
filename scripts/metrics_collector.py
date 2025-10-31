#!/usr/bin/env python3
"""
Infrastructure Metrics Collector
Collects broker and bookie health metrics during test execution for comprehensive reporting
"""

import json
import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Callable
from threading import Thread, Event

logger = logging.getLogger(__name__)


class MetricsCollector:
    """Collects infrastructure health metrics from Pulsar brokers and BookKeeper bookies."""

    def __init__(
        self,
        namespace: str,
        experiment_dir: Path,
        run_command_func: Callable,
        prometheus_url: Optional[str] = None
    ):
        """
        Initialize metrics collector.

        Args:
            namespace: Kubernetes namespace where Pulsar is deployed
            experiment_dir: Directory to store collected metrics
            run_command_func: Function to run kubectl commands
            prometheus_url: Prometheus service URL (optional, defaults to kubectl port-forward)
        """
        self.namespace = namespace
        self.experiment_dir = experiment_dir
        self.run_command = run_command_func
        self.prometheus_url = prometheus_url or "http://localhost:9090"

        # Metrics storage directory
        self.metrics_dir = experiment_dir / "metrics"
        self.metrics_dir.mkdir(exist_ok=True)

        # Background collection state
        self.collection_thread: Optional[Thread] = None
        self.stop_event = Event()
        self.collected_metrics: List[Dict] = []

    def collect_baseline_metrics(self) -> Dict:
        """
        Collect baseline metrics before test starts.

        Returns:
            Dictionary with baseline broker and bookie metrics
        """
        logger.info("Collecting baseline infrastructure metrics...")

        baseline = {
            'timestamp': datetime.now().isoformat(),
            'brokers': self._collect_broker_metrics(),
            'bookies': self._collect_bookie_metrics(),
            'cluster_summary': self._collect_cluster_summary()
        }

        # Save baseline snapshot
        baseline_file = self.metrics_dir / "baseline_snapshot.json"
        with open(baseline_file, 'w') as f:
            json.dump(baseline, f, indent=2)

        logger.info(f"Baseline metrics saved to: {baseline_file}")
        return baseline

    def collect_final_metrics(self) -> Dict:
        """
        Collect final metrics after test completes.

        Returns:
            Dictionary with final broker and bookie metrics
        """
        logger.info("Collecting final infrastructure metrics...")

        final = {
            'timestamp': datetime.now().isoformat(),
            'brokers': self._collect_broker_metrics(),
            'bookies': self._collect_bookie_metrics(),
            'cluster_summary': self._collect_cluster_summary()
        }

        # Save final snapshot
        final_file = self.metrics_dir / "final_snapshot.json"
        with open(final_file, 'w') as f:
            json.dump(final, f, indent=2)

        logger.info(f"Final metrics saved to: {final_file}")
        return final

    def start_background_collection(self, interval_seconds: int = 30) -> None:
        """
        Start background thread to collect metrics periodically during test.

        Args:
            interval_seconds: How often to collect metrics (default: 30s)
        """
        if self.collection_thread and self.collection_thread.is_alive():
            logger.warning("Background collection already running")
            return

        logger.info(f"Starting background metrics collection (every {interval_seconds}s)")

        self.stop_event.clear()
        self.collected_metrics = []

        def collection_loop():
            while not self.stop_event.wait(timeout=interval_seconds):
                try:
                    metrics = {
                        'timestamp': datetime.now().isoformat(),
                        'brokers': self._collect_broker_metrics(),
                        'bookies': self._collect_bookie_metrics()
                    }
                    self.collected_metrics.append(metrics)
                    logger.debug(f"Collected metrics snapshot ({len(self.collected_metrics)} total)")
                except Exception as e:
                    logger.error(f"Error collecting background metrics: {e}")

        self.collection_thread = Thread(target=collection_loop, daemon=True)
        self.collection_thread.start()

    def stop_background_collection(self) -> List[Dict]:
        """
        Stop background collection and save timeseries data.

        Returns:
            List of collected metric snapshots
        """
        if not self.collection_thread:
            logger.warning("No background collection running")
            return []

        logger.info("Stopping background metrics collection...")
        self.stop_event.set()
        self.collection_thread.join(timeout=10)

        # Save timeseries data
        if self.collected_metrics:
            timeseries_file = self.metrics_dir / "timeseries.json"
            with open(timeseries_file, 'w') as f:
                json.dump(self.collected_metrics, f, indent=2)
            logger.info(f"Timeseries metrics saved to: {timeseries_file} ({len(self.collected_metrics)} snapshots)")

        return self.collected_metrics

    def _collect_broker_metrics(self) -> List[Dict]:
        """
        Collect metrics from all Pulsar broker pods.

        Returns:
            List of per-broker metric dictionaries
        """
        broker_metrics = []

        # Get all broker pods
        result = self.run_command(
            ["kubectl", "get", "pods", "-n", "pulsar", "-l", "app=pulsar,component=broker", "-o", "json"],
            "Get broker pods",
            capture_output=True,
            check=False
        )

        if result.returncode != 0:
            logger.warning("Failed to get broker pods")
            return broker_metrics

        try:
            pods_data = json.loads(result.stdout)
            pods = pods_data.get('items', [])

            for pod in pods:
                pod_name = pod['metadata']['name']

                # Get pod metrics (CPU, memory)
                pod_metrics = self._get_pod_resource_metrics(pod_name, "pulsar")

                # Get JVM metrics via metrics endpoint
                jvm_metrics = self._get_broker_jvm_metrics(pod_name)

                broker_metrics.append({
                    'pod_name': pod_name,
                    'resources': pod_metrics,
                    'jvm': jvm_metrics
                })

        except Exception as e:
            logger.error(f"Error collecting broker metrics: {e}")

        return broker_metrics

    def _collect_bookie_metrics(self) -> List[Dict]:
        """
        Collect metrics from all BookKeeper bookie pods.

        Returns:
            List of per-bookie metric dictionaries
        """
        bookie_metrics = []

        # Get all bookie pods
        result = self.run_command(
            ["kubectl", "get", "pods", "-n", "pulsar", "-l", "app=pulsar,component=bookie", "-o", "json"],
            "Get bookie pods",
            capture_output=True,
            check=False
        )

        if result.returncode != 0:
            logger.warning("Failed to get bookie pods")
            return bookie_metrics

        try:
            pods_data = json.loads(result.stdout)
            pods = pods_data.get('items', [])

            for pod in pods:
                pod_name = pod['metadata']['name']

                # Get pod metrics (CPU, memory)
                pod_metrics = self._get_pod_resource_metrics(pod_name, "pulsar")

                # Get JVM metrics via metrics endpoint
                jvm_metrics = self._get_bookie_jvm_metrics(pod_name)

                bookie_metrics.append({
                    'pod_name': pod_name,
                    'resources': pod_metrics,
                    'jvm': jvm_metrics
                })

        except Exception as e:
            logger.error(f"Error collecting bookie metrics: {e}")

        return bookie_metrics

    def _get_pod_resource_metrics(self, pod_name: str, namespace: str) -> Dict:
        """
        Get CPU and memory usage for a pod using kubectl top.

        Args:
            pod_name: Name of the pod
            namespace: Kubernetes namespace

        Returns:
            Dictionary with cpu and memory usage
        """
        result = self.run_command(
            ["kubectl", "top", "pod", pod_name, "-n", namespace, "--no-headers"],
            f"Get metrics for {pod_name}",
            capture_output=True,
            check=False
        )

        if result.returncode != 0:
            return {'cpu': None, 'memory': None}

        try:
            # Parse output: "pod-name  123m  456Mi"
            parts = result.stdout.split()
            if len(parts) >= 3:
                cpu = parts[1]  # e.g., "123m"
                memory = parts[2]  # e.g., "456Mi"
                return {
                    'cpu': cpu,
                    'memory': memory
                }
        except Exception as e:
            logger.debug(f"Error parsing pod metrics: {e}")

        return {'cpu': None, 'memory': None}

    def _get_broker_jvm_metrics(self, pod_name: str) -> Dict:
        """
        Get JVM metrics from broker metrics endpoint.

        Args:
            pod_name: Broker pod name

        Returns:
            Dictionary with JVM heap, GC, and thread metrics
        """
        # Query broker metrics endpoint (port 8080)
        result = self.run_command(
            ["kubectl", "exec", "-n", "pulsar", pod_name, "--",
             "curl", "-s", "http://localhost:8080/metrics"],
            f"Get JVM metrics for {pod_name}",
            capture_output=True,
            check=False,
            timeout=5
        )

        if result.returncode != 0:
            return self._empty_jvm_metrics()

        # Parse Prometheus-format metrics
        return self._parse_jvm_metrics(result.stdout)

    def _get_bookie_jvm_metrics(self, pod_name: str) -> Dict:
        """
        Get JVM metrics from bookie metrics endpoint.

        Args:
            pod_name: Bookie pod name

        Returns:
            Dictionary with JVM heap, GC, and thread metrics
        """
        # Query bookie metrics endpoint (port 8000)
        result = self.run_command(
            ["kubectl", "exec", "-n", "pulsar", pod_name, "--",
             "curl", "-s", "http://localhost:8000/metrics"],
            f"Get JVM metrics for {pod_name}",
            capture_output=True,
            check=False,
            timeout=5
        )

        if result.returncode != 0:
            return self._empty_jvm_metrics()

        # Parse Prometheus-format metrics
        return self._parse_jvm_metrics(result.stdout)

    def _parse_jvm_metrics(self, metrics_output: str) -> Dict:
        """
        Parse JVM metrics from Prometheus format output.

        Args:
            metrics_output: Raw metrics output from /metrics endpoint

        Returns:
            Dictionary with parsed JVM metrics
        """
        jvm_metrics = self._empty_jvm_metrics()

        try:
            lines = metrics_output.split('\n')

            for line in lines:
                # Skip comments and empty lines
                if line.startswith('#') or not line.strip():
                    continue

                # Parse metric lines: metric_name{labels} value
                if 'jvm_memory_bytes_used' in line and 'area="heap"' in line:
                    value = float(line.split()[-1])
                    jvm_metrics['heap_used_bytes'] = value
                    jvm_metrics['heap_used_mb'] = value / (1024 * 1024)

                elif 'jvm_memory_bytes_max' in line and 'area="heap"' in line:
                    value = float(line.split()[-1])
                    jvm_metrics['heap_max_bytes'] = value
                    jvm_metrics['heap_max_mb'] = value / (1024 * 1024)

                elif 'jvm_gc_collection_seconds_sum' in line:
                    value = float(line.split()[-1])
                    jvm_metrics['gc_time_seconds'] = value

                elif 'jvm_threads_current' in line:
                    value = float(line.split()[-1])
                    jvm_metrics['thread_count'] = int(value)

        except Exception as e:
            logger.debug(f"Error parsing JVM metrics: {e}")

        return jvm_metrics

    def _empty_jvm_metrics(self) -> Dict:
        """Return empty JVM metrics structure."""
        return {
            'heap_used_bytes': None,
            'heap_used_mb': None,
            'heap_max_bytes': None,
            'heap_max_mb': None,
            'gc_time_seconds': None,
            'thread_count': None
        }

    def _collect_cluster_summary(self) -> Dict:
        """
        Collect high-level cluster summary metrics.

        Returns:
            Dictionary with cluster-wide metrics
        """
        summary = {
            'broker_count': 0,
            'bookie_count': 0,
            'total_topics': 0,
            'total_subscriptions': 0
        }

        try:
            # Count brokers
            result = self.run_command(
                ["kubectl", "get", "pods", "-n", "pulsar", "-l", "component=broker", "-o", "json"],
                "Count brokers",
                capture_output=True,
                check=False
            )
            if result.returncode == 0:
                pods_data = json.loads(result.stdout)
                summary['broker_count'] = len(pods_data.get('items', []))

            # Count bookies
            result = self.run_command(
                ["kubectl", "get", "pods", "-n", "pulsar", "-l", "component=bookie", "-o", "json"],
                "Count bookies",
                capture_output=True,
                check=False
            )
            if result.returncode == 0:
                pods_data = json.loads(result.stdout)
                summary['bookie_count'] = len(pods_data.get('items', []))

        except Exception as e:
            logger.debug(f"Error collecting cluster summary: {e}")

        return summary

    def export_metrics_for_plotting(self) -> Dict:
        """
        Export collected metrics in format optimized for plotting.

        Returns:
            Dictionary with timeseries arrays for each metric type
        """
        if not self.collected_metrics:
            logger.warning("No metrics collected yet")
            return {}

        # Initialize timeseries arrays
        plot_data = {
            'timestamps': [],
            'brokers': {},
            'bookies': {}
        }

        # Extract timeseries data
        for snapshot in self.collected_metrics:
            plot_data['timestamps'].append(snapshot['timestamp'])

            # Broker metrics
            for broker in snapshot.get('brokers', []):
                pod_name = broker['pod_name']
                if pod_name not in plot_data['brokers']:
                    plot_data['brokers'][pod_name] = {
                        'cpu': [],
                        'memory': [],
                        'heap_used_mb': [],
                        'gc_time_seconds': []
                    }

                plot_data['brokers'][pod_name]['cpu'].append(broker['resources'].get('cpu'))
                plot_data['brokers'][pod_name]['memory'].append(broker['resources'].get('memory'))
                plot_data['brokers'][pod_name]['heap_used_mb'].append(broker['jvm'].get('heap_used_mb'))
                plot_data['brokers'][pod_name]['gc_time_seconds'].append(broker['jvm'].get('gc_time_seconds'))

            # Bookie metrics
            for bookie in snapshot.get('bookies', []):
                pod_name = bookie['pod_name']
                if pod_name not in plot_data['bookies']:
                    plot_data['bookies'][pod_name] = {
                        'cpu': [],
                        'memory': [],
                        'heap_used_mb': [],
                        'gc_time_seconds': []
                    }

                plot_data['bookies'][pod_name]['cpu'].append(bookie['resources'].get('cpu'))
                plot_data['bookies'][pod_name]['memory'].append(bookie['resources'].get('memory'))
                plot_data['bookies'][pod_name]['heap_used_mb'].append(bookie['jvm'].get('heap_used_mb'))
                plot_data['bookies'][pod_name]['gc_time_seconds'].append(bookie['jvm'].get('gc_time_seconds'))

        # Save plot data
        plot_data_file = self.metrics_dir / "plot_data.json"
        with open(plot_data_file, 'w') as f:
            json.dump(plot_data, f, indent=2)

        logger.info(f"Plot data exported to: {plot_data_file}")
        return plot_data
