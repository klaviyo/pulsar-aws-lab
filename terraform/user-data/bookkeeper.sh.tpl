#!/bin/bash
set -euo pipefail

# BookKeeper User Data Script
# This script configures and starts BookKeeper on EC2 instances

# Source common functions
source /tmp/common.sh

# Template variables from Terraform
CLUSTER_NAME="${cluster_name}"
BK_HEAP_SIZE="${bk_heap_size:-1g}"
BK_DIRECT_MEMORY="${bk_direct_memory:-2g}"
PULSAR_VERSION="${pulsar_version:-2.11.0}"
JOURNAL_DISK_DEVICE="${journal_disk_device:-/dev/xvdf}"
LEDGER_DISK_DEVICE="${ledger_disk_device:-/dev/xvdg}"

# Constants
readonly PULSAR_HOME="/opt/apache-pulsar-$${PULSAR_VERSION}"
readonly BK_DATA_DIR="/mnt/bookkeeper"
readonly BK_JOURNAL_DIR="$${BK_DATA_DIR}/journal"
readonly BK_LEDGER_DIR="$${BK_DATA_DIR}/ledgers"
readonly BK_CONF_DIR="$${PULSAR_HOME}/conf"
readonly PULSAR_USER="pulsar"

log "INFO" "=== BookKeeper initialization started ==="
log "INFO" "Cluster: $${CLUSTER_NAME}, Heap: $${BK_HEAP_SIZE}, Direct Memory: $${BK_DIRECT_MEMORY}"

# Get instance metadata
INSTANCE_ID=$(get_instance_metadata "instance-id")
PRIVATE_IP=$(get_instance_metadata "local-ipv4")
AZ=$(get_instance_metadata "placement/availability-zone")

log "INFO" "Instance: $${INSTANCE_ID}, IP: $${PRIVATE_IP}, AZ: $${AZ}"

# Determine BookKeeper ID
BK_ID=$(get_tag_value "BookkeeperID" 2>/dev/null || echo "")

if [[ -z "$${BK_ID}" ]]; then
    # Generate ID from last octet of IP
    BK_ID=$(echo "$${PRIVATE_IP}" | awk -F. '{print $4}')
    log "INFO" "Generated BookKeeper ID from IP: $${BK_ID}"
else
    log "INFO" "Using BookKeeper ID from tag: $${BK_ID}"
fi

# Setup storage
log "INFO" "Setting up BookKeeper storage"

# Function to format and mount disk
setup_disk() {
    local device="$${1}"
    local mount_point="$${2}"

    if [[ ! -b "$${device}" ]]; then
        log "WARN" "Device $${device} not found, skipping"
        return 1
    fi

    log "INFO" "Setting up device: $${device} -> $${mount_point}"

    # Check if device is already formatted
    if ! blkid "$${device}" &>/dev/null; then
        log "INFO" "Formatting $${device} as ext4"
        mkfs.ext4 -F "$${device}"
    else
        log "INFO" "Device $${device} already formatted"
    fi

    # Create mount point
    mkdir -p "$${mount_point}"

    # Check if already mounted
    if ! mountpoint -q "$${mount_point}"; then
        log "INFO" "Mounting $${device} to $${mount_point}"
        mount "$${device}" "$${mount_point}"

        # Add to fstab for persistence
        local uuid
        uuid=$(blkid -s UUID -o value "$${device}")
        if ! grep -q "$${uuid}" /etc/fstab; then
            echo "UUID=$${uuid} $${mount_point} ext4 defaults,nofail 0 2" >> /etc/fstab
            log "INFO" "Added $${device} to /etc/fstab"
        fi
    else
        log "INFO" "Device $${device} already mounted at $${mount_point}"
    fi

    return 0
}

# Try to setup dedicated disks, fallback to main data dir
if setup_disk "$${JOURNAL_DISK_DEVICE}" "$${BK_DATA_DIR}"; then
    JOURNAL_DIR="$${BK_DATA_DIR}/journal"
    LEDGER_DIR="$${BK_DATA_DIR}/ledgers"
    log "INFO" "Using dedicated disk for journal and ledgers"
elif [[ -d "$${BK_DATA_DIR}" ]]; then
    log "INFO" "Using existing data directory: $${BK_DATA_DIR}"
    JOURNAL_DIR="$${BK_JOURNAL_DIR}"
    LEDGER_DIR="$${BK_LEDGER_DIR}"
