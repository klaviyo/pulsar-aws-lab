[Unit]
Description=Apache Pulsar BookKeeper (Bookie)
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
Environment="PULSAR_LOG_FILE=bookkeeper.log"
Environment="PULSAR_GC_LOG=/var/log/pulsar/bookkeeper-gc.log"

# Working directory
WorkingDirectory=/opt/pulsar

# Start command
ExecStart=/opt/pulsar/bin/pulsar bookie

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
ReadWritePaths=/var/log/pulsar /mnt/bookkeeper /data/bookkeeper

# Process settings
KillMode=mixed
KillSignal=SIGTERM
SendSIGKILL=yes

# Memory management (important for BookKeeper)
# Note: Actual JVM settings should be in bookkeeper_env.sh
OOMScoreAdjust=-900

[Install]
WantedBy=multi-user.target
