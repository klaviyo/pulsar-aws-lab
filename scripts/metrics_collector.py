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

    def _get_bookie_pod_names(self) -> List[str]:
        """
        Get list of all bookie pod names.

        Returns:
            List of bookie pod names
        """
        result = self.run_command(
            ["kubectl", "get", "pods", "-n", "pulsar",
             "-l", "app=pulsar,component=bookie",
             "-o", "jsonpath={.items[*].metadata.name}"],
            "Get bookie pod names",
            capture_output=True,
            check=False
        )

        if result.returncode == 0:
            return result.stdout.strip().split()
        return []

    def _get_pod_iostat(self, pod_name: str) -> Optional[Dict]:
        """
        Collect iostat metrics from a single pod.

        Args:
            pod_name: Pod name to collect from

        Returns:
            Dictionary with iostat metrics per device, or None if failed
        """
        # Run iostat with 2 samples (skip first which is since boot)
        result = self.run_command(
            ["kubectl", "exec", "-n", "pulsar", pod_name, "--",
             "sh", "-c", "iostat -xm 1 2 2>/dev/null | tail -n +4 || echo 'IOSTAT_NOT_AVAILABLE'"],
            f"Get iostat for {pod_name}",
            capture_output=True,
            check=False,
            timeout=5
        )

        if result.returncode != 0 or 'IOSTAT_NOT_AVAILABLE' in result.stdout:
            logger.debug(f"iostat not available in {pod_name}")
            return None

        return self._parse_iostat_output(result.stdout)

    def _parse_iostat_output(self, output: str) -> Dict[str, Dict]:
        """
        Parse iostat -xm output into structured data.

        Args:
            output: Raw iostat output

        Returns:
            Dictionary mapping device names to metrics
        """
        devices = {}
        lines = output.strip().split('\n')

        for line in lines:
            if not line.strip() or line.startswith('Device') or line.startswith('Linux'):
                continue

            parts = line.split()
            if len(parts) >= 8:
                device = parts[0]
                try:
                    devices[device] = {
                        'reads_per_sec': float(parts[1]),
                        'writes_per_sec': float(parts[2]),
                        'read_mb_per_sec': float(parts[3]),
                        'write_mb_per_sec': float(parts[4]),
                        'await_ms': float(parts[5]),
                        'svctm_ms': float(parts[6]),
                        'util_percent': float(parts[7])
                    }
                except (ValueError, IndexError) as e:
                    logger.debug(f"Error parsing iostat line '{line}': {e}")

        return devices

    def _collect_bookie_iostat_metrics(self, max_workers: int = 10) -> Dict:
        """
        Collect iostat metrics from all bookie pods in parallel.

        Args:
            max_workers: Maximum concurrent kubectl exec operations

        Returns:
            Dictionary with iostat metrics per bookie
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        bookie_pods = self._get_bookie_pod_names()

        if not bookie_pods:
            logger.warning("No bookie pods found")
            return {'available': False}

        logger.debug(f"Collecting iostat from {len(bookie_pods)} bookies in parallel (max_workers={max_workers})")

        iostat_results = {}
        start_time = time.time()

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_pod = {
                executor.submit(self._get_pod_iostat, pod_name): pod_name
                for pod_name in bookie_pods
            }

            # Collect results as they complete
            for future in as_completed(future_to_pod):
                pod_name = future_to_pod[future]
                try:
                    iostat_data = future.result()
                    if iostat_data:
                        iostat_results[pod_name] = iostat_data
                except Exception as e:
                    logger.debug(f"Failed to collect iostat from {pod_name}: {e}")

        elapsed = time.time() - start_time
        logger.debug(f"Collected iostat from {len(iostat_results)}/{len(bookie_pods)} bookies in {elapsed:.2f}s")

        return {
            'available': len(iostat_results) > 0,
            'bookies': iostat_results,
            'collection_time_seconds': round(elapsed, 2)
        }

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
            'bookie_iostat': self._collect_bookie_iostat_metrics(),
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
            'bookie_iostat': self._collect_bookie_iostat_metrics(),
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

            # Collect first snapshot immediately (don't wait for first interval)
            try:
                snapshot_count += 1
                metrics = {
                    'timestamp': datetime.now().isoformat(),
                    'brokers': self._collect_broker_metrics(),
                    'bookies': self._collect_bookie_metrics(),
                    'bookkeeper': self._collect_bookkeeper_metrics(),
                    'disk_io': self._collect_disk_metrics(),
                    'bookie_iostat': self._collect_bookie_iostat_metrics(),
                    'network': self._collect_network_metrics()
                }
                self.collected_metrics.append(metrics)
                logger.info(f"Collected initial metrics snapshot #{snapshot_count}")
            except Exception as e:
                logger.error(f"Error collecting initial metrics: {e}")

            # Continue collecting at intervals until stopped
            while not self.stop_event.wait(timeout=interval_seconds):
                try:
                    snapshot_count += 1
                    metrics = {
                        'timestamp': datetime.now().isoformat(),
                        'brokers': self._collect_broker_metrics(),
                        'bookies': self._collect_bookie_metrics(),
                        'bookkeeper': self._collect_bookkeeper_metrics(),
                        'disk_io': self._collect_disk_metrics(),
                        'bookie_iostat': self._collect_bookie_iostat_metrics(),
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
        # Query broker metrics endpoint (port 8080, JSON format)
        result = self.run_command(
            ["kubectl", "exec", "-n", "pulsar", pod_name, "--",
             "curl", "-s", "-m", "3", "http://localhost:8080/admin/v2/broker-stats/metrics"],
            f"Get JVM metrics for {pod_name}",
            capture_output=True,
            check=False,
            timeout=5
        )

        if result.returncode != 0 or not result.stdout.strip():
            return self._empty_jvm_metrics()

        # Parse JSON-format metrics
        return self._parse_broker_json_metrics(result.stdout)

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

    def _parse_broker_json_metrics(self, json_output: str) -> Dict:
        """
        Parse JVM metrics from Pulsar broker JSON metrics format.

        Args:
            json_output: Raw JSON output from /admin/v2/broker-stats/metrics endpoint

        Returns:
            Dictionary with parsed JVM metrics
        """
        jvm_metrics = self._empty_jvm_metrics()

        try:
            metrics_list = json.loads(json_output)

            # Find the JVM metrics object (has "metric":"jvm_metrics" dimension)
            for metric_obj in metrics_list:
                dimensions = metric_obj.get('dimensions', {})
                if dimensions.get('metric') == 'jvm_metrics':
                    metrics = metric_obj.get('metrics', {})

                    # Extract heap metrics
                    if 'jvm_heap_used' in metrics:
                        heap_used = metrics['jvm_heap_used']
                        jvm_metrics['heap_used_bytes'] = heap_used
                        jvm_metrics['heap_used_mb'] = heap_used / (1024 * 1024)

                    if 'jvm_max_memory' in metrics:
                        heap_max = metrics['jvm_max_memory']
                        jvm_metrics['heap_max_bytes'] = heap_max
                        jvm_metrics['heap_max_mb'] = heap_max / (1024 * 1024)

                    # Extract GC metrics (convert from pause count to seconds)
                    if 'jvm_full_gc_pause' in metrics:
                        # Pause is in units, convert to approximate seconds
                        jvm_metrics['gc_time_seconds'] = metrics['jvm_full_gc_pause']

                    # Extract thread count
                    if 'jvm_thread_cnt' in metrics:
                        jvm_metrics['thread_count'] = int(metrics['jvm_thread_cnt'])

                    break

        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            logger.debug(f"Error parsing broker JSON metrics: {e}")

        return jvm_metrics

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
            'bookies': {},
            'bookkeeper': {
                'bookie_write_latency_p50': [],
                'bookie_write_latency_p99': [],
                'bookie_write_latency_p999': [],
                'broker_addentry_latency': [],
                'journal_sync_latency': []
            },
            'bookie_iostat': {},
            'disk_io_nodes': {},
            'network_pods': {}
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

            # BookKeeper metrics
            bk = snapshot.get('bookkeeper', {})
            if bk.get('available'):
                bk_write = bk.get('bookie_write_latency_ms', {})
                plot_data['bookkeeper']['bookie_write_latency_p50'].append(bk_write.get('p50'))
                plot_data['bookkeeper']['bookie_write_latency_p99'].append(bk_write.get('p99'))
                plot_data['bookkeeper']['bookie_write_latency_p999'].append(bk_write.get('p999'))

                broker_latency = bk.get('broker_addentry_latency_ms', {})
                plot_data['bookkeeper']['broker_addentry_latency'].append(broker_latency.get('avg'))

                journal = bk.get('journal_sync_latency_ms', {})
                plot_data['bookkeeper']['journal_sync_latency'].append(journal.get('avg'))
            else:
                plot_data['bookkeeper']['bookie_write_latency_p50'].append(None)
                plot_data['bookkeeper']['bookie_write_latency_p99'].append(None)
                plot_data['bookkeeper']['bookie_write_latency_p999'].append(None)
                plot_data['bookkeeper']['broker_addentry_latency'].append(None)
                plot_data['bookkeeper']['journal_sync_latency'].append(None)

            # Bookie iostat metrics
            iostat = snapshot.get('bookie_iostat', {})
            if iostat.get('available'):
                for bookie_name, devices in iostat.get('bookies', {}).items():
                    if bookie_name not in plot_data['bookie_iostat']:
                        plot_data['bookie_iostat'][bookie_name] = {}

                    for device_name, device_metrics in devices.items():
                        if device_name not in plot_data['bookie_iostat'][bookie_name]:
                            plot_data['bookie_iostat'][bookie_name][device_name] = {
                                'reads_per_sec': [],
                                'writes_per_sec': [],
                                'read_mb_per_sec': [],
                                'write_mb_per_sec': [],
                                'await_ms': [],
                                'svctm_ms': [],
                                'util_percent': []
                            }

                        plot_data['bookie_iostat'][bookie_name][device_name]['reads_per_sec'].append(device_metrics.get('reads_per_sec'))
                        plot_data['bookie_iostat'][bookie_name][device_name]['writes_per_sec'].append(device_metrics.get('writes_per_sec'))
                        plot_data['bookie_iostat'][bookie_name][device_name]['read_mb_per_sec'].append(device_metrics.get('read_mb_per_sec'))
                        plot_data['bookie_iostat'][bookie_name][device_name]['write_mb_per_sec'].append(device_metrics.get('write_mb_per_sec'))
                        plot_data['bookie_iostat'][bookie_name][device_name]['await_ms'].append(device_metrics.get('await_ms'))
                        plot_data['bookie_iostat'][bookie_name][device_name]['svctm_ms'].append(device_metrics.get('svctm_ms'))
                        plot_data['bookie_iostat'][bookie_name][device_name]['util_percent'].append(device_metrics.get('util_percent'))

            # Disk I/O metrics (from node-exporter)
            disk_io = snapshot.get('disk_io', {})
            if disk_io.get('available'):
                for node_name, devices in disk_io.get('nodes', {}).items():
                    if node_name not in plot_data['disk_io_nodes']:
                        plot_data['disk_io_nodes'][node_name] = {}

                    for device_name, device_metrics in devices.items():
                        if device_name not in plot_data['disk_io_nodes'][node_name]:
                            plot_data['disk_io_nodes'][node_name][device_name] = {
                                'io_time_percent': [],
                                'write_bytes_per_sec': [],
                                'io_operations_now': []
                            }

                        plot_data['disk_io_nodes'][node_name][device_name]['io_time_percent'].append(device_metrics.get('io_time_percent'))
                        plot_data['disk_io_nodes'][node_name][device_name]['write_bytes_per_sec'].append(device_metrics.get('write_bytes_per_sec'))
                        plot_data['disk_io_nodes'][node_name][device_name]['io_operations_now'].append(device_metrics.get('io_operations_now'))

            # Network metrics
            network = snapshot.get('network', {})
            if network.get('available'):
                for pod_name, pod_metrics in network.get('pods', {}).items():
                    if pod_name not in plot_data['network_pods']:
                        plot_data['network_pods'][pod_name] = {
                            'tx_bytes_per_sec': [],
                            'rx_bytes_per_sec': [],
                            'tx_errors_per_sec': []
                        }

                    plot_data['network_pods'][pod_name]['tx_bytes_per_sec'].append(pod_metrics.get('tx_bytes_per_sec'))
                    plot_data['network_pods'][pod_name]['rx_bytes_per_sec'].append(pod_metrics.get('rx_bytes_per_sec'))
                    plot_data['network_pods'][pod_name]['tx_errors_per_sec'].append(pod_metrics.get('tx_errors_per_sec'))

        # Save plot data
        plot_data_file = self.metrics_dir / "plot_data.json"
        with open(plot_data_file, 'w') as f:
            json.dump(plot_data, f, indent=2)

        logger.info(f"Plot data exported to: {plot_data_file}")
        return plot_data
