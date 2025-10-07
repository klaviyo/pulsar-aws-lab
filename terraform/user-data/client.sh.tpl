#!/bin/bash
set -euo pipefail

# Client User Data Script
# This script configures OpenMessaging Benchmark on client nodes

# Source common functions
source /tmp/common.sh

# Template variables from Terraform
CLUSTER_NAME="${cluster_name}"
OMB_VERSION="${omb_version:-0.0.1-SNAPSHOT}"
PULSAR_VERSION="${pulsar_version:-2.11.0}"

# Constants
readonly OMB_HOME="/opt/openmessaging-benchmark"
readonly PULSAR_HOME="/opt/apache-pulsar-$${PULSAR_VERSION}"
readonly BENCHMARK_USER="pulsar"
readonly WORKLOADS_DIR="/opt/benchmark-workloads"
readonly RESULTS_DIR="/opt/benchmark-results"

log "INFO" "=== Client/Benchmark initialization started ==="
log "INFO" "Cluster: $${CLUSTER_NAME}, OMB Version: $${OMB_VERSION}"

# Get instance metadata
INSTANCE_ID=$(get_instance_metadata "instance-id")
PRIVATE_IP=$(get_instance_metadata "local-ipv4")
AZ=$(get_instance_metadata "placement/availability-zone")
REGION=$(echo "$${AZ}" | sed 's/[a-z]$//')

log "INFO" "Instance: $${INSTANCE_ID}, IP: $${PRIVATE_IP}, AZ: $${AZ}"

# Get ZooKeeper connection string
log "INFO" "Getting ZooKeeper connection string"
ZK_SERVERS=$(retry_command 20 get_zk_connection_string "$${CLUSTER_NAME}")

if [[ -z "$${ZK_SERVERS}" ]]; then
    log "ERROR" "Failed to get ZooKeeper connection string"
    exit 1
fi

log "INFO" "ZooKeeper servers: $${ZK_SERVERS}"

# Wait for ZooKeeper to be ready
log "INFO" "Waiting for ZooKeeper cluster to be ready"
if ! wait_for_zookeeper "$${CLUSTER_NAME}" "300"; then
    log "ERROR" "ZooKeeper cluster not ready"
    exit 1
fi

# Wait for BookKeeper to be ready
log "INFO" "Waiting for BookKeeper cluster to be ready"
if ! wait_for_bookkeeper "$${CLUSTER_NAME}" "3" "300"; then
    log "ERROR" "BookKeeper cluster not ready"
    exit 1
fi

# Get broker URLs
log "INFO" "Getting broker service URLs"
BROKER_URLS=$(retry_command 20 get_broker_urls "$${CLUSTER_NAME}")

if [[ -z "$${BROKER_URLS}" ]]; then
    log "ERROR" "Failed to get broker URLs"
    exit 1
fi

log "INFO" "Broker service URLs: $${BROKER_URLS}"

# Extract first broker for HTTP endpoint
FIRST_BROKER_IP=$(echo "$${BROKER_URLS}" | cut -d',' -f1 | sed 's|pulsar://||' | cut -d':' -f1)
BROKER_HTTP_URL="http://$${FIRST_BROKER_IP}:8080"

log "INFO" "Broker HTTP URL: $${BROKER_HTTP_URL}"

# Wait for at least one broker to be ready
log "INFO" "Waiting for broker to be ready"
if ! wait_for_service "$${FIRST_BROKER_IP}" "6650" "300"; then
    log "ERROR" "Broker not ready"
    exit 1
fi

# Create directories
log "INFO" "Creating benchmark directories"
create_directory "$${OMB_HOME}" "$${BENCHMARK_USER}" "755"
create_directory "$${WORKLOADS_DIR}" "$${BENCHMARK_USER}" "755"
create_directory "$${RESULTS_DIR}" "$${BENCHMARK_USER}" "755"

