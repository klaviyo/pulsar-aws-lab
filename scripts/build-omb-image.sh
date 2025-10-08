#!/usr/bin/env bash
#
# Build and optionally push OpenMessaging Benchmark Docker image
#
# Usage:
#   ./build-omb-image.sh [OPTIONS]
#
# Options:
#   --push              Push image to registry after build
#   --registry REPO     Docker registry/repository (default: pulsar-omb)
#   --tag TAG           Image tag (default: latest)
#   --platform PLATFORM Build for specific platform (e.g., linux/amd64,linux/arm64)
#

set -euo pipefail

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DOCKERFILE_DIR="$PROJECT_ROOT/docker/omb"

# Default values
PUSH=false
REGISTRY="pulsar-omb"
TAG="latest"
PLATFORM=""

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
            echo "  --push              Push image to registry after build"
            echo "  --registry REPO     Docker registry/repository (default: pulsar-omb)"
            echo "  --tag TAG           Image tag (default: latest)"
            echo "  --platform PLATFORM Build for specific platform"
            echo "  -h, --help          Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Run '$0 --help' for usage information"
            exit 1
            ;;
    esac
done

# Build the image
IMAGE_NAME="${REGISTRY}:${TAG}"
echo "Building Docker image: ${IMAGE_NAME}"
echo "Dockerfile location: ${DOCKERFILE_DIR}"

BUILD_CMD="docker build -t ${IMAGE_NAME} ${DOCKERFILE_DIR}"

if [ -n "$PLATFORM" ]; then
    BUILD_CMD="${BUILD_CMD} --platform ${PLATFORM}"
fi

echo "Running: ${BUILD_CMD}"
eval "${BUILD_CMD}"

echo "✓ Successfully built ${IMAGE_NAME}"

# Push if requested
if [ "$PUSH" = true ]; then
    echo "Pushing image to registry..."
    docker push "${IMAGE_NAME}"
    echo "✓ Successfully pushed ${IMAGE_NAME}"
fi

echo ""
echo "Image details:"
docker images "${REGISTRY}" --filter "reference=${IMAGE_NAME}"
echo ""
echo "To run the image:"
echo "  docker run --rm ${IMAGE_NAME} 'benchmark --help'"
echo ""

if [ "$PUSH" = false ]; then
    echo "To push the image to a registry:"
    echo "  $0 --push --registry <your-registry>/${REGISTRY} --tag ${TAG}"
fi
