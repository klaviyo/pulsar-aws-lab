#!/bin/bash
set -euo pipefail

# Common functions for Pulsar user-data scripts
# This script provides shared utilities for EC2 metadata, service discovery, and error handling

# Configuration
readonly METADATA_URL="http://169.254.169.254/latest/meta-data"
readonly METADATA_TOKEN_URL="http://169.254.169.254/latest/api/token"
readonly MAX_RETRIES=30
readonly RETRY_DELAY=10
readonly LOG_FILE="/var/log/pulsar-init.log"

# Get IMDSv2 token for secure metadata access
get_imds_token() {
    local token
    token=$(curl -s -X PUT "${METADATA_TOKEN_URL}" \
        -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" \
        --max-time 5 --retry 3 || echo "")
    echo "${token}"
}

# Fetch EC2 instance metadata
# Usage: get_instance_metadata <field>
# Fields: instance-id, local-ipv4, placement/availability-zone, instance-type
get_instance_metadata() {
    local field="${1:-}"
    if [[ -z "${field}" ]]; then
        log "ERROR" "get_instance_metadata: field parameter required"
        return 1
    fi

    local token
    token=$(get_imds_token)

    local metadata
    if [[ -n "${token}" ]]; then
        metadata=$(curl -s -H "X-aws-ec2-metadata-token: ${token}" \
            "${METADATA_URL}/${field}" --max-time 5 --retry 3 || echo "")
    else
        # Fallback to IMDSv1
        metadata=$(curl -s "${METADATA_URL}/${field}" --max-time 5 --retry 3 || echo "")
    fi

    if [[ -z "${metadata}" ]]; then
        log "ERROR" "Failed to fetch metadata: ${field}"
        return 1
    fi

    echo "${metadata}"
}

# Get EC2 tag value for this instance
# Usage: get_tag_value <tag_name>
get_tag_value() {
    local tag_name="${1:-}"
    if [[ -z "${tag_name}" ]]; then
        log "ERROR" "get_tag_value: tag_name parameter required"
        return 1
    fi

    local instance_id
    instance_id=$(get_instance_metadata "instance-id")

    local region
    region=$(get_instance_metadata "placement/availability-zone" | sed 's/[a-z]$//')

    local tag_value
    tag_value=$(aws ec2 describe-tags \
        --region "${region}" \
        --filters "Name=resource-id,Values=${instance_id}" \
                  "Name=key,Values=${tag_name}" \
        --query 'Tags[0].Value' \
        --output text 2>/dev/null || echo "")

    if [[ -z "${tag_value}" ]] || [[ "${tag_value}" == "None" ]]; then
        log "WARN" "Tag not found: ${tag_name}"
        return 1
    fi

    echo "${tag_value}"
}

# Wait for a service port to be available
# Usage: wait_for_service <host> <port> [timeout_seconds]
wait_for_service() {
    local host="${1:-}"
    local port="${2:-}"
    local timeout="${3:-300}"

    if [[ -z "${host}" ]] || [[ -z "${port}" ]]; then
        log "ERROR" "wait_for_service: host and port required"
        return 1
    fi

    log "INFO" "Waiting for ${host}:${port} (timeout: ${timeout}s)"

    local elapsed=0
    while [[ ${elapsed} -lt ${timeout} ]]; do
        if nc -z -w 5 "${host}" "${port}" 2>/dev/null; then
            log "INFO" "Service ${host}:${port} is ready"
            return 0
        fi
        sleep 5
        elapsed=$((elapsed + 5))
    done

    log "ERROR" "Timeout waiting for ${host}:${port}"
    return 1
}

# Generate ZooKeeper server list by querying AWS for ZK instances
# Usage: generate_zk_servers <cluster_name>
# Returns: host1:2888:3888,host2:2888:3888,host3:2888:3888
generate_zk_servers() {
    local cluster_name="${1:-}"
    if [[ -z "${cluster_name}" ]]; then
        log "ERROR" "generate_zk_servers: cluster_name required"
        return 1
    fi

    local region
    region=$(get_instance_metadata "placement/availability-zone" | sed 's/[a-z]$//')

    log "INFO" "Discovering ZooKeeper instances for cluster: ${cluster_name}"

    local zk_ips
    zk_ips=$(aws ec2 describe-instances \
        --region "${region}" \
        --filters "Name=tag:Component,Values=zookeeper" \
                  "Name=tag:ClusterName,Values=${cluster_name}" \
                  "Name=instance-state-name,Values=running" \
        --query 'Reservations[*].Instances[*].PrivateIpAddress' \
        --output text | tr '\t' '\n' | sort)

    if [[ -z "${zk_ips}" ]]; then
        log "ERROR" "No ZooKeeper instances found for cluster: ${cluster_name}"
        return 1
    fi

    local zk_servers=""
    local count=1
    while IFS= read -r ip; do
        if [[ -n "${ip}" ]]; then
            if [[ -n "${zk_servers}" ]]; then
                zk_servers="${zk_servers},"
            fi
            zk_servers="${zk_servers}${ip}:2888:3888"
            count=$((count + 1))
        fi
    done <<< "${zk_ips}"

    if [[ -z "${zk_servers}" ]]; then
        log "ERROR" "Failed to generate ZooKeeper server list"
        return 1
    fi

    log "INFO" "ZooKeeper servers: ${zk_servers}"
    echo "${zk_servers}"
}

# Get ZooKeeper connection string
# Usage: get_zk_connection_string <cluster_name>
# Returns: host1:2181,host2:2181,host3:2181
get_zk_connection_string() {
    local cluster_name="${1:-}"
    if [[ -z "${cluster_name}" ]]; then
        log "ERROR" "get_zk_connection_string: cluster_name required"
        return 1
    fi

    local region
    region=$(get_instance_metadata "placement/availability-zone" | sed 's/[a-z]$//')

    log "INFO" "Getting ZooKeeper connection string for cluster: ${cluster_name}"

    local zk_ips
    zk_ips=$(aws ec2 describe-instances \
        --region "${region}" \
        --filters "Name=tag:Component,Values=zookeeper" \
                  "Name=tag:ClusterName,Values=${cluster_name}" \
                  "Name=instance-state-name,Values=running" \
        --query 'Reservations[*].Instances[*].PrivateIpAddress' \
        --output text | tr '\t' '\n' | sort)

    if [[ -z "${zk_ips}" ]]; then
        log "ERROR" "No ZooKeeper instances found for cluster: ${cluster_name}"
        return 1
    fi

    local zk_string=""
    while IFS= read -r ip; do
        if [[ -n "${ip}" ]]; then
            if [[ -n "${zk_string}" ]]; then
                zk_string="${zk_string},"
            fi
            zk_string="${zk_string}${ip}:2181"
        fi
    done <<< "${zk_ips}"

    if [[ -z "${zk_string}" ]]; then
        log "ERROR" "Failed to generate ZooKeeper connection string"
        return 1
    fi

    log "INFO" "ZooKeeper connection string: ${zk_string}"
    echo "${zk_string}"
}

# Get broker service URLs
# Usage: get_broker_urls <cluster_name>
# Returns: pulsar://host1:6650,pulsar://host2:6650
get_broker_urls() {
    local cluster_name="${1:-}"
    if [[ -z "${cluster_name}" ]]; then
        log "ERROR" "get_broker_urls: cluster_name required"
        return 1
    fi

    local region
    region=$(get_instance_metadata "placement/availability-zone" | sed 's/[a-z]$//')

    log "INFO" "Getting broker URLs for cluster: ${cluster_name}"

    local broker_ips
    broker_ips=$(aws ec2 describe-instances \
        --region "${region}" \
        --filters "Name=tag:Component,Values=broker" \
                  "Name=tag:ClusterName,Values=${cluster_name}" \
                  "Name=instance-state-name,Values=running" \
        --query 'Reservations[*].Instances[*].PrivateIpAddress' \
        --output text | tr '\t' '\n' | sort)

    if [[ -z "${broker_ips}" ]]; then
        log "ERROR" "No broker instances found for cluster: ${cluster_name}"
        return 1
    fi

    local broker_urls=""
    while IFS= read -r ip; do
        if [[ -n "${ip}" ]]; then
            if [[ -n "${broker_urls}" ]]; then
                broker_urls="${broker_urls},"
            fi
            broker_urls="${broker_urls}pulsar://${ip}:6650"
        fi
    done <<< "${broker_ips}"

    if [[ -z "${broker_urls}" ]]; then
        log "ERROR" "Failed to generate broker URLs"
        return 1
    fi

    log "INFO" "Broker URLs: ${broker_urls}"
    echo "${broker_urls}"
}

# Structured logging function
# Usage: log <level> <message>
log() {
    local level="${1:-INFO}"
    local message="${2:-}"
    local timestamp
    timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    local log_entry="[${timestamp}] [${level}] ${message}"
    echo "${log_entry}" | tee -a "${LOG_FILE}"

    # Also log to syslog if available
    if command -v logger &> /dev/null; then
        logger -t pulsar-init -p "user.${level,,}" "${message}"
    fi
}

# Retry a command with exponential backoff
# Usage: retry_command <max_attempts> <command> [args...]
retry_command() {
    local max_attempts="${1}"
    shift
    local command=("$@")

    local attempt=1
    local delay=2

    while [[ ${attempt} -le ${max_attempts} ]]; do
        log "INFO" "Attempt ${attempt}/${max_attempts}: ${command[*]}"

        if "${command[@]}"; then
            log "INFO" "Command succeeded: ${command[*]}"
            return 0
        fi

        if [[ ${attempt} -lt ${max_attempts} ]]; then
            log "WARN" "Command failed, retrying in ${delay}s: ${command[*]}"
            sleep "${delay}"
            delay=$((delay * 2))
            if [[ ${delay} -gt 60 ]]; then
                delay=60
            fi
        fi

        attempt=$((attempt + 1))
    done

    log "ERROR" "Command failed after ${max_attempts} attempts: ${command[*]}"
    return 1
}

# Wait for multiple ZooKeeper instances to be ready
# Usage: wait_for_zookeeper <cluster_name> [timeout]
wait_for_zookeeper() {
    local cluster_name="${1:-}"
    local timeout="${2:-300}"

    if [[ -z "${cluster_name}" ]]; then
        log "ERROR" "wait_for_zookeeper: cluster_name required"
        return 1
    fi

    log "INFO" "Waiting for ZooKeeper cluster to be ready"

    local region
    region=$(get_instance_metadata "placement/availability-zone" | sed 's/[a-z]$//')

    local zk_ips
    zk_ips=$(aws ec2 describe-instances \
        --region "${region}" \
        --filters "Name=tag:Component,Values=zookeeper" \
                  "Name=tag:ClusterName,Values=${cluster_name}" \
                  "Name=instance-state-name,Values=running" \
        --query 'Reservations[*].Instances[*].PrivateIpAddress' \
        --output text | tr '\t' '\n')

    if [[ -z "${zk_ips}" ]]; then
        log "ERROR" "No ZooKeeper instances found"
        return 1
    fi

    local all_ready=false
    local elapsed=0

    while [[ ${elapsed} -lt ${timeout} ]] && [[ "${all_ready}" == "false" ]]; do
        all_ready=true

        while IFS= read -r ip; do
            if [[ -n "${ip}" ]]; then
                if ! echo "ruok" | nc -w 5 "${ip}" 2181 2>/dev/null | grep -q "imok"; then
                    all_ready=false
                    log "INFO" "ZooKeeper ${ip}:2181 not ready yet"
                    break
                fi
            fi
        done <<< "${zk_ips}"

        if [[ "${all_ready}" == "true" ]]; then
            log "INFO" "All ZooKeeper instances are ready"
            return 0
        fi

        sleep 5
        elapsed=$((elapsed + 5))
    done

    log "ERROR" "Timeout waiting for ZooKeeper cluster"
    return 1
}

# Wait for BookKeeper cluster to have minimum bookies available
# Usage: wait_for_bookkeeper <cluster_name> <min_bookies> [timeout]
wait_for_bookkeeper() {
    local cluster_name="${1:-}"
    local min_bookies="${2:-3}"
    local timeout="${3:-300}"

    if [[ -z "${cluster_name}" ]]; then
        log "ERROR" "wait_for_bookkeeper: cluster_name required"
        return 1
    fi

    log "INFO" "Waiting for at least ${min_bookies} BookKeeper instances"

    local elapsed=0
    while [[ ${elapsed} -lt ${timeout} ]]; do
        local region
        region=$(get_instance_metadata "placement/availability-zone" | sed 's/[a-z]$//')

        local bk_count
        bk_count=$(aws ec2 describe-instances \
            --region "${region}" \
            --filters "Name=tag:Component,Values=bookkeeper" \
                      "Name=tag:ClusterName,Values=${cluster_name}" \
                      "Name=instance-state-name,Values=running" \
            --query 'Reservations[*].Instances[*].InstanceId' \
            --output text | wc -w)

        if [[ ${bk_count} -ge ${min_bookies} ]]; then
            log "INFO" "BookKeeper cluster ready with ${bk_count} bookies"
            sleep 10  # Extra time for bookies to register with ZK
            return 0
        fi

        log "INFO" "Found ${bk_count}/${min_bookies} bookies, waiting..."
        sleep 5
        elapsed=$((elapsed + 5))
    done

    log "ERROR" "Timeout waiting for BookKeeper cluster"
    return 1
}

# Create directory with proper permissions
# Usage: create_directory <path> <owner> <mode>
create_directory() {
    local path="${1:-}"
    local owner="${2:-pulsar}"
    local mode="${3:-755}"

    if [[ -z "${path}" ]]; then
        log "ERROR" "create_directory: path required"
        return 1
    fi

    if [[ ! -d "${path}" ]]; then
        log "INFO" "Creating directory: ${path}"
        mkdir -p "${path}"
    fi

    chown -R "${owner}:${owner}" "${path}"
    chmod "${mode}" "${path}"

    log "INFO" "Directory ready: ${path} (owner: ${owner}, mode: ${mode})"
}

# Initialize script execution
log "INFO" "Common functions loaded successfully"