else
    log "WARN" "No dedicated disk found, using local storage"
    BK_DATA_DIR="/var/lib/bookkeeper"
    JOURNAL_DIR="$${BK_DATA_DIR}/journal"
    LEDGER_DIR="$${BK_DATA_DIR}/ledgers"
fi

# Create BookKeeper directories
log "INFO" "Creating BookKeeper data directories"
create_directory "$${JOURNAL_DIR}" "$${PULSAR_USER}" "755"
create_directory "$${LEDGER_DIR}" "$${PULSAR_USER}" "755"

# Set ownership on parent directory
chown -R "$${PULSAR_USER}:$${PULSAR_USER}" "$${BK_DATA_DIR}"

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

# Generate bookkeeper.conf
log "INFO" "Generating BookKeeper configuration"
cat > "$${BK_CONF_DIR}/bookkeeper.conf" <<EOF
# BookKeeper configuration for Pulsar cluster: $${CLUSTER_NAME}
# Generated at: $(date -u +"%Y-%m-%dT%H:%M:%SZ")

# ZooKeeper settings
zkServers=$${ZK_SERVERS}
zkTimeout=30000
zkLedgersRootPath=/ledgers

# Bookie settings
bookiePort=3181
listeningInterface=$${PRIVATE_IP}
allowLoopback=false

# Journal settings
journalDirectories=$${JOURNAL_DIR}
journalWriteBufferSizeKB=64
journalMaxGroupWaitMSec=1
journalMaxBackups=5
journalFormatVersionToWrite=6
journalSyncData=true

# Ledger storage settings
ledgerDirectories=$${LEDGER_DIR}
ledgerStorageClass=org.apache.bookkeeper.bookie.storage.ldb.DbLedgerStorage
dbStorage_writeCacheMaxSizeMb=512
dbStorage_readAheadCacheMaxSizeMb=256
dbStorage_rocksDB_blockCacheSize=268435456

# Entry log settings
logSizeLimit=1073741824
minorCompactionThreshold=0.2
minorCompactionInterval=3600
majorCompactionThreshold=0.5
majorCompactionInterval=86400
compactionMaxOutstandingRequests=100000

# Performance settings
numAddWorkerThreads=8
numReadWorkerThreads=8
numHighPriorityWorkerThreads=8
maxPendingReadRequestsPerThread=10000
maxPendingAddRequestsPerThread=10000

# Garbage collection
gcWaitTime=300000
gcOverreplicatedLedgerWaitTime=86400000
isForceGCAllowWhenNoSpace=true

# Auto-recovery
autoRecoveryDaemonEnabled=true

# Statistics
statsProviderClass=org.apache.bookkeeper.stats.prometheus.PrometheusMetricsProvider
prometheusStatsHttpPort=8000

# Disk usage
diskUsageThreshold=0.95
diskUsageWarnThreshold=0.90
diskCheckInterval=10000

# Network settings
serverTcpNoDelay=true
clientTcpNoDelay=true

# Replication settings
reppDnsResolverClass=org.apache.pulsar.zookeeper.ZkBookieRackAffinityMapping
ensemblePlacementPolicy=org.apache.bookkeeper.client.RackawareEnsemblePlacementPolicy

# File info
fileInfoFormatVersionToWrite=1

# Metadata service
metadataServiceUri=zk+null://$${ZK_SERVERS}/ledgers

# Extra server components
extraServerComponents=org.apache.bookkeeper.stream.server.StreamStorageLifecycleComponent
EOF

chown "$${PULSAR_USER}:$${PULSAR_USER}" "$${BK_CONF_DIR}/bookkeeper.conf"

# Set JVM options
log "INFO" "Configuring JVM: Heap=$${BK_HEAP_SIZE}, DirectMemory=$${BK_DIRECT_MEMORY}"

