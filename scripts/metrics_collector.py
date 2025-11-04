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

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    logger.warning("requests library not available - Prometheus integration disabled")

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
            prometheus_url: Prometheus service URL (optional, auto-detects if not provided)
        """
        self.namespace = namespace
        self.experiment_dir = experiment_dir
        self.run_command = run_command_func

        # Detect or use provided Prometheus URL
        self.prometheus_url = prometheus_url or self._detect_prometheus_endpoint()
        self.prometheus_available = self.prometheus_url is not None and REQUESTS_AVAILABLE

        if self.prometheus_available:
            logger.info(f"Prometheus integration enabled: {self.prometheus_url}")
        else:
            logger.warning("Prometheus integration disabled - limited metrics available")

        # Metrics storage directory
        self.metrics_dir = experiment_dir / "metrics"
        self.metrics_dir.mkdir(exist_ok=True)

        # Background collection state
        self.collection_thread: Optional[Thread] = None
        self.stop_event = Event()
        self.collected_metrics: List[Dict] = []

    def _detect_prometheus_endpoint(self) -> Optional[str]:
        """
        Auto-detect Prometheus service endpoint.

        Returns:
            Prometheus URL if found, None otherwise
        """
        # Try common Prometheus service locations
        potential_services = [
            ("monitoring", "prometheus-server"),
            ("pulsar", "prometheus"),
            ("default", "prometheus"),
        ]

        for ns, svc_name in potential_services:
            try:
                result = self.run_command(
                    ["kubectl", "get", "svc", "-n", ns, svc_name, "-o", "json"],
                    f"Check for Prometheus in {ns}/{svc_name}",
                    capture_output=True,
                    check=False
                )

                if result.returncode == 0:
                    logger.info(f"Found Prometheus service: {ns}/{svc_name}")
                    # Use port 80 as default HTTP port
                    return f"http://{svc_name}.{ns}.svc.cluster.local:80"
            except Exception as e:
                logger.debug(f"Error checking {ns}/{svc_name}: {e}")

        # Try localhost (for port-forwarded Prometheus)
        try:
            if REQUESTS_AVAILABLE:
                response = requests.get("http://localhost:9090/api/v1/status/config", timeout=1)
                if response.status_code == 200:
                    logger.info("Found Prometheus on localhost:9090")
                    return "http://localhost:9090"
        except Exception:
            pass

        logger.warning("Could not auto-detect Prometheus endpoint")
        return None

    def _query_prometheus(self, query: str, time_param: Optional[str] = None) -> List[Dict]:
        """
        Execute Prometheus query and return results.

        Args:
            query: PromQL query string
            time_param: Optional timestamp for query (default: current time)

        Returns:
            List of result dictionaries from Prometheus
        """
        if not self.prometheus_available:
            return []

        try:
            params = {'query': query}
            if time_param:
                params['time'] = time_param

            response = requests.get(
                f"{self.prometheus_url}/api/v1/query",
                params=params,
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'success':
                    return data.get('data', {}).get('result', [])
                else:
                    logger.warning(f"Prometheus query failed: {data.get('error')}")
            else:
                logger.warning(f"Prometheus HTTP error: {response.status_code}")
        except Exception as e:
            logger.debug(f"Error querying Prometheus: {e}")

        return []

    def _collect_bookkeeper_metrics(self) -> Dict:
        """
        Collect BookKeeper write latency metrics from Prometheus.

        Returns:
            Dictionary with BookKeeper performance metrics
        """
        bk_metrics = {'available': self.prometheus_available}

        if not self.prometheus_available:
            return bk_metrics

        try:
            # Bookie write latency (from bookie perspective)
            bookie_write_p50 = self._query_prometheus(
                'histogram_quantile(0.50, rate(bookie_SERVER_ADD_ENTRY_bucket[1m]))'
            )
            bookie_write_p99 = self._query_prometheus(
                'histogram_quantile(0.99, rate(bookie_SERVER_ADD_ENTRY_bucket[1m]))'
            )
            bookie_write_p999 = self._query_prometheus(
                'histogram_quantile(0.999, rate(bookie_SERVER_ADD_ENTRY_bucket[1m]))'
            )

            # Broker → BookKeeper latency (from broker perspective)
            broker_addentry_latency = self._query_prometheus(
                'pulsar_managedLedger_addEntryLatency'
            )

            # Journal sync latency
            journal_sync = self._query_prometheus(
                'bookie_journal_JOURNAL_SYNC_latency'
            )

            # Parse results
            bk_metrics['bookie_write_latency_ms'] = {
                'p50': float(bookie_write_p50[0]['value'][1]) * 1000 if bookie_write_p50 else None,
                'p99': float(bookie_write_p99[0]['value'][1]) * 1000 if bookie_write_p99 else None,
                'p999': float(bookie_write_p999[0]['value'][1]) * 1000 if bookie_write_p999 else None
            }

            bk_metrics['broker_addentry_latency_ms'] = {
                'avg': float(broker_addentry_latency[0]['value'][1]) if broker_addentry_latency else None
            }

            bk_metrics['journal_sync_latency_ms'] = {
                'avg': float(journal_sync[0]['value'][1]) * 1000 if journal_sync else None
            }

        except Exception as e:
            logger.debug(f"Error collecting BookKeeper metrics: {e}")

        return bk_metrics

    def _collect_disk_metrics(self) -> Dict:
        """
        Collect disk I/O metrics from node-exporter (if available).

        Returns:
            Dictionary with disk I/O metrics per bookie node
        """
        disk_metrics = {'available': self.prometheus_available}

        if not self.prometheus_available:
            return disk_metrics

        try:
            # Disk I/O time percentage (how busy the disk is)
            io_time_query = 'rate(node_disk_io_time_seconds_total{job="node-exporter"}[1m]) * 100'
            io_time_results = self._query_prometheus(io_time_query)

            # Disk write throughput
            write_bytes_query = 'rate(node_disk_written_bytes_total{job="node-exporter"}[1m])'
            write_bytes_results = self._query_prometheus(write_bytes_query)

            # Current pending I/O operations
            io_now_query = 'node_disk_io_now{job="node-exporter"}'
            io_now_results = self._query_prometheus(io_now_query)

            # Organize by node/device
            nodes = {}
            for result in io_time_results:
                node = result['metric'].get('instance', 'unknown')
                device = result['metric'].get('device', 'unknown')
                if node not in nodes:
                    nodes[node] = {}
                if device not in nodes[node]:
                    nodes[node][device] = {}
                nodes[node][device]['io_time_percent'] = float(result['value'][1])

            for result in write_bytes_results:
                node = result['metric'].get('instance', 'unknown')
                device = result['metric'].get('device', 'unknown')
                if node in nodes and device in nodes[node]:
                    nodes[node][device]['write_bytes_per_sec'] = float(result['value'][1])

            for result in io_now_results:
                node = result['metric'].get('instance', 'unknown')
                device = result['metric'].get('device', 'unknown')
                if node in nodes and device in nodes[node]:
                    nodes[node][device]['io_operations_now'] = int(float(result['value'][1]))

            disk_metrics['nodes'] = nodes
            disk_metrics['available'] = len(nodes) > 0

        except Exception as e:
            logger.debug(f"Error collecting disk metrics: {e}")
            disk_metrics['available'] = False

        return disk_metrics

    def _collect_network_metrics(self) -> Dict:
        """
        Collect network metrics from cAdvisor (if available).

        Returns:
            Dictionary with network metrics per pod
        """
        net_metrics = {'available': self.prometheus_available}

        if not self.prometheus_available:
            return net_metrics

        try:
            # Network transmit rate (bytes/sec)
            tx_bytes_query = 'rate(container_network_transmit_bytes_total{namespace="pulsar"}[1m])'
            tx_bytes_results = self._query_prometheus(tx_bytes_query)

            # Network receive rate (bytes/sec)
            rx_bytes_query = 'rate(container_network_receive_bytes_total{namespace="pulsar"}[1m])'
            rx_bytes_results = self._query_prometheus(rx_bytes_query)

            # Transmission errors
            tx_errors_query = 'rate(container_network_transmit_errors_total{namespace="pulsar"}[1m])'
            tx_errors_results = self._query_prometheus(tx_errors_query)

            # Organize by pod
            pods = {}
            for result in tx_bytes_results:
                pod = result['metric'].get('pod', 'unknown')
                if pod not in pods:
                    pods[pod] = {}
                pods[pod]['tx_bytes_per_sec'] = float(result['value'][1])

            for result in rx_bytes_results:
                pod = result['metric'].get('pod', 'unknown')
                if pod in pods:
                    pods[pod]['rx_bytes_per_sec'] = float(result['value'][1])

            for result in tx_errors_results:
                pod = result['metric'].get('pod', 'unknown')
                if pod in pods:
                    pods[pod]['tx_errors_per_sec'] = float(result['value'][1])

            net_metrics['pods'] = pods
            net_metrics['available'] = len(pods) > 0

        except Exception as e:
            logger.debug(f"Error collecting network metrics: {e}")
            net_metrics['available'] = False

        return net_metrics

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
            'bookkeeper': self._collect_bookkeeper_metrics(),
            'disk_io': self._collect_disk_metrics(),
            'network': self._collect_network_metrics(),
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
            'bookkeeper': self._collect_bookkeeper_metrics(),
            'disk_io': self._collect_disk_metrics(),
            'network': self._collect_network_metrics(),
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
            snapshot_count = 0
            while not self.stop_event.wait(timeout=interval_seconds):
                try:
                    snapshot_count += 1
                    metrics = {
                        'timestamp': datetime.now().isoformat(),
                        'brokers': self._collect_broker_metrics(),
                        'bookies': self._collect_bookie_metrics(),
                        'bookkeeper': self._collect_bookkeeper_metrics(),
                        'disk_io': self._collect_disk_metrics(),
                        'network': self._collect_network_metrics()
                    }
                    self.collected_metrics.append(metrics)
                    logger.debug(f"Collected metrics snapshot #{snapshot_count} ({len(self.collected_metrics)} total)")
                except Exception as e:
                    logger.error(f"Error collecting background metrics: {e}")

            logger.info(f"Background collection loop ended. Total snapshots: {snapshot_count}")

        # Use non-daemon thread to ensure collection completes
        self.collection_thread = Thread(target=collection_loop, daemon=False)
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
