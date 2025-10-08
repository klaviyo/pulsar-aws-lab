# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Pulsar AWS Lab is a reproducible, ephemeral Apache Pulsar testing framework on AWS. It automates the complete lifecycle of Pulsar cluster deployment, load testing using OpenMessaging Benchmark framework, comprehensive reporting, and teardown with cost tracking.

**Architecture Note**: This project uses **EKS (Elastic Kubernetes Service)** with Helm charts for deployment. Pulsar is deployed via the official Apache Pulsar Helm chart, and OpenMessaging Benchmark runs in Kubernetes pods using a custom Docker image. The EKS cluster is long-lived infrastructure, while Pulsar deployments are ephemeral (installed/uninstalled per test).

## Architecture

### High-Level Components

1. **EKS Infrastructure** (`terraform/modules/eks/`, `terraform/modules/network/`, `terraform/modules/iam/`)
   - Modular Terraform design for EKS cluster provisioning
   - Creates VPC, subnets, security groups, and IAM roles
   - EKS cluster with managed node groups (default: Kubernetes 1.31)
   - Default node configuration: t3.medium instances, 3 nodes (1-5 scaling)
   - OIDC provider for Kubernetes service account integration
   - Cost allocation tags on all resources (experiment-id, component, timestamp)
   - State management (local by default, S3 backend optional)

2. **Helm Chart** (`helm/pulsar-eks-lab/`)
   - Self-contained Helm chart wrapping Apache Pulsar chart (v4.3.0)
   - Includes OpenMessaging Benchmark pod templates (producer/consumer)
   - Grafana dashboard configurations for monitoring
   - EKS-optimized values: persistence enabled, anti-affinity, pod monitoring
   - Default replica counts: 3 ZooKeeper, 3 BookKeeper, 3 Broker, 2 Proxy
   - Configurable via `values.yaml` or runtime overrides

3. **Docker Image** (`docker/omb/`)
   - Custom OpenMessaging Benchmark Docker image
   - Multi-stage build: Maven 3.9 + Java 21 LTS
   - Builds OMB from official GitHub repository
   - Runtime image includes only JRE for smaller footprint
   - Pre-configured with benchmark binary in PATH

4. **Configuration System** (`config/`)
   - Infrastructure config: EKS cluster version, node types, VPC networking
   - Pulsar cluster config: replica counts, JVM settings, storage settings
   - Test plans: workload matrices and variation strategies
   - Workload definitions: topics, partitions, message sizes, rates

5. **Orchestration** (`scripts/orchestrator.py`)
   - Python-based workflow: setup → helm_install → wait → test → report → helm_uninstall → teardown
   - Terraform automation for EKS cluster lifecycle
   - kubectl and Helm integration for Pulsar deployment
   - Test matrix execution via Kubernetes Jobs
   - AWS cost tracking integration
   - Emergency cleanup via tag-based resource discovery

6. **Testing**
   - OpenMessaging Benchmark framework deployed as Kubernetes pods
   - Producer and consumer pods deployed via Helm chart
   - Workload configurations passed via ConfigMaps
   - Configurable workloads: topics, partitions, message sizes, producer/consumer counts
   - Test types: fixed rate, ramp up, scale to failure, latency sensitivity
   - Results collected from pod logs and stored locally

### Workflow

1. **Setup** (one-time): Terraform provisions EKS cluster and node groups
2. **Build Docker Image** (one-time): Build custom OMB image and push to registry
3. **Deploy**: Helm installs Pulsar chart with OMB pods to EKS cluster
4. **Wait**: Orchestrator waits for all pods to reach Ready state
5. **Test**: Run test matrix via OMB pods, collect metrics and logs
6. **Report**: Generate comprehensive offline report with costs
7. **Undeploy**: Helm uninstalls Pulsar release (cleanup pods/PVCs)
8. **Teardown** (optional): Terraform destroys EKS cluster

### Key Design Principles