# Update pulsar_env.sh
if [[ -f "$${BK_CONF_DIR}/pulsar_env.sh" ]]; then
    if grep -q "BOOKIE_MEM=" "$${BK_CONF_DIR}/pulsar_env.sh"; then
        sed -i "s|BOOKIE_MEM=.*|BOOKIE_MEM=\"-Xms$${BK_HEAP_SIZE} -Xmx$${BK_HEAP_SIZE} -XX:MaxDirectMemorySize=$${BK_DIRECT_MEMORY} -XX:+UseG1GC\"|" "$${BK_CONF_DIR}/pulsar_env.sh"
    else
        echo "BOOKIE_MEM=\"-Xms$${BK_HEAP_SIZE} -Xmx$${BK_HEAP_SIZE} -XX:MaxDirectMemorySize=$${BK_DIRECT_MEMORY} -XX:+UseG1GC\"" >> "$${BK_CONF_DIR}/pulsar_env.sh"
    fi
fi

# Create systemd service
log "INFO" "Creating BookKeeper systemd service"
cat > /etc/systemd/system/bookkeeper.service <<EOF
[Unit]
Description=Apache Pulsar BookKeeper
After=network.target
Documentation=https://pulsar.apache.org/

[Service]
Type=simple
User=$${PULSAR_USER}
Group=$${PULSAR_USER}
Environment="BOOKIE_MEM=-Xms$${BK_HEAP_SIZE} -Xmx$${BK_HEAP_SIZE} -XX:MaxDirectMemorySize=$${BK_DIRECT_MEMORY} -XX:+UseG1GC"
Environment="BOOKIE_GC=-XX:+UseG1GC -XX:MaxGCPauseMillis=10 -XX:+ParallelRefProcEnabled -XX:+UnlockExperimentalVMOptions -XX:+DoEscapeAnalysis -XX:ParallelGCThreads=4 -XX:ConcGCThreads=4 -XX:G1NewSizePercent=50 -XX:+DisableExplicitGC -XX:-ResizePLAB"
ExecStart=$${PULSAR_HOME}/bin/pulsar bookie
Restart=on-failure
RestartSec=10
LimitNOFILE=65536
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Initialize BookKeeper metadata (only on first bookie)
if [[ "$${BK_ID}" == "1" ]]; then
    log "INFO" "This is the first bookie, initializing BookKeeper metadata"

    # Wait a bit more for ZooKeeper to stabilize
    sleep 10

    # Run metadata format
    log "INFO" "Running BookKeeper metaformat"
    if ! sudo -u "$${PULSAR_USER}" "$${PULSAR_HOME}/bin/bookkeeper" shell metaformat -nonInteractive -force 2>&1 | tee -a "$${LOG_FILE}"; then
        log "WARN" "Metaformat may have failed, checking if already formatted"
        # Metaformat might fail if already formatted, which is OK
        if ! echo "ls /" | sudo -u "$${PULSAR_USER}" "$${PULSAR_HOME}/bin/bookkeeper" shell 2>&1 | grep -q "ledgers"; then
            log "ERROR" "BookKeeper metadata not initialized properly"
            exit 1
        else
            log "INFO" "BookKeeper metadata already initialized"
        fi
    fi

    log "INFO" "BookKeeper metadata initialized successfully"
else
    log "INFO" "Not the first bookie (ID=$${BK_ID}), skipping metaformat"

    # Wait for first bookie to initialize metadata
    log "INFO" "Waiting for BookKeeper metadata to be initialized"
    sleep 30
fi

# Reload systemd and enable service
systemctl daemon-reload
systemctl enable bookkeeper.service

log "INFO" "Starting BookKeeper service"
if ! systemctl start bookkeeper.service; then
    log "ERROR" "Failed to start BookKeeper service"
    journalctl -u bookkeeper.service -n 50 --no-pager
    exit 1
fi

# Wait for service to be ready
log "INFO" "Waiting for BookKeeper to be ready on port 3181"
if ! wait_for_service "localhost" "3181" "120"; then
    log "ERROR" "BookKeeper service failed to start"
    systemctl status bookkeeper.service
    journalctl -u bookkeeper.service -n 100 --no-pager
    exit 1
fi

# Additional health check
sleep 10
if ! systemctl is-active --quiet bookkeeper.service; then
    log "ERROR" "BookKeeper service is not active"
    systemctl status bookkeeper.service
    exit 1
fi

# Log final status
log "INFO" "BookKeeper service status:"
systemctl status bookkeeper.service --no-pager || true

log "INFO" "BookKeeper storage:"
df -h "$${BK_DATA_DIR}" || true

log "INFO" "=== BookKeeper initialization completed successfully ==="
exit 0
