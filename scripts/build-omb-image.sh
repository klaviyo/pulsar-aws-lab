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
#   --platform PLATFORM Build for specific platform (default: linux/amd64,linux/arm64)
#   --multi-arch        Build for both amd64 and arm64 (same as --platform linux/amd64,linux/arm64)
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
PLATFORM="linux/amd64,linux/arm64"  # Default to multi-arch for Graviton support
MULTI_ARCH=true

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
            MULTI_ARCH=false
            if [[ "$PLATFORM" == *","* ]]; then
                MULTI_ARCH=true
            fi
            shift 2
            ;;
        --multi-arch)
            PLATFORM="linux/amd64,linux/arm64"
            MULTI_ARCH=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --push              Push image to ECR after build"
            echo "  --registry REPO     Docker registry/repository"
            echo "                      (default: 439508887365.dkr.ecr.us-east-1.amazonaws.com/sre/pulsar-omb)"
            echo "  --tag TAG           Additional image tag (timestamp tag is always created)"
            echo "  --platform PLATFORM Build for specific platform (default: linux/amd64,linux/arm64)"
            echo "  --multi-arch        Build for both amd64 and arm64 (default)"
            echo "  -h, --help          Show this help message"
            echo ""
            echo "Image is always tagged with timestamp (YYYYMMDDHHmm) and 'latest'"
            echo ""
            echo "Examples:"
            echo "  $0 --push                    # Build multi-arch and push to ECR"
            echo "  $0 --platform linux/amd64    # Build x86_64 only"
            echo "  $0 --platform linux/arm64    # Build ARM64 only"
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
echo "Platform(s): ${PLATFORM}"
echo "Multi-arch: ${MULTI_ARCH}"
echo ""

# For multi-arch builds, we need to use buildx and push in a single command
# because multi-arch manifests can't be stored locally
if [ "$MULTI_ARCH" = true ]; then
    echo "Multi-arch build requires pushing to registry. Checking prerequisites..."

    # Ensure buildx builder exists
    if ! docker buildx inspect multiarch-builder &>/dev/null; then
        echo "Creating buildx builder for multi-arch builds..."
        docker buildx create --name multiarch-builder --use --bootstrap
    else
        docker buildx use multiarch-builder
    fi

    if [ "$PUSH" = false ]; then
        echo ""
        echo "ERROR: Multi-arch builds must be pushed to a registry."
        echo "       Multi-platform images cannot be stored locally."
        echo ""
        echo "Please run with --push flag:"
        echo "  $0 --push"
        echo ""
        echo "Or build for a single platform:"
        echo "  $0 --platform linux/amd64"
        echo "  $0 --platform linux/arm64"
        exit 1
    fi

    echo ""
    echo "Logging in to ECR..."
    aws ecr get-login-password --region ${AWS_REGION} | docker login --username AWS --password-stdin ${REGISTRY%%/*}

    echo ""
    echo "Building and pushing multi-arch image..."

    BUILD_CMD="docker buildx build --platform ${PLATFORM}"
    BUILD_CMD="${BUILD_CMD} -t ${TIMESTAMP_IMAGE} -t ${LATEST_IMAGE}"

    if [ -n "$TAG" ]; then
        CUSTOM_IMAGE="${REGISTRY}:${TAG}"
        BUILD_CMD="${BUILD_CMD} -t ${CUSTOM_IMAGE}"
    fi

    BUILD_CMD="${BUILD_CMD} --push ${DOCKERFILE_DIR}"

    echo "Running: ${BUILD_CMD}"
    eval "${BUILD_CMD}"

    echo ""
    echo "✓ Successfully built and pushed multi-arch images"
    echo ""
    echo "Images available at:"
    echo "  ${TIMESTAMP_IMAGE}"
    echo "  ${LATEST_IMAGE}"
    if [ -n "$TAG" ]; then
        echo "  ${CUSTOM_IMAGE}"
    fi
    echo ""
    echo "Supported architectures: amd64 (x86_64), arm64 (Graviton)"

else
    # Single platform build - can be done locally
    BUILD_CMD="docker build -t ${TIMESTAMP_IMAGE} -t ${LATEST_IMAGE}"

    if [ -n "$TAG" ]; then
        CUSTOM_IMAGE="${REGISTRY}:${TAG}"
        BUILD_CMD="${BUILD_CMD} -t ${CUSTOM_IMAGE}"
    fi

    BUILD_CMD="${BUILD_CMD} --platform ${PLATFORM} ${DOCKERFILE_DIR}"

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
fi