- **Kubernetes-Native**: Leverages EKS managed infrastructure, Helm for deployment
- **Cost Optimization**: Long-lived EKS cluster, ephemeral Pulsar deployments, default t3.medium nodes
- **Reproducibility**: All configs version controlled, deterministic Helm deployments
- **Scalability**: Kubernetes auto-scaling for nodes, configurable replica counts for Pulsar components
- **Observability**: Integrated Grafana dashboards, Prometheus metrics via pod monitors
- **Safety**: Namespace isolation, RBAC, resource limits, confirmation prompts
- **Extensibility**: Modular Helm chart, custom values overlays, pluggable workloads

## Development Commands

### Prerequisites
```bash
# Install Python dependencies
pip install -r scripts/requirements.txt

# Install kubectl (Kubernetes CLI)
# macOS: brew install kubectl
# Linux: curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
# Windows: choco install kubernetes-cli

# Install Helm (Kubernetes package manager)
# macOS: brew install helm
# Linux: curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
# Windows: choco install kubernetes-helm

# Install Terraform (for EKS infrastructure)
# macOS: brew install terraform
# Linux: See https://www.terraform.io/downloads
# Windows: choco install terraform

# Install AWS CLI
# macOS: brew install awscli
# Linux: See https://aws.amazon.com/cli/
# Windows: choco install awscli

# Install Docker (for building OMB image)
# macOS: brew install --cask docker
# Linux: See https://docs.docker.com/engine/install/
# Windows: choco install docker-desktop

# Configure AWS credentials
aws configure
# Or export credentials
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-east-1
```

### Docker Image Management

```bash
# Build OpenMessaging Benchmark Docker image
cd docker/omb
docker build -t pulsar-omb:latest .

# Tag for ECR (if using AWS container registry)
docker tag pulsar-omb:latest <account-id>.dkr.ecr.us-east-1.amazonaws.com/pulsar-omb:latest

# Login to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com

# Push to ECR
docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/pulsar-omb:latest

# For local development (minikube), load image directly
minikube image load pulsar-omb:latest
```

### kubectl Configuration

**Prerequisites**: EKS cluster must be managed externally (not part of this repository).

```bash
# Configure kubectl to connect to existing EKS cluster
aws eks update-kubeconfig --region <region> --name <cluster-name>

# Verify cluster access
kubectl get nodes
kubectl cluster-info
```

### Helm Operations

```bash
# Download Helm chart dependencies (one-time)
cd helm/pulsar-eks-lab
helm dependency update

# Install Pulsar to EKS cluster
helm install pulsar ./helm/pulsar-eks-lab \
  --namespace pulsar \
  --create-namespace \
  --timeout 15m

# Install with custom values
helm install pulsar ./helm/pulsar-eks-lab \
  --namespace pulsar \
  --create-namespace \
  --values my-custom-values.yaml

# Check deployment status
kubectl get pods -n pulsar
kubectl get pvc -n pulsar

# Upgrade running deployment
helm upgrade pulsar ./helm/pulsar-eks-lab \
  --namespace pulsar \
  --values updated-values.yaml

# Uninstall Pulsar (cleanup)
helm uninstall pulsar --namespace pulsar

# Delete namespace and PVCs
kubectl delete namespace pulsar
```

### Main Operations (via Orchestrator)

```bash
# Full lifecycle (setup EKS → deploy Pulsar → test → report → undeploy → teardown)
python scripts/orchestrator.py full --test-plan config/test-plans/poc.yaml

# With custom tags for cost tracking
python scripts/orchestrator.py full --test-plan config/test-plans/poc.yaml --tag team=engineering --tag project=perf-testing

# List experiments
python scripts/orchestrator.py list

# Individual steps (assumes EKS cluster already exists)
python scripts/orchestrator.py deploy                                     # Helm install Pulsar
python scripts/orchestrator.py run --test-plan config/test-plans/poc.yaml --experiment-id latest
python scripts/orchestrator.py report --experiment-id latest
python scripts/orchestrator.py undeploy --experiment-id latest            # Helm uninstall Pulsar
```

### Kubectl Operations

