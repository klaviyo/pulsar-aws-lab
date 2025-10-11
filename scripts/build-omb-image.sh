#!/usr/bin/env bash
#
# Build and optionally push OpenMessaging Benchmark Docker image
#
# Usage:
#   ./build-omb-image.sh [OPTIONS]
#
# Options:
#   --push              Push image to ECR after build
#   --registry REPO     Docker registry/repository (default: ECR sre/pulsar-omb)
#   --tag TAG           Additional image tag (timestamp tag is always added)
#   --platform PLATFORM Build for specific platform (default: linux/amd64)
#

set -euo pipefail

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DOCKERFILE_DIR="$PROJECT_ROOT/docker/omb"

# Default values
PUSH=false
REGISTRY="439508887365.dkr.ecr.us-east-1.amazonaws.com/sre/pulsar-omb"
AWS_REGION="us-east-1"
TAG=""
PLATFORM="linux/amd64"  # Default to x86_64 for AWS compatibility

# Generate timestamp tag: YYYYMMDDHHmm
TIMESTAMP_TAG=$(date -u +"%Y%m%d%H%M")

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --push)
            PUSH=true
            shift
            ;;
        --registry)
            REGISTRY="$2"
            shift 2
            ;;
        --tag)
            TAG="$2"
            shift 2
            ;;
        --platform)
            PLATFORM="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --push              Push image to ECR after build"
            echo "  --registry REPO     Docker registry/repository"
            echo "                      (default: 439508887365.dkr.ecr.us-east-1.amazonaws.com/sre/pulsar-omb)"
            echo "  --tag TAG           Additional image tag (timestamp tag is always created)"
            echo "  --platform PLATFORM Build for specific platform (default: linux/amd64)"
            echo "  -h, --help          Show this help message"
            echo ""
            echo "Image is always tagged with timestamp (YYYYMMDDHHmm) and 'latest'"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Run '$0 --help' for usage information"
            exit 1
            ;;
    esac
done

# Build the image with timestamp tag
TIMESTAMP_IMAGE="${REGISTRY}:${TIMESTAMP_TAG}"
LATEST_IMAGE="${REGISTRY}:latest"

echo "Building Docker image with tags:"
echo "  - ${TIMESTAMP_TAG} (timestamp)"
echo "  - latest"
if [ -n "$TAG" ]; then
    echo "  - ${TAG} (custom)"
fi
echo ""
echo "Dockerfile location: ${DOCKERFILE_DIR}"
echo ""

BUILD_CMD="docker build -t ${TIMESTAMP_IMAGE} -t ${LATEST_IMAGE}"

if [ -n "$TAG" ]; then
    CUSTOM_IMAGE="${REGISTRY}:${TAG}"
    BUILD_CMD="${BUILD_CMD} -t ${CUSTOM_IMAGE}"
fi

if [ -n "$PLATFORM" ]; then
    BUILD_CMD="${BUILD_CMD} --platform ${PLATFORM}"
fi

BUILD_CMD="${BUILD_CMD} ${DOCKERFILE_DIR}"

echo "Running: ${BUILD_CMD}"
eval "${BUILD_CMD}"

echo "✓ Successfully built images"

# Push if requested
if [ "$PUSH" = true ]; then
    echo ""
    echo "Logging in to ECR..."
    aws ecr get-login-password --region ${AWS_REGION} | docker login --username AWS --password-stdin ${REGISTRY%%/*}

    echo ""
    echo "Pushing images to ECR..."

    echo "  Pushing ${TIMESTAMP_TAG}..."
    docker push "${TIMESTAMP_IMAGE}"

    echo "  Pushing latest..."
    docker push "${LATEST_IMAGE}"

    if [ -n "$TAG" ]; then
        echo "  Pushing ${TAG}..."
        docker push "${CUSTOM_IMAGE}"
    fi

    echo ""
    echo "✓ Successfully pushed all images to ECR"
    echo ""
    echo "Images available at:"
    echo "  ${TIMESTAMP_IMAGE}"
    echo "  ${LATEST_IMAGE}"
    if [ -n "$TAG" ]; then
        echo "  ${CUSTOM_IMAGE}"
    fi
fi

echo ""
echo "Local image details:"
docker images "${REGISTRY}" | head -5
echo ""
echo "To run the image:"
echo "  docker run --rm ${LATEST_IMAGE} 'benchmark --help'"
echo ""

if [ "$PUSH" = false ]; then
    echo "To push images to ECR:"
    echo "  $0 --push"
fi
