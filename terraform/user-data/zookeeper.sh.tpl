#!/bin/bash
set -euo pipefail

# ZooKeeper User Data Script
# This script configures and starts ZooKeeper on EC2 instances

# Source common functions
source /tmp/common.sh

# Template variables from Terraform
CLUSTER_NAME="${cluster_name}"
ZK_HEAP_SIZE="${zk_heap_size:-512m}"
PULSAR_VERSION="${pulsar_version:-2.11.0}"

# Constants
readonly PULSAR_HOME="/opt/apache-pulsar-$${PULSAR_VERSION}"
readonly ZK_DATA_DIR="/var/lib/zookeeper"
readonly ZK_CONF_DIR="$${PULSAR_HOME}/conf"
readonly PULSAR_USER="pulsar"

log "INFO" "=== ZooKeeper initialization started ==="
log "INFO" "Cluster: $${CLUSTER_NAME}, Heap: $${ZK_HEAP_SIZE}"

# Get instance metadata
INSTANCE_ID=$(get_instance_metadata "instance-id")
PRIVATE_IP=$(get_instance_metadata "local-ipv4")
AZ=$(get_instance_metadata "placement/availability-zone")

log "INFO" "Instance: $${INSTANCE_ID}, IP: $${PRIVATE_IP}, AZ: $${AZ}"

# Determine ZooKeeper ID
# Try to get from tag first, fallback to generating from IP
ZK_ID=$(get_tag_value "ZookeeperID" 2>/dev/null || echo "")

if [[ -z "$${ZK_ID}" ]]; then
    # Generate ID from last octet of IP (1-255)
    ZK_ID=$(echo "$${PRIVATE_IP}" | awk -F. '{print $4}')
    log "INFO" "Generated ZooKeeper ID from IP: $${ZK_ID}"
else
    log "INFO" "Using ZooKeeper ID from tag: $${ZK_ID}"
fi

# Validate ZK_ID
if [[ ! "$${ZK_ID}" =~ ^[0-9]+$ ]] || [[ "$${ZK_ID}" -lt 1 ]] || [[ "$${ZK_ID}" -gt 255 ]]; then
    log "ERROR" "Invalid ZooKeeper ID: $${ZK_ID}"
    exit 1
fi

# Create data directory and set myid
log "INFO" "Creating ZooKeeper data directory: $${ZK_DATA_DIR}"
create_directory "$${ZK_DATA_DIR}" "$${PULSAR_USER}" "755"

echo "$${ZK_ID}" > "$${ZK_DATA_DIR}/myid"
chown "$${PULSAR_USER}:$${PULSAR_USER}" "$${ZK_DATA_DIR}/myid"
log "INFO" "Created myid file with ID: $${ZK_ID}"

# Generate ZooKeeper server list
log "INFO" "Discovering ZooKeeper cluster members"
ZK_SERVERS=$(retry_command 10 generate_zk_servers "$${CLUSTER_NAME}")

if [[ -z "$${ZK_SERVERS}" ]]; then
    log "ERROR" "Failed to generate ZooKeeper server list"
    exit 1
fi

# Generate zoo.cfg
log "INFO" "Generating ZooKeeper configuration"
cat > "$${ZK_CONF_DIR}/zookeeper.conf" <<EOF
# ZooKeeper configuration for Pulsar cluster: $${CLUSTER_NAME}
# Generated at: $(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Basic settings
tickTime=2000
initLimit=10
syncLimit=5
maxClientCnxns=60

# Data directory
dataDir=$${ZK_DATA_DIR}
dataLogDir=$${ZK_DATA_DIR}/datalog

# Client port
clientPort=2181

# Admin server
admin.enableServer=true
admin.serverPort=8080

# Performance tuning
autopurge.snapRetainCount=3
autopurge.purgeInterval=1
maxSessionTimeout=40000

# 4-letter words whitelist
4lw.commands.whitelist=*

# Cluster members
EOF

# Add server entries
IFS=',' read -ra SERVERS <<< "$${ZK_SERVERS}"
for server in "$${SERVERS[@]}"; do
    # Extract IP from format: ip:2888:3888
    SERVER_IP=$(echo "$${server}" | cut -d: -f1)

    # Find ZK ID for this server (last octet of IP)
    SERVER_ID=$(echo "$${SERVER_IP}" | awk -F. '{print $4}')

    echo "server.$${SERVER_ID}=$${server}" >> "$${ZK_CONF_DIR}/zookeeper.conf"
    log "INFO" "Added server: server.$${SERVER_ID}=$${server}"
done

chown "$${PULSAR_USER}:$${PULSAR_USER}" "$${ZK_CONF_DIR}/zookeeper.conf"

# Set JVM heap size
log "INFO" "Configuring JVM heap size: $${ZK_HEAP_SIZE}"
export PULSAR_MEM="-Xms$${ZK_HEAP_SIZE} -Xmx$${ZK_HEAP_SIZE} -XX:+UseG1GC"

# Update pulsar_env.sh if it exists
if [[ -f "$${ZK_CONF_DIR}/pulsar_env.sh" ]]; then
    if grep -q "PULSAR_MEM=" "$${ZK_CONF_DIR}/pulsar_env.sh"; then
        sed -i "s|PULSAR_MEM=.*|PULSAR_MEM=\"-Xms$${ZK_HEAP_SIZE} -Xmx$${ZK_HEAP_SIZE} -XX:+UseG1GC\"|" "$${ZK_CONF_DIR}/pulsar_env.sh"
    else
        echo "PULSAR_MEM=\"-Xms$${ZK_HEAP_SIZE} -Xmx$${ZK_HEAP_SIZE} -XX:+UseG1GC\"" >> "$${ZK_CONF_DIR}/pulsar_env.sh"
    fi
fi

# Create systemd service
log "INFO" "Creating ZooKeeper systemd service"
cat > /etc/systemd/system/zookeeper.service <<EOF
[Unit]
Description=Apache Pulsar ZooKeeper
After=network.target
Documentation=https://pulsar.apache.org/

[Service]
Type=simple
User=$${PULSAR_USER}
Group=$${PULSAR_USER}
Environment="PULSAR_MEM=-Xms$${ZK_HEAP_SIZE} -Xmx$${ZK_HEAP_SIZE} -XX:+UseG1GC"
Environment="PULSAR_GC=-XX:+UseG1GC -XX:MaxGCPauseMillis=10"
ExecStart=$${PULSAR_HOME}/bin/pulsar zookeeper
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
systemctl enable zookeeper.service

log "INFO" "Starting ZooKeeper service"
if ! systemctl start zookeeper.service; then
    log "ERROR" "Failed to start ZooKeeper service"
    journalctl -u zookeeper.service -n 50 --no-pager
    exit 1
fi

# Wait for service to be ready
log "INFO" "Waiting for ZooKeeper to be ready on port 2181"
if ! wait_for_service "localhost" "2181" "120"; then
    log "ERROR" "ZooKeeper service failed to start"
    systemctl status zookeeper.service
    journalctl -u zookeeper.service -n 100 --no-pager
    exit 1
fi

# Verify ZooKeeper is responding
log "INFO" "Verifying ZooKeeper health"
if echo "ruok" | nc -w 5 localhost 2181 | grep -q "imok"; then
    log "INFO" "ZooKeeper health check passed"
else
    log "ERROR" "ZooKeeper health check failed"
    exit 1
fi

# Log final status
log "INFO" "ZooKeeper service status:"
systemctl status zookeeper.service --no-pager || true

log "INFO" "=== ZooKeeper initialization completed successfully ==="
exit 0
