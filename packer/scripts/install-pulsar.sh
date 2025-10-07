#!/bin/bash
set -e

PULSAR_VERSION=${PULSAR_VERSION:-3.0.0}
PULSAR_DOWNLOAD_URL="https://archive.apache.org/dist/pulsar/pulsar-${PULSAR_VERSION}/apache-pulsar-${PULSAR_VERSION}-bin.tar.gz"
PULSAR_HOME="/opt/pulsar"
DOWNLOAD_DIR="/tmp"

echo "========================================="
echo "Installing Apache Pulsar ${PULSAR_VERSION}"
echo "========================================="

# Download Pulsar binary
echo "Downloading Pulsar from ${PULSAR_DOWNLOAD_URL}..."
cd ${DOWNLOAD_DIR}

if [ ! -f "apache-pulsar-${PULSAR_VERSION}-bin.tar.gz" ]; then
    wget --no-verbose --show-progress \
        --timeout=300 \
        --tries=3 \
        "${PULSAR_DOWNLOAD_URL}" \
        -O "apache-pulsar-${PULSAR_VERSION}-bin.tar.gz"
else
    echo "Pulsar tarball already exists, skipping download"
fi

# Verify download
echo "Verifying download..."
if [ ! -f "apache-pulsar-${PULSAR_VERSION}-bin.tar.gz" ]; then
    echo "ERROR: Failed to download Pulsar tarball"
    exit 1
fi

FILE_SIZE=$(stat -c%s "apache-pulsar-${PULSAR_VERSION}-bin.tar.gz")
echo "Downloaded file size: ${FILE_SIZE} bytes"

if [ ${FILE_SIZE} -lt 10000000 ]; then
    echo "ERROR: Downloaded file is too small (likely corrupted)"
    exit 1
fi

# Extract Pulsar
echo "Extracting Pulsar to ${PULSAR_HOME}..."
sudo mkdir -p ${PULSAR_HOME}

sudo tar -xzf "apache-pulsar-${PULSAR_VERSION}-bin.tar.gz" \
    --strip-components=1 \
    -C ${PULSAR_HOME}

# Verify extraction
if [ ! -f "${PULSAR_HOME}/bin/pulsar" ]; then
    echo "ERROR: Pulsar extraction failed - pulsar binary not found"
    exit 1
fi

# Set ownership and permissions
echo "Setting permissions..."
sudo chown -R root:root ${PULSAR_HOME}
sudo chmod -R 755 ${PULSAR_HOME}
sudo chmod +x ${PULSAR_HOME}/bin/*

# Create symlinks for common commands
echo "Creating command symlinks..."
sudo ln -sf ${PULSAR_HOME}/bin/pulsar /usr/local/bin/pulsar
sudo ln -sf ${PULSAR_HOME}/bin/pulsar-admin /usr/local/bin/pulsar-admin
sudo ln -sf ${PULSAR_HOME}/bin/pulsar-client /usr/local/bin/pulsar-client
sudo ln -sf ${PULSAR_HOME}/bin/pulsar-perf /usr/local/bin/pulsar-perf
sudo ln -sf ${PULSAR_HOME}/bin/bookkeeper /usr/local/bin/bookkeeper

# Create configuration backup directory
echo "Creating configuration backup..."
sudo mkdir -p ${PULSAR_HOME}/conf/backup
sudo cp -r ${PULSAR_HOME}/conf/*.conf ${PULSAR_HOME}/conf/backup/ 2>/dev/null || true

# Verify installation
echo "Verifying Pulsar installation..."
if ! ${PULSAR_HOME}/bin/pulsar version > /dev/null 2>&1; then
    echo "WARNING: Pulsar version command returned non-zero exit code"
fi

echo "Installed binaries:"
ls -lh ${PULSAR_HOME}/bin/pulsar*

echo "Configuration files:"
ls -lh ${PULSAR_HOME}/conf/*.conf | head -10

# Create version marker file
echo ${PULSAR_VERSION} | sudo tee ${PULSAR_HOME}/VERSION

echo "Apache Pulsar ${PULSAR_VERSION} installed successfully!"
echo "Installation directory: ${PULSAR_HOME}"