# Download and setup OpenMessaging Benchmark (if not already present)
if [[ ! -f "$${OMB_HOME}/bin/benchmark" ]]; then
    log "INFO" "OpenMessaging Benchmark not found, setting up"

    # Clone OMB repository
    log "INFO" "Cloning OpenMessaging Benchmark repository"
    cd /tmp
    if [[ -d "openmessaging-benchmark" ]]; then
        rm -rf openmessaging-benchmark
    fi

    git clone https://github.com/openmessaging/benchmark.git openmessaging-benchmark || {
        log "ERROR" "Failed to clone OMB repository"
        exit 1
    }

    cd openmessaging-benchmark

    # Build OMB
    log "INFO" "Building OpenMessaging Benchmark"
    if command -v mvn &> /dev/null; then
        mvn clean package -DskipTests || {
            log "ERROR" "Failed to build OMB"
            exit 1
        }
    else
        log "ERROR" "Maven not found, cannot build OMB"
        exit 1
    fi

    # Install to OMB_HOME
    log "INFO" "Installing OMB to $${OMB_HOME}"
    cp -r benchmark-framework/target/benchmark-framework-*-bin/benchmark-framework-*/* "$${OMB_HOME}/"

    # Set ownership
    chown -R "$${BENCHMARK_USER}:$${BENCHMARK_USER}" "$${OMB_HOME}"

    log "INFO" "OpenMessaging Benchmark installed successfully"
else
    log "INFO" "OpenMessaging Benchmark already installed"
fi

# Generate Pulsar driver configuration
log "INFO" "Generating Pulsar driver configuration"
cat > "$${OMB_HOME}/driver-pulsar/pulsar.yaml" <<EOF
# Pulsar driver configuration for cluster: $${CLUSTER_NAME}
# Generated at: $(date -u +"%Y-%m-%dT%H:%M:%SZ")

name: Pulsar

driverClass: io.openmessaging.benchmark.driver.pulsar.PulsarBenchmarkDriver

# Client configuration
client:
  serviceUrl: $${BROKER_URLS}
  httpUrl: $${BROKER_HTTP_URL}

  # I/O threads
  numIoThreads: 8

  # Connections per broker
  connectionsPerBroker: 4

  # Memory limits
  memoryLimitMB: 512

# Producer configuration
producer:
  batchingEnabled: true
  batchingMaxPublishDelayMs: 10
  blockIfQueueFull: true
  maxPendingMessages: 10000
  maxPendingMessagesAcrossPartitions: 500000

# Consumer configuration
consumer:
  receiverQueueSize: 1000
  subscriptionType: Exclusive

# Topic configuration
topic:
  numberOfPartitions: 3

# Namespace configuration
namespace:
  replicationClusters: []

# Admin configuration
admin:
  serviceHttpUrl: $${BROKER_HTTP_URL}
EOF

chown "$${BENCHMARK_USER}:$${BENCHMARK_USER}" "$${OMB_HOME}/driver-pulsar/pulsar.yaml"

# Create default workload configurations
log "INFO" "Creating default workload configurations"

# Simple throughput workload
cat > "$${WORKLOADS_DIR}/simple-throughput.yaml" <<EOF
# Simple throughput workload
name: simple-throughput

topics: 1
partitionsPerTopic: 3
messageSize: 1024
payloadFile: payload/payload-1Kb.data

subscriptionsPerTopic: 1
consumerPerSubscription: 1
producersPerTopic: 1

producerRate: 10000
consumerBacklogSizeGB: 0
testDurationMinutes: 5

keyDistributor: NO_KEY
EOF

# High throughput workload
cat > "$${WORKLOADS_DIR}/high-throughput.yaml" <<EOF
# High throughput workload
name: high-throughput

topics: 10
partitionsPerTopic: 3
messageSize: 1024
payloadFile: payload/payload-1Kb.data

subscriptionsPerTopic: 1
consumerPerSubscription: 10
producersPerTopic: 10

producerRate: 100000
consumerBacklogSizeGB: 0
testDurationMinutes: 10

keyDistributor: NO_KEY
EOF

# Latency test workload
cat > "$${WORKLOADS_DIR}/latency-test.yaml" <<EOF
# Latency test workload
name: latency-test

topics: 1
partitionsPerTopic: 1
messageSize: 100
payloadFile: payload/payload-100b.data

subscriptionsPerTopic: 1
consumerPerSubscription: 1
producersPerTopic: 1

producerRate: 1000
consumerBacklogSizeGB: 0
testDurationMinutes: 5

keyDistributor: NO_KEY
EOF

# Large message workload
cat > "$${WORKLOADS_DIR}/large-messages.yaml" <<EOF
# Large message workload
name: large-messages

topics: 5
partitionsPerTopic: 3
messageSize: 10240
payloadFile: payload/payload-10Kb.data

subscriptionsPerTopic: 1
consumerPerSubscription: 5
producersPerTopic: 5

producerRate: 5000
consumerBacklogSizeGB: 0
testDurationMinutes: 10

keyDistributor: NO_KEY
EOF

chown -R "$${BENCHMARK_USER}:$${BENCHMARK_USER}" "$${WORKLOADS_DIR}"

# Create benchmark wrapper scripts
log "INFO" "Creating benchmark wrapper scripts"

# Run benchmark script
cat > "$${OMB_HOME}/bin/run-benchmark.sh" <<'EOF'
#!/bin/bash
set -euo pipefail

WORKLOAD="${1:-}"
OUTPUT_DIR="${2:-/opt/benchmark-results}"

if [[ -z "$${WORKLOAD}" ]]; then
    echo "Usage: $0 <workload-file> [output-dir]"
    exit 1
fi

if [[ ! -f "$${WORKLOAD}" ]]; then
    echo "Error: Workload file not found: $${WORKLOAD}"
    exit 1
fi

WORKLOAD_NAME=$(basename "$${WORKLOAD}" .yaml)
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
RESULT_FILE="$${OUTPUT_DIR}/$${WORKLOAD_NAME}-$${TIMESTAMP}.json"

echo "Running benchmark: $${WORKLOAD_NAME}"
echo "Output: $${RESULT_FILE}"

cd /opt/openmessaging-benchmark

./bin/benchmark \
    --drivers driver-pulsar/pulsar.yaml \
    --workers worker-1:localhost \
    --workloads "$${WORKLOAD}" \
    --output "$${RESULT_FILE}"

echo "Benchmark completed: $${RESULT_FILE}"
EOF

chmod +x "$${OMB_HOME}/bin/run-benchmark.sh"

# List workloads script
cat > "$${OMB_HOME}/bin/list-workloads.sh" <<'EOF'
#!/bin/bash
set -euo pipefail

WORKLOADS_DIR="/opt/benchmark-workloads"

echo "Available workloads:"
echo "==================="

for workload in "$${WORKLOADS_DIR}"/*.yaml; do
    if [[ -f "$${workload}" ]]; then
        name=$(basename "$${workload}")
        echo "  - $${name}"

        # Extract key parameters
        if grep -q "producerRate:" "$${workload}"; then
            rate=$(grep "producerRate:" "$${workload}" | awk '{print $2}')
            echo "    Rate: $${rate} msgs/sec"
        fi

        if grep -q "topics:" "$${workload}"; then
            topics=$(grep "topics:" "$${workload}" | awk '{print $2}')
            echo "    Topics: $${topics}"
        fi

        if grep -q "messageSize:" "$${workload}"; then
            size=$(grep "messageSize:" "$${workload}" | awk '{print $2}')
            echo "    Message size: $${size} bytes"
        fi

        echo ""
    fi
done
EOF

chmod +x "$${OMB_HOME}/bin/list-workloads.sh"

# Check cluster script
cat > "$${OMB_HOME}/bin/check-cluster.sh" <<EOF
#!/bin/bash
set -euo pipefail

echo "Checking Pulsar cluster: $${CLUSTER_NAME}"
echo "========================================"

# Check brokers
echo ""
echo "Brokers:"
curl -s "$${BROKER_HTTP_URL}/admin/v2/brokers/$${CLUSTER_NAME}" | python3 -m json.tool || echo "Failed to get brokers"

# Check namespaces
echo ""
echo "Namespaces:"
curl -s "$${BROKER_HTTP_URL}/admin/v2/namespaces/public" | python3 -m json.tool || echo "Failed to get namespaces"

# Check broker stats
echo ""
echo "Broker stats:"
curl -s "$${BROKER_HTTP_URL}/admin/v2/broker-stats/metrics" | head -20 || echo "Failed to get metrics"

echo ""
echo "Cluster check completed"
EOF

chmod +x "$${OMB_HOME}/bin/check-cluster.sh"

chown -R "$${BENCHMARK_USER}:$${BENCHMARK_USER}" "$${OMB_HOME}/bin"

# Create README
cat > "$${OMB_HOME}/README.md" <<EOF
# OpenMessaging Benchmark - Pulsar Cluster: $${CLUSTER_NAME}

## Quick Start

### List available workloads
\`\`\`bash
/opt/openmessaging-benchmark/bin/list-workloads.sh
\`\`\`

### Run a benchmark
\`\`\`bash
/opt/openmessaging-benchmark/bin/run-benchmark.sh /opt/benchmark-workloads/simple-throughput.yaml
\`\`\`

### Check cluster status
\`\`\`bash
/opt/openmessaging-benchmark/bin/check-cluster.sh
\`\`\`

## Results

Results are stored in: $${RESULTS_DIR}

## Configuration

- Driver config: $${OMB_HOME}/driver-pulsar/pulsar.yaml
- Workloads: $${WORKLOADS_DIR}
- Broker URLs: $${BROKER_URLS}
- ZooKeeper: $${ZK_SERVERS}

## Custom Workloads

Create custom workloads in $${WORKLOADS_DIR} following the YAML format.

See: https://github.com/openmessaging/benchmark
EOF

chown "$${BENCHMARK_USER}:$${BENCHMARK_USER}" "$${OMB_HOME}/README.md"

# Verify cluster connectivity
log "INFO" "Verifying cluster connectivity"
sleep 5

if curl -s -f "$${BROKER_HTTP_URL}/metrics" > /dev/null 2>&1; then
    log "INFO" "Successfully connected to broker HTTP endpoint"
else
    log "WARN" "Could not connect to broker HTTP endpoint, but continuing"
fi

# Log final status
log "INFO" "Benchmark configuration:"
log "INFO" "  - OMB Home: $${OMB_HOME}"
log "INFO" "  - Workloads: $${WORKLOADS_DIR}"
log "INFO" "  - Results: $${RESULTS_DIR}"
log "INFO" "  - Broker URLs: $${BROKER_URLS}"
log "INFO" "  - ZooKeeper: $${ZK_SERVERS}"

# Test listing workloads
log "INFO" "Available workloads:"
"$${OMB_HOME}/bin/list-workloads.sh" | tee -a "$${LOG_FILE}" || true

log "INFO" "=== Client/Benchmark initialization completed successfully ==="
exit 0
