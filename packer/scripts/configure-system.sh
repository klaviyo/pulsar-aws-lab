#!/bin/bash
set -e

echo "========================================="
echo "Configuring System Settings"
echo "========================================="

# Update system packages
echo "Installing system utilities..."
sudo dnf install -y \
    wget \
    tar \
    vim \
    htop \
    sysstat \
    net-tools \
    nmap-ncat \
    git \
    unzip \
    jq \
    procps-ng \
    which \
    hostname \
    bind-utils

# Install Java 17 (Amazon Corretto) - LTS version with better performance
echo "Installing Amazon Corretto 17..."
sudo dnf install -y java-17-amazon-corretto-devel

# Find the actual Java installation path
echo "Locating Java installation..."

# The actual directory is java-17-amazon-corretto.x86_64
JAVA_HOME=/usr/lib/jvm/java-17-amazon-corretto.x86_64

echo "Using JAVA_HOME: ${JAVA_HOME}"

# Verify it exists and has java binary
if [ ! -d "${JAVA_HOME}" ]; then
    echo "ERROR: JAVA_HOME directory not found: ${JAVA_HOME}"
    echo "Contents of /usr/lib/jvm/:"
    ls -la /usr/lib/jvm/ || true
    exit 1
fi

if [ ! -f "${JAVA_HOME}/bin/java" ]; then
    echo "ERROR: Java binary not found at ${JAVA_HOME}/bin/java"
    echo "Contents of ${JAVA_HOME}:"
    ls -la "${JAVA_HOME}" || true
    exit 1
fi

echo "Java binary verified at: ${JAVA_HOME}/bin/java"

# Configure Java 17 as default alternative
echo "Configuring Java alternatives..."

# Register and set java alternative
sudo alternatives --install /usr/bin/java java ${JAVA_HOME}/bin/java 1
sudo alternatives --set java ${JAVA_HOME}/bin/java

# Register and set javac alternative
sudo alternatives --install /usr/bin/javac javac ${JAVA_HOME}/bin/javac 1
sudo alternatives --set javac ${JAVA_HOME}/bin/javac

# Verify Java installation using full path
echo "Java version:"
${JAVA_HOME}/bin/java -version

# Also verify the alternatives are working
echo "Verifying alternatives configuration..."
/usr/bin/java -version

# Disable SELinux (if present on Amazon Linux 2023)
echo "Configuring SELinux..."
if [ -f /etc/selinux/config ]; then
    sudo sed -i 's/^SELINUX=.*/SELINUX=disabled/' /etc/selinux/config
    echo "SELinux disabled (requires reboot)"
else
    echo "SELinux not present on this system"
fi

# Set timezone to UTC
echo "Setting timezone to UTC..."
sudo timedatectl set-timezone UTC

# Configure system limits for Pulsar
echo "Configuring system limits..."
cat <<'EOF' | sudo tee /etc/security/limits.d/99-pulsar.conf
* soft nofile 1048576
* hard nofile 1048576
* soft nproc 1048576
* hard nproc 1048576
* soft core unlimited
* hard core unlimited
EOF

# Configure sysctl for network performance
echo "Configuring sysctl settings..."
cat <<'EOF' | sudo tee /etc/sysctl.d/99-pulsar.conf
# Network tuning for Pulsar
net.core.somaxconn = 32768
net.ipv4.tcp_max_syn_backlog = 8192
net.ipv4.ip_local_port_range = 10000 65535
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 30

# Memory settings
vm.max_map_count = 262144
vm.swappiness = 1

# File descriptor limits
fs.file-max = 2097152
EOF

# Create base directories
echo "Creating base directories..."
sudo mkdir -p /var/log/pulsar
sudo mkdir -p /opt/pulsar
sudo mkdir -p /mnt/bookkeeper/journal
sudo mkdir -p /mnt/bookkeeper/ledgers
sudo mkdir -p /opt/benchmark-results
sudo mkdir -p /opt/benchmark-configs

# Set permissions
sudo chmod 755 /var/log/pulsar
sudo chmod 755 /opt/pulsar
sudo chmod 755 /mnt/bookkeeper
sudo chmod 755 /mnt/bookkeeper/journal
sudo chmod 755 /mnt/bookkeeper/ledgers
sudo chmod 755 /opt/benchmark-results
sudo chmod 755 /opt/benchmark-configs

# Create environment setup script (using detected JAVA_HOME)
cat <<EOF | sudo tee /etc/profile.d/pulsar.sh
# Pulsar environment variables
export PULSAR_HOME=/opt/pulsar
export PATH=\$PULSAR_HOME/bin:\$PATH
export JAVA_HOME=${JAVA_HOME}
export PATH=\$JAVA_HOME/bin:\$PATH
EOF

sudo chmod 644 /etc/profile.d/pulsar.sh

echo "System configuration completed successfully!"
