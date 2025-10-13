# OpenMessaging Benchmark Docker Image
# Uses Maven 3.9 and Java 17 LTS

FROM maven:3.9-eclipse-temurin-17 AS builder

# Install ca-certificates and update certificate store
RUN apt-get update && \
    apt-get install -y ca-certificates git && \
    update-ca-certificates -f && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Clone OpenMessaging Benchmark repository
RUN git clone https://github.com/openmessaging/benchmark.git .

# Build the benchmark project using Maven
# Use 'install' to create distribution package with all dependencies
RUN mvn clean install -DskipTests

# List the build output to debug the structure
RUN echo "=== Build output structure ===" && \
    find /app -name "*bin.tar.gz" -o -name "benchmark" -type f && \
    echo "=== Package directory ===" && \
    ls -la /app/package/target/ || echo "No package/target" && \
    echo "=== Root target ===" && \
    ls -la /app/target/ || echo "No root target"

# Runtime stage - use JDK (not JRE) to ensure all Java tools are available
FROM eclipse-temurin:17-jdk

# Install runtime dependencies
RUN apt-get update && \
    apt-get install -y ca-certificates curl netcat-openbsd dnsutils && \
    update-ca-certificates -f && \
    rm -rf /var/lib/apt/lists/*

# Copy the entire build directory to inspect it
WORKDIR /app
COPY --from=builder /app /app

# Find and extract the distribution tar.gz
RUN cd /app && \
    DIST_TAR=$(find . -name "*openmessaging-benchmark*bin.tar.gz" | head -1) && \
    if [ -z "$DIST_TAR" ]; then \
        echo "ERROR: Distribution tarball not found" && \
        find /app -name "*.tar.gz" && \
        exit 1; \
    fi && \
    echo "Found distribution: $DIST_TAR" && \
    tar -xzf "$DIST_TAR" --strip-components=1 -C /app && \
    rm -rf /app/benchmark /app/driver-* /app/package /app/pom.xml /app/.git* /app/src 2>/dev/null || true

# Make benchmark script executable
RUN chmod +x /app/bin/benchmark

# Set environment variables
ENV PATH="/app/bin:${PATH}"
ENV JAVA_HOME=/opt/java/openjdk

# Default working directory for running benchmarks
WORKDIR /app

# No ENTRYPOINT - let CMD be the main process
# Default command runs the benchmark help
CMD ["/app/bin/benchmark", "--help"]
