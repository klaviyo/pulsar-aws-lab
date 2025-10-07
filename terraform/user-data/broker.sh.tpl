#!/bin/bash
set -euo pipefail

# Broker User Data Script
# This script configures and starts Pulsar Broker on EC2 instances

# Source common functions
source /tmp/common.sh

# Template variables from Terraform
CLUSTER_NAME="${cluster_name}"
BROKER_HEAP_SIZE="${broker_heap_size:-2g}"
BROKER_DIRECT_MEMORY="${broker_direct_memory:-2g}"
PULSAR_VERSION="${pulsar_version:-2.11.0}"

# Constants
readonly PULSAR_HOME="/opt/apache-pulsar-$${PULSAR_VERSION}"
readonly BROKER_CONF_DIR="$${PULSAR_HOME}/conf"
readonly PULSAR_USER="pulsar"

log "INFO" "=== Broker initialization started ==="
log "INFO" "Cluster: $${CLUSTER_NAME}, Heap: $${BROKER_HEAP_SIZE}, Direct Memory: $${BROKER_DIRECT_MEMORY}"

# Get instance metadata
INSTANCE_ID=$(get_instance_metadata "instance-id")
PRIVATE_IP=$(get_instance_metadata "local-ipv4")
AZ=$(get_instance_metadata "placement/availability-zone")
REGION=$(echo "$${AZ}" | sed 's/[a-z]$//')

log "INFO" "Instance: $${INSTANCE_ID}, IP: $${PRIVATE_IP}, AZ: $${AZ}"

# Determine Broker ID
BROKER_ID=$(get_tag_value "BrokerID" 2>/dev/null || echo "")

if [[ -z "$${BROKER_ID}" ]]; then
    # Generate ID from last octet of IP
    BROKER_ID=$(echo "$${PRIVATE_IP}" | awk -F. '{print $4}')
    log "INFO" "Generated Broker ID from IP: $${BROKER_ID}"
else
    log "INFO" "Using Broker ID from tag: $${BROKER_ID}"
fi

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

# Get all broker IPs for cluster configuration
log "INFO" "Discovering broker instances"
BROKER_IPS=$(aws ec2 describe-instances \
    --region "$${REGION}" \
    --filters "Name=tag:Component,Values=broker" \
              "Name=tag:ClusterName,Values=$${CLUSTER_NAME}" \
              "Name=instance-state-name,Values=running" \
    --query 'Reservations[*].Instances[*].PrivateIpAddress' \
    --output text | tr '\t' ',' || echo "$${PRIVATE_IP}")

log "INFO" "Broker IPs: $${BROKER_IPS}"

# Generate broker.conf
log "INFO" "Generating Broker configuration"
cat > "$${BROKER_CONF_DIR}/broker.conf" <<EOF
# Broker configuration for Pulsar cluster: $${CLUSTER_NAME}
# Generated at: $(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Cluster settings
clusterName=$${CLUSTER_NAME}
brokerServicePort=6650
brokerServicePortTls=6651
webServicePort=8080
webServicePortTls=8443

# Broker identity
advertisedAddress=$${PRIVATE_IP}
bindAddress=0.0.0.0

# ZooKeeper settings
zookeeperServers=$${ZK_SERVERS}
configurationStoreServers=$${ZK_SERVERS}
zooKeeperSessionTimeoutMillis=30000
zooKeeperCacheExpirySeconds=300

# BookKeeper settings
bookkeeperClientAuthenticationPlugin=
bookkeeperClientAuthenticationParametersName=
bookkeeperClientAuthenticationParameters=
managedLedgerDefaultEnsembleSize=3
managedLedgerDefaultWriteQuorum=2
managedLedgerDefaultAckQuorum=2
managedLedgerMaxEntriesPerLedger=50000
managedLedgerMinLedgerRolloverTimeMinutes=10
managedLedgerMaxLedgerRolloverTimeMinutes=240

# Storage settings
managedLedgerCacheSizeMB=1024
managedLedgerCacheEvictionWatermark=0.9
managedLedgerDefaultMarkDeleteRateLimit=1.0
managedLedgerOffloadAutoTriggerSizeThresholdBytes=-1

