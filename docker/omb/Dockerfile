# OpenMessaging Benchmark Docker Image
# Uses Maven 3.9 and Java 17 LTS (Java 21 has compatibility issues with OMB dependencies)

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
# Skip tests during build for faster image creation
RUN mvn clean verify -DskipTests

# Runtime stage - smaller final image
FROM eclipse-temurin:17-jre

# Install runtime dependencies
RUN apt-get update && \
    apt-get install -y ca-certificates curl && \
    update-ca-certificates -f && \
    rm -rf /var/lib/apt/lists/*

# Copy built artifacts from builder stage
WORKDIR /app
COPY --from=builder /app /app

# Make benchmark script executable
RUN chmod +x /app/bin/benchmark

# Set environment variables
ENV PATH="/app/bin:${PATH}"

# Default command runs the benchmark
CMD ["benchmark"]

# Entrypoint allows for flexible command execution
ENTRYPOINT ["/bin/bash", "-c"]
