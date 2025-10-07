#!/bin/bash
#
# Packer Build Script for Pulsar Base AMI
#
# This script provides a convenient wrapper around Packer build commands
# with validation, error checking, and helpful output.
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default values
REGION="${AWS_DEFAULT_REGION:-us-west-2}"
PULSAR_VERSION="3.0.0"
INSTANCE_TYPE="t3.small"
VAR_FILE=""
VALIDATE_ONLY=false
DEBUG=false

# Script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Build Pulsar base AMI using Packer.

OPTIONS:
    -r, --region REGION         AWS region (default: ${REGION})
    -v, --version VERSION       Pulsar version (default: ${PULSAR_VERSION})
    -i, --instance-type TYPE    Instance type for building (default: ${INSTANCE_TYPE})
    -f, --var-file FILE         Variables file (optional)
    --validate                  Only validate template, don't build
    --debug                     Enable Packer debug output
    -h, --help                  Show this help message

EXAMPLES:
    # Build with defaults
    $0

    # Build specific Pulsar version in different region
    $0 --region us-east-1 --version 3.1.0

    # Validate template only
    $0 --validate

    # Use variables file
    $0 --var-file my-vars.pkrvars.hcl

    # Debug mode
    $0 --debug

EOF
    exit 1
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -r|--region)
            REGION="$2"
            shift 2
            ;;
        -v|--version)
            PULSAR_VERSION="$2"
            shift 2
            ;;
        -i|--instance-type)
            INSTANCE_TYPE="$2"
            shift 2
            ;;
        -f|--var-file)
            VAR_FILE="$2"
            shift 2
            ;;
        --validate)
            VALIDATE_ONLY=true
            shift
            ;;
        --debug)
            DEBUG=true
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo -e "${RED}Error: Unknown option $1${NC}"
            usage
            ;;
    esac
done

# Function to print colored messages
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."

    # Check if packer is installed
    if ! command -v packer &> /dev/null; then
        log_error "Packer is not installed. Please install Packer first."
        log_info "Visit: https://www.packer.io/downloads"
        exit 1
    fi

    log_info "Packer version: $(packer version)"

    # Check AWS credentials
    if [ -z "${AWS_ACCESS_KEY_ID}" ] && [ -z "${AWS_PROFILE}" ]; then
        log_warn "AWS credentials not found in environment."
        log_warn "Make sure you have configured AWS CLI or set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY"

        # Try to verify with AWS CLI
        if command -v aws &> /dev/null; then
            if ! aws sts get-caller-identity &> /dev/null; then
                log_error "AWS credentials are not properly configured."
                exit 1
            fi
            log_info "AWS credentials verified via AWS CLI"
        fi
    else
        log_info "AWS credentials found in environment"
    fi

    # Check if template exists
    if [ ! -f "${SCRIPT_DIR}/pulsar-base.pkr.hcl" ]; then
        log_error "Template file not found: ${SCRIPT_DIR}/pulsar-base.pkr.hcl"
        exit 1
    fi

    # Check if var file exists (if specified)
    if [ -n "${VAR_FILE}" ] && [ ! -f "${SCRIPT_DIR}/${VAR_FILE}" ]; then
        log_error "Variables file not found: ${SCRIPT_DIR}/${VAR_FILE}"
        exit 1
    fi

    log_info "Prerequisites check passed"
}

# Initialize Packer
init_packer() {
    log_info "Initializing Packer..."
    cd "${SCRIPT_DIR}"
    packer init pulsar-base.pkr.hcl
}

# Validate template
validate_template() {
    log_info "Validating Packer template..."
    cd "${SCRIPT_DIR}"

    VALIDATE_CMD="packer validate"

    # Add variables
    VALIDATE_CMD="${VALIDATE_CMD} -var region=${REGION}"
    VALIDATE_CMD="${VALIDATE_CMD} -var pulsar_version=${PULSAR_VERSION}"
    VALIDATE_CMD="${VALIDATE_CMD} -var instance_type=${INSTANCE_TYPE}"

    # Add var file if specified
    if [ -n "${VAR_FILE}" ]; then
        VALIDATE_CMD="${VALIDATE_CMD} -var-file=${VAR_FILE}"
    fi

    VALIDATE_CMD="${VALIDATE_CMD} pulsar-base.pkr.hcl"

    log_info "Running: ${VALIDATE_CMD}"
    eval ${VALIDATE_CMD}

    log_info "Template validation successful"
}

# Build AMI
build_ami() {
    log_info "Building Pulsar base AMI..."
    log_info "Region: ${REGION}"
    log_info "Pulsar Version: ${PULSAR_VERSION}"
    log_info "Instance Type: ${INSTANCE_TYPE}"

    cd "${SCRIPT_DIR}"

    BUILD_CMD="packer build"

    # Add debug flag if enabled
    if [ "${DEBUG}" = true ]; then
        BUILD_CMD="${BUILD_CMD} -debug"
    fi

    # Add variables
    BUILD_CMD="${BUILD_CMD} -var region=${REGION}"
    BUILD_CMD="${BUILD_CMD} -var pulsar_version=${PULSAR_VERSION}"
    BUILD_CMD="${BUILD_CMD} -var instance_type=${INSTANCE_TYPE}"

    # Add var file if specified
    if [ -n "${VAR_FILE}" ]; then
        BUILD_CMD="${BUILD_CMD} -var-file=${VAR_FILE}"
    fi

    BUILD_CMD="${BUILD_CMD} pulsar-base.pkr.hcl"

    log_info "Running: ${BUILD_CMD}"

    # Run build and capture output
    if eval ${BUILD_CMD}; then
        log_info "AMI build completed successfully!"
        log_info "Check Packer output above for the AMI ID"
    else
        log_error "AMI build failed!"
        exit 1
    fi
}

# Main execution
main() {
    echo "========================================="
    echo "Pulsar Base AMI Builder"
    echo "========================================="
    echo ""

    check_prerequisites
    init_packer
    validate_template

    if [ "${VALIDATE_ONLY}" = true ]; then
        log_info "Validation complete. Skipping build (--validate flag set)"
        exit 0
    fi

    echo ""
    log_info "Starting AMI build..."
    log_warn "This will take approximately 15-25 minutes"
    echo ""

    build_ami

    echo ""
    log_info "Build process completed!"
    log_info "You can now use the AMI in your Terraform configuration"
}

# Run main function
main