# Message settings
maxMessageSize=5242880
brokerDeleteInactiveTopicsEnabled=false
brokerDeleteInactiveTopicsMaxInactiveDurationSeconds=3600

# Deduplication
brokerDeduplicationEnabled=false

# Load balancer
loadBalancerEnabled=true
loadBalancerPlacementStrategy=weightedRandomSelection
loadBalancerSheddingEnabled=true
loadBalancerSheddingIntervalMinutes=1
loadBalancerSheddingGracePeriodMinutes=5
loadBalancerBrokerMaxTopics=50000
loadBalancerBrokerUnderloadedThresholdPercentage=50
loadBalancerBrokerOverloadedThresholdPercentage=85

# Replication
replicationConnectionsPerBroker=16
replicationProducerQueueSize=1000

# Topic settings
defaultNumberOfNamespaceBundles=4
maxNumPartitionsPerPartitionedTopic=0

# Subscription settings
backlogQuotaDefaultLimitGB=10
backlogQuotaDefaultRetentionPolicy=producer_request_hold

# Message retention
defaultRetentionTimeInMinutes=0
defaultRetentionSizeInMB=0

# Compaction
brokerServiceCompactionMonitorIntervalInSeconds=60

# TLS (disabled for now)
tlsEnabled=false
brokerClientTlsEnabled=false

# Authentication (disabled for now)
authenticationEnabled=false
authorizationEnabled=false

# Web socket
webSocketServiceEnabled=false

# Functions (disabled for testing)
functionsWorkerEnabled=false

# Transactions (disabled for testing)
transactionCoordinatorEnabled=false

# Schema
isSchemaValidationEnforced=false

# Performance settings
numIOThreads=8
numHttpServerThreads=8
numExecutorThreads=16
numCacheExecutorThreads=16

# Metrics
exposeTopicLevelMetricsInPrometheus=true
exposeConsumerLevelMetricsInPrometheus=false
exposeProducerLevelMetricsInPrometheus=false
metricsServletTimeoutMs=10000
statsUpdateFrequencyInSecs=60

# Broker entry metadata
brokerEntryMetadataInterceptors=

# System topic
systemTopicEnabled=true
topicLevelPoliciesEnabled=false

# Protocols
protocolHandlerDirectory=./protocols

# Namespace policies
allowAutoTopicCreation=true
allowAutoTopicCreationType=partitioned
defaultNumPartitions=3

# Broker shutdown
brokerShutdownTimeoutMs=60000

# HTTP
httpMaxRequestSize=65536

# Dispatcher settings
maxConcurrentLookupRequest=50000
maxConcurrentTopicLoadRequest=5000
dispatcherMaxReadBatchSize=100
dispatcherMinReadBatchSize=1
dispatcherMaxRoundRobinBatchSize=20

# Active consumer failover delay
activeConsumerFailoverDelayTimeMillis=1000

# Client library cache
brokerClientTlsEnabledWithKeyStore=false

# Bookkeeper metadata driver
bookkeeperMetadataServiceUri=zk+null://$${ZK_SERVERS}/ledgers

# Acknowledgment at batch index level
acknowledgmentAtBatchIndexLevelEnabled=true
EOF

chown "$${PULSAR_USER}:$${PULSAR_USER}" "$${BROKER_CONF_DIR}/broker.conf"

# Initialize cluster metadata (only on first broker)
if [[ "$${BROKER_ID}" == "1" ]] || [[ "$${PRIVATE_IP}" == $(echo "$${BROKER_IPS}" | cut -d',' -f1) ]]; then
    log "INFO" "This is the first broker, initializing cluster metadata"

    # Wait a bit for BookKeeper to stabilize
    sleep 15

    # Check if cluster already exists
    log "INFO" "Checking if cluster metadata already exists"
    if sudo -u "$${PULSAR_USER}" "$${PULSAR_HOME}/bin/pulsar" initialize-cluster-metadata \
        --cluster "$${CLUSTER_NAME}" \
        --zookeeper "$${ZK_SERVERS}" \
        --configuration-store "$${ZK_SERVERS}" \
        --web-service-url "http://$${PRIVATE_IP}:8080" \
        --broker-service-url "pulsar://$${PRIVATE_IP}:6650" 2>&1 | tee -a "$${LOG_FILE}"; then
        log "INFO" "Cluster metadata initialized successfully"
    else
        log "WARN" "Cluster metadata initialization may have failed, checking if already exists"
        # This might fail if already initialized, which is OK
        sleep 5
    fi