```bash
# View cluster resources
kubectl get all -n pulsar
kubectl get pods -n pulsar -w  # Watch pod status

# Check pod logs
kubectl logs -n pulsar pulsar-broker-0 -f
kubectl logs -n pulsar pulsar-bookkeeper-0 -f
kubectl logs -n pulsar omb-producer-<pod-id> -f

# Exec into pods
kubectl exec -it -n pulsar pulsar-broker-0 -- bash

# Port forward for local access
kubectl port-forward -n pulsar svc/pulsar-broker 8080:8080  # Admin API
kubectl port-forward -n pulsar svc/pulsar-broker 6650:6650  # Binary protocol
kubectl port-forward -n pulsar svc/pulsar-grafana 3000:3000 # Grafana UI

# Manage topics via pulsar-admin
kubectl exec -n pulsar pulsar-broker-0 -- bin/pulsar-admin topics list public/default
kubectl exec -n pulsar pulsar-broker-0 -- bin/pulsar-admin topics delete persistent://public/default/my-topic
```

### Configuration

- `config/infrastructure.yaml`: AWS region and experiment metadata
- `config/pulsar-cluster.yaml`: Pulsar component settings (replicas, JVM, storage)
- `config/test-plans/*.yaml`: Test scenario definitions and matrices
- `workloads/*.yaml`: OpenMessaging Benchmark workload specifications
- `helm/pulsar-eks-lab/values.yaml`: Helm chart default values (EKS-optimized)

**Note**: EKS cluster is managed externally. Configuration files control Helm deployment and test execution.

## Pulsar Components

Deployed as StatefulSets and Deployments in Kubernetes:

- **ZooKeeper**: Cluster coordination (default: 3 replicas)
  - Service: `pulsar-zookeeper` (ClusterIP)
  - Port: 2181 (client), 2888 (peer), 3888 (election)
  - Persistent storage via PVCs

- **BookKeeper**: Message storage layer (default: 3 replicas)
  - Service: `pulsar-bookkeeper` (ClusterIP)
  - Port: 3181 (client)
  - Persistent storage for journal and ledgers via PVCs

- **Broker**: Message routing and serving (default: 3 replicas)
  - Service: `pulsar-broker` (ClusterIP and LoadBalancer options)
  - Port: 6650 (binary protocol), 8080 (HTTP admin)
  - Stateless, connects to BookKeeper for storage

- **Proxy**: Load balancing and routing (default: 2 replicas)
  - Service: `pulsar-proxy` (LoadBalancer)
  - Port: 6650 (binary), 8080 (HTTP)
  - Optional component for external access

- **OpenMessaging Benchmark**: Load testing (default: 1 producer, 1 consumer)
  - Pods: `omb-producer`, `omb-consumer`
  - Custom Docker image with OMB framework
  - ConfigMap for workload definitions

### Kubernetes Resources

- **Namespaces**: `pulsar` (default), configurable
- **StatefulSets**: ZooKeeper, BookKeeper (require stable network identity)
- **Deployments**: Broker, Proxy, OMB pods (stateless)
- **Services**: ClusterIP for internal communication, LoadBalancer for external access
- **ConfigMaps**: Pulsar configuration, OMB workloads, Grafana dashboards
- **PersistentVolumeClaims**: EBS volumes for ZooKeeper and BookKeeper data
- **ServiceMonitors**: Prometheus scraping configuration for metrics

## Test Plans

Test plans define variations to systematically explore:
- Infrastructure: instance types, storage types, cluster size
- Workload: topics, partitions, message size, producer/consumer counts, rates
- Pulsar config: JVM settings, retention policies, replication factors

Each test run generates:
- Throughput metrics (msgs/sec, MB/sec)
- Latency percentiles (p50, p95, p99, p99.9, max)
- Cost analysis (total cost, cost per million messages)
- Offline report package (HTML + raw data + configs)

## Cost Management

- All resources tagged with experiment-id for tracking
- Cost estimates shown before deployment
- Post-experiment cost report via AWS Cost Explorer API
- Automatic cleanup on completion or failure

## Troubleshooting

### Common Issues

**EKS Cluster Connection Issues**
```bash
# Ensure kubeconfig is updated
aws eks update-kubeconfig --region us-east-1 --name pulsar-eks-<experiment-id>

# Verify cluster access
kubectl get nodes
kubectl cluster-info

# Check IAM permissions
aws sts get-caller-identity
```

