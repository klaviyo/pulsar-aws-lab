[Unit]
Description=Apache Pulsar Broker
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
Environment="PULSAR_LOG_FILE=broker.log"
Environment="PULSAR_GC_LOG=/var/log/pulsar/broker-gc.log"

# Working directory
WorkingDirectory=/opt/pulsar

# Start command
ExecStart=/opt/pulsar/bin/pulsar broker

# Restart policy
Restart=on-failure
RestartSec=10
TimeoutStopSec=60

# Resource limits
LimitNOFILE=1048576
LimitNPROC=1048576
LimitCORE=infinity

# Security settings
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/log/pulsar /data/broker

# Process settings
KillMode=mixed
KillSignal=SIGTERM
SendSIGKILL=yes

# Memory management (important for Broker)
# Note: Actual JVM settings should be in broker_env.sh
OOMScoreAdjust=-800

[Install]
WantedBy=multi-user.target
