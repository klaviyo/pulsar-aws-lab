#!/bin/bash
set -e

BENCHMARK_DIR="/opt/openmessaging-benchmark"
BENCHMARK_REPO="https://github.com/openmessaging/benchmark.git"
BENCHMARK_BRANCH="master"

echo "========================================="
echo "Installing OpenMessaging Benchmark"
echo "========================================="

# Install Maven (OpenMessaging Benchmark requires Maven 3.8.6+)
echo "Checking Maven installation..."

# Try system Maven first
sudo dnf install -y maven

# Check if system Maven meets minimum version requirement
MAVEN_VERSION_OUTPUT=$(mvn --version 2>/dev/null | head -1)
MAVEN_VERSION=$(echo "$MAVEN_VERSION_OUTPUT" | grep -oP 'Apache Maven \K[0-9.]+' || echo "0.0.0")

echo "System Maven version: $MAVEN_VERSION"

# Compare version (simple string comparison works for 3.8.x vs 3.9.x)
REQUIRED_VERSION="3.8.6"
if [[ "$MAVEN_VERSION" < "$REQUIRED_VERSION" ]]; then
    echo "System Maven $MAVEN_VERSION is older than required $REQUIRED_VERSION"
    echo "Installing Maven 3.9.11 manually..."

    MAVEN_BINARY_VERSION="3.9.11"
    MAVEN_HOME="/opt/maven"
    MAVEN_TARBALL="apache-maven-${MAVEN_BINARY_VERSION}-bin.tar.gz"

    # Try multiple mirrors for reliability (dlcdn first as it's the current release)
    MAVEN_MIRRORS=(
        "https://dlcdn.apache.org/maven/maven-3/${MAVEN_BINARY_VERSION}/binaries/${MAVEN_TARBALL}"
        "https://archive.apache.org/dist/maven/maven-3/${MAVEN_BINARY_VERSION}/binaries/${MAVEN_TARBALL}"
    )

    cd /tmp
    DOWNLOAD_SUCCESS=false

    for MIRROR in "${MAVEN_MIRRORS[@]}"; do
        echo "Trying to download from: $MIRROR"
        if wget --timeout=30 --tries=3 "$MIRROR"; then
            DOWNLOAD_SUCCESS=true
            break
        else
            echo "Download failed from $MIRROR, trying next mirror..."
        fi
    done

    if [ "$DOWNLOAD_SUCCESS" = false ]; then
        echo "ERROR: Failed to download Maven from all mirrors"
        exit 1
    fi

    # Verify download
    if [ ! -f "$MAVEN_TARBALL" ]; then
        echo "ERROR: Maven tarball not found after download"
        exit 1
    fi

    # Extract to /opt
    echo "Extracting Maven..."
    sudo tar -xzf "$MAVEN_TARBALL" -C /opt/
    sudo mv /opt/apache-maven-${MAVEN_BINARY_VERSION} ${MAVEN_HOME}

    # Set Maven command to use installed version
    MAVEN_CMD="${MAVEN_HOME}/bin/mvn"

    # Clean up
    rm -f "$MAVEN_TARBALL"

    echo "Installed Maven to ${MAVEN_HOME}"
else
    echo "System Maven $MAVEN_VERSION meets requirements"
    # Use system Maven
    MAVEN_CMD="mvn"
fi

# Verify final Maven version
echo "Final Maven version:"
${MAVEN_CMD} --version

# Clone OpenMessaging Benchmark repository
echo "Cloning OpenMessaging Benchmark from ${BENCHMARK_REPO}..."
if [ -d "${BENCHMARK_DIR}" ]; then
    echo "Removing existing benchmark directory..."
    sudo rm -rf ${BENCHMARK_DIR}
fi

sudo git clone --depth 1 --branch ${BENCHMARK_BRANCH} ${BENCHMARK_REPO} ${BENCHMARK_DIR}

# Set ownership
sudo chown -R root:root ${BENCHMARK_DIR}

