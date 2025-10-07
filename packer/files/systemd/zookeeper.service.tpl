[Unit]
Description=Apache Pulsar ZooKeeper
Documentation=https://pulsar.apache.org/docs/
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root

# Environment variables
Environment="PULSAR_HOME=/opt/pulsar"
Environment="PULSAR_LOG_DIR=/var/log/pulsar"
Environment="PULSAR_LOG_FILE=zookeeper.log"
Environment="PULSAR_GC_LOG=/var/log/pulsar/zookeeper-gc.log"

# Working directory
WorkingDirectory=/opt/pulsar

# Start command
ExecStart=/opt/pulsar/bin/pulsar zookeeper

# Restart policy
Restart=on-failure
RestartSec=10
TimeoutStopSec=30

# Resource limits
LimitNOFILE=1048576
LimitNPROC=1048576

# Security settings
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/log/pulsar /data/zookeeper

# Process settings
KillMode=mixed
KillSignal=SIGTERM
SendSIGKILL=yes

[Install]
WantedBy=multi-user.target