else
    log "INFO" "Not the first broker, skipping cluster initialization"
    # Wait for first broker to initialize
    sleep 30
fi

# Set JVM options
log "INFO" "Configuring JVM: Heap=$${BROKER_HEAP_SIZE}, DirectMemory=$${BROKER_DIRECT_MEMORY}"

# Update pulsar_env.sh
if [[ -f "$${BROKER_CONF_DIR}/pulsar_env.sh" ]]; then
    if grep -q "PULSAR_MEM=" "$${BROKER_CONF_DIR}/pulsar_env.sh"; then
        sed -i "s|PULSAR_MEM=.*|PULSAR_MEM=\"-Xms$${BROKER_HEAP_SIZE} -Xmx$${BROKER_HEAP_SIZE} -XX:MaxDirectMemorySize=$${BROKER_DIRECT_MEMORY} -XX:+UseG1GC\"|" "$${BROKER_CONF_DIR}/pulsar_env.sh"
    else
        echo "PULSAR_MEM=\"-Xms$${BROKER_HEAP_SIZE} -Xmx$${BROKER_HEAP_SIZE} -XX:MaxDirectMemorySize=$${BROKER_DIRECT_MEMORY} -XX:+UseG1GC\"" >> "$${BROKER_CONF_DIR}/pulsar_env.sh"
    fi
fi

# Create systemd service
log "INFO" "Creating Broker systemd service"
cat > /etc/systemd/system/broker.service <<EOF
[Unit]
Description=Apache Pulsar Broker
After=network.target
Documentation=https://pulsar.apache.org/

[Service]
Type=simple
User=$${PULSAR_USER}
Group=$${PULSAR_USER}
Environment="PULSAR_MEM=-Xms$${BROKER_HEAP_SIZE} -Xmx$${BROKER_HEAP_SIZE} -XX:MaxDirectMemorySize=$${BROKER_DIRECT_MEMORY} -XX:+UseG1GC"
Environment="PULSAR_GC=-XX:+UseG1GC -XX:MaxGCPauseMillis=10 -XX:+ParallelRefProcEnabled -XX:+UnlockExperimentalVMOptions -XX:+DoEscapeAnalysis -XX:ParallelGCThreads=4 -XX:ConcGCThreads=4 -XX:G1NewSizePercent=50 -XX:+DisableExplicitGC -XX:-ResizePLAB"
ExecStart=$${PULSAR_HOME}/bin/pulsar broker
Restart=on-failure
RestartSec=10
LimitNOFILE=65536
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd and enable service
systemctl daemon-reload
systemctl enable broker.service

log "INFO" "Starting Broker service"
if ! systemctl start broker.service; then
    log "ERROR" "Failed to start Broker service"
    journalctl -u broker.service -n 50 --no-pager
    exit 1
fi

# Wait for service to be ready
log "INFO" "Waiting for Broker to be ready on port 6650"
if ! wait_for_service "localhost" "6650" "120"; then
    log "ERROR" "Broker service failed to start on port 6650"
    systemctl status broker.service
    journalctl -u broker.service -n 100 --no-pager
    exit 1
fi

# Also check HTTP port
log "INFO" "Waiting for Broker HTTP to be ready on port 8080"
if ! wait_for_service "localhost" "8080" "60"; then
    log "ERROR" "Broker HTTP service failed to start on port 8080"
    systemctl status broker.service
    journalctl -u broker.service -n 100 --no-pager
    exit 1
fi

# Health check via HTTP
sleep 10
log "INFO" "Performing broker health check"
if curl -s -f "http://localhost:8080/metrics" > /dev/null 2>&1; then
    log "INFO" "Broker health check passed"
else
    log "WARN" "Broker health check failed, but service is running"
fi

# Log final status
log "INFO" "Broker service status:"
systemctl status broker.service --no-pager || true

log "INFO" "=== Broker initialization completed successfully ==="
exit 0
