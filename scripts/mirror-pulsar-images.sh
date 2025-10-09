#!/bin/bash
set -e

# Mirror Apache Pulsar images from DockerHub to AWS ECR
# This script pulls images from DockerHub and pushes them to your ECR repository

ECR_REGISTRY="439508887365.dkr.ecr.us-east-1.amazonaws.com"
AWS_REGION="us-east-1"
PULSAR_VERSION="4.0.7"
PULSAR_MANAGER_VERSION="v0.4.0"

echo "==> Logging into AWS ECR..."
aws ecr get-login-password --region ${AWS_REGION} | docker login --username AWS --password-stdin ${ECR_REGISTRY}

# Create ECR repositories if they don't exist (ignore errors if they already exist)
echo "==> Creating ECR repositories (if they don't exist)..."
aws ecr create-repository --repository-name sre/pulsar-all --region ${AWS_REGION} 2>/dev/null || echo "Repository sre/pulsar-all already exists"
aws ecr create-repository --repository-name sre/pulsar-manager --region ${AWS_REGION} 2>/dev/null || echo "Repository sre/pulsar-manager already exists"

# Mirror pulsar-all image (used by ZooKeeper, BookKeeper, Broker, Proxy, Toolset)
echo ""
echo "==> Mirroring apachepulsar/pulsar-all:${PULSAR_VERSION}..."
docker pull apachepulsar/pulsar-all:${PULSAR_VERSION}
docker tag apachepulsar/pulsar-all:${PULSAR_VERSION} ${ECR_REGISTRY}/sre/pulsar-all:${PULSAR_VERSION}
docker push ${ECR_REGISTRY}/sre/pulsar-all:${PULSAR_VERSION}

# Also tag as latest for convenience
docker tag apachepulsar/pulsar-all:${PULSAR_VERSION} ${ECR_REGISTRY}/sre/pulsar-all:latest
docker push ${ECR_REGISTRY}/sre/pulsar-all:latest

# Mirror pulsar-manager image (if you need it later)
echo ""
echo "==> Mirroring apachepulsar/pulsar-manager:${PULSAR_MANAGER_VERSION}..."
docker pull apachepulsar/pulsar-manager:${PULSAR_MANAGER_VERSION}
docker tag apachepulsar/pulsar-manager:${PULSAR_MANAGER_VERSION} ${ECR_REGISTRY}/sre/pulsar-manager:${PULSAR_MANAGER_VERSION}
docker push ${ECR_REGISTRY}/sre/pulsar-manager:${PULSAR_MANAGER_VERSION}

echo ""
echo "==> ✅ All images mirrored successfully!"
echo ""
echo "Images pushed to ECR:"
echo "  - ${ECR_REGISTRY}/sre/pulsar-all:${PULSAR_VERSION}"
echo "  - ${ECR_REGISTRY}/sre/pulsar-all:latest"
echo "  - ${ECR_REGISTRY}/sre/pulsar-manager:${PULSAR_MANAGER_VERSION}"
echo ""
echo "Next step: Update helm/values.yaml to use these ECR images"