**Helm Installation Failures**
```bash
# Check Helm chart dependencies
cd helm/pulsar-eks-lab
helm dependency list
helm dependency update

# Verify chart syntax
helm lint ./helm/pulsar-eks-lab

# Debug installation
helm install pulsar ./helm/pulsar-eks-lab --dry-run --debug

# Check failed pods
kubectl get pods -n pulsar
kubectl describe pod <pod-name> -n pulsar
kubectl logs <pod-name> -n pulsar --previous  # For crashed pods
```

**Pod Not Starting or CrashLoopBackOff**
```bash
# Check pod status and events
kubectl describe pod <pod-name> -n pulsar

# Check logs
kubectl logs <pod-name> -n pulsar -f

# Check resource constraints
kubectl top nodes
kubectl top pods -n pulsar

# Check PVC status (for StatefulSets)
kubectl get pvc -n pulsar
kubectl describe pvc <pvc-name> -n pulsar
```

**Docker Image Pull Failures**
```bash
# Verify image exists
docker images | grep pulsar-omb

# For ECR, verify authentication
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com

# Check imagePullSecrets in Helm values
kubectl get secrets -n pulsar
```

**Stuck Resources After Failed Deployment**
```bash
# Uninstall Helm release
helm uninstall pulsar --namespace pulsar

# Force delete stuck pods
kubectl delete pod <pod-name> -n pulsar --grace-period=0 --force

# Delete namespace (removes all resources)
kubectl delete namespace pulsar

# Emergency cleanup by experiment ID (destroys EKS cluster)
python scripts/cleanup_by_tag.py --experiment-id <exp-id> --execute

# Or use orchestrator teardown
python scripts/orchestrator.py teardown --experiment-id <exp-id>
```

**Performance Issues or Slow Tests**
```bash
# Check node resources
kubectl top nodes
kubectl describe nodes

# Check pod resource usage
kubectl top pods -n pulsar

# Scale up node group (managed externally)
aws eks update-nodegroup-config \
  --cluster-name <cluster-name> \
  --nodegroup-name <nodegroup-name> \
  --scaling-config desiredSize=5
```

### Important Files and Locations

- **Orchestrator logs**: `~/.pulsar-aws-lab/<experiment-id>/orchestrator.log`
- **Experiment results**: `~/.pulsar-aws-lab/<experiment-id>/`
- **Latest experiment symlink**: `~/.pulsar-aws-lab/latest`
- **Kubeconfig**: `~/.kube/config` (updated by `aws eks update-kubeconfig`)
- **Helm values**: `helm/pulsar-eks-lab/values.yaml`
- **Docker context**: `docker/omb/`

### Useful Debugging Commands

```bash
# Get all resources in namespace
kubectl get all -n pulsar

# Check events (helpful for debugging)
kubectl get events -n pulsar --sort-by='.lastTimestamp'

# Check logs for all pods with label
kubectl logs -n pulsar -l app=pulsar-broker --tail=100

# Port forward to access services locally
kubectl port-forward -n pulsar svc/pulsar-broker 8080:8080

# Copy files from pod (e.g., logs, configs)
kubectl cp pulsar/pulsar-broker-0:/pulsar/logs ./local-logs

# Execute commands in pod
kubectl exec -it -n pulsar pulsar-broker-0 -- bash
```

### Key Architectural Changes

**Migrated from EC2 to EKS Architecture**

This project has migrated from AMI-based EC2 instances to Kubernetes on EKS:

- **OLD (v1)**: Terraform provisions EC2 instances → Packer AMIs with pre-installed Pulsar → systemd services
- **NEW (v2)**: Terraform provisions EKS cluster → Helm deploys Pulsar chart → Kubernetes manages pods

**What Changed:**
- No more Packer/AMI building - replaced with Docker images for OMB
- No more Ansible or systemd - replaced with Kubernetes native orchestration
- No more SSM commands - replaced with kubectl/Helm operations
- EC2 instances replaced with EKS managed node groups
- Individual component VMs replaced with pods in a single cluster

**Migration Benefits:**
- Better resource utilization (multiple components per node)
- Easier scaling and updates (Kubernetes native)
- Improved observability (Prometheus/Grafana integration)
- Faster deployment cycles (no AMI rebuild required)
- Cost optimization (shared node infrastructure)

If you see references to Packer, AMIs, Ansible, or SSM in code or docs, they are outdated and should be removed. These components have been completely removed from the project.