# Build the benchmark framework
echo "Building OpenMessaging Benchmark (this may take several minutes)..."
cd ${BENCHMARK_DIR}

# Use Maven with appropriate memory settings for t3.small instances
export MAVEN_OPTS="-Xmx1024m -XX:MaxMetaspaceSize=512m"

sudo -E ${MAVEN_CMD} clean install -DskipTests -q

# Verify build
if [ ! -d "${BENCHMARK_DIR}/benchmark-framework/target" ]; then
    echo "ERROR: Benchmark build failed - target directory not found"
    exit 1
fi

# Create benchmark results directory
echo "Creating benchmark directories..."
sudo mkdir -p /opt/benchmark-results
sudo mkdir -p /opt/benchmark-configs
sudo mkdir -p /opt/benchmark-workloads

sudo chmod 755 /opt/benchmark-results
sudo chmod 755 /opt/benchmark-configs
sudo chmod 755 /opt/benchmark-workloads

# Create convenience wrapper script
echo "Creating benchmark wrapper script..."
cat <<'EOF' | sudo tee /usr/local/bin/run-benchmark
#!/bin/bash
# OpenMessaging Benchmark Wrapper Script
# Usage: run-benchmark <driver-config> <workload-config>

BENCHMARK_HOME="/opt/openmessaging-benchmark"
DRIVER_CONFIG="${1:-/opt/benchmark-configs/pulsar-driver.yaml}"
WORKLOAD_CONFIG="${2:-/opt/benchmark-workloads/default.yaml}"
RESULTS_DIR="/opt/benchmark-results"

if [ ! -f "${DRIVER_CONFIG}" ]; then
    echo "ERROR: Driver config not found: ${DRIVER_CONFIG}"
    exit 1
fi

if [ ! -f "${WORKLOAD_CONFIG}" ]; then
    echo "ERROR: Workload config not found: ${WORKLOAD_CONFIG}"
    exit 1
fi

cd ${BENCHMARK_HOME}

# Run the benchmark
./bin/benchmark \
    --drivers ${DRIVER_CONFIG} \
    --workers ${WORKLOAD_CONFIG} \
    --output ${RESULTS_DIR}
EOF

sudo chmod +x /usr/local/bin/run-benchmark

# Create benchmark info script
cat <<'EOF' | sudo tee /usr/local/bin/benchmark-info
#!/bin/bash
# Display benchmark installation information

echo "OpenMessaging Benchmark Installation"
echo "====================================="
echo "Installation directory: /opt/openmessaging-benchmark"
echo "Results directory: /opt/benchmark-results"
echo "Config directory: /opt/benchmark-configs"
echo "Workload directory: /opt/benchmark-workloads"
echo ""
echo "Available drivers:"
ls -1 /opt/openmessaging-benchmark/driver-*/target/*.jar 2>/dev/null | head -10
echo ""
echo "Usage: run-benchmark <driver-config> <workload-config>"
EOF

sudo chmod +x /usr/local/bin/benchmark-info

# Clean Maven cache to save space
echo "Cleaning Maven cache..."
sudo rm -rf /root/.m2/repository

# Verify installation
echo "Verifying benchmark installation..."
if [ ! -f "${BENCHMARK_DIR}/bin/benchmark" ]; then
    echo "ERROR: Benchmark script not found"
    exit 1
fi

if [ ! -f "${BENCHMARK_DIR}/benchmark-framework/target/openmessaging-benchmark-0.0.1-SNAPSHOT.jar" ]; then
    echo "WARNING: Benchmark JAR not found in expected location"
fi

echo "Benchmark installation info:"
ls -lh ${BENCHMARK_DIR}/bin/
ls -lh ${BENCHMARK_DIR}/benchmark-framework/target/*.jar 2>/dev/null || true

# Create version marker
echo ${BENCHMARK_BRANCH} | sudo tee ${BENCHMARK_DIR}/VERSION

echo "OpenMessaging Benchmark installed successfully!"
echo "Location: ${BENCHMARK_DIR}"
echo "Run 'benchmark-info' for more details"
