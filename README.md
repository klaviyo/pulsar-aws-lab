# Pulsar AWS Lab

A reproducible Apache Pulsar testing framework on AWS EKS with automated infrastructure deployment, load testing using OpenMessaging Benchmark, comprehensive reporting, and cost tracking.

**🚀 New to Pulsar AWS Lab? See [CLAUDE.md](CLAUDE.md) for complete documentation!**

## Features

- ✅ **EKS Infrastructure**: Terraform-based EKS cluster with managed node groups
- ✅ **Helm Deployments**: Apache Pulsar deployed via official Helm charts
- ✅ **Kubernetes Native**: Pulsar components run as Kubernetes pods with built-in monitoring
- ✅ **Load Testing**: OpenMessaging Benchmark framework in containers
- ✅ **Cost Tracking**: AWS Cost Explorer integration with detailed reporting
- ✅ **Ephemeral Tests**: Deploy Pulsar → test → undeploy (cluster remains)
- ✅ **Systematic Testing**: Test plan matrices for exploring parameter spaces
- ✅ **Full Lifecycle**: Automated deploy → test → report → undeploy

## Architecture

### Components

Pulsar runs in Kubernetes with the following components:

- **ZooKeeper StatefulSet**: Cluster coordination (default: 3 replicas)
- **BookKeeper StatefulSet**: Message storage layer (default: 3 replicas)
- **Broker Deployment**: Message routing and serving (default: 3 replicas)
- **Proxy Deployment**: Load balancer for clients (default: 2 replicas)
- **Prometheus & Grafana**: Monitoring stack with pre-configured dashboards

### Deployment Architecture

**EKS + Helm Architecture:**
- **Long-lived EKS cluster** - Created once, reused for multiple tests
- **Ephemeral Pulsar deployments** - Installed/uninstalled per test via Helm
- **Docker-based OMB** - Benchmark runs as Kubernetes Jobs
- **Kubernetes-native** - All components are Kubernetes resources

**Deployment Flow:**
```
1. One-time EKS Setup:
   Terraform → EKS Cluster + VPC + Node Groups → kubectl configured

2. Per Test Cycle:
   Helm install Pulsar → Wait for pods ready → Run OMB Job → Collect results → Helm uninstall

3. Cleanup:
   Terraform destroy (when done with all testing)
```

**Network Design:**
- EKS nodes in private subnets with NAT gateway for egress
- Public subnets for load balancers
- Pulsar components communicate via Kubernetes Services
- OMB Jobs connect to broker service: `pulsar://pulsar-broker:6650`

**Security:**
- IAM roles for EKS cluster and node groups
- OIDC provider for Kubernetes service account authentication
- Network policies for pod-to-pod communication (optional)

### Directory Structure

```
pulsar-aws-lab/
├── config/               # Configuration files
│   ├── infrastructure.yaml  # EKS cluster configuration
│   └── test-plans/       # Test scenario definitions
├── terraform/            # Infrastructure as Code
│   └── modules/          # EKS, network, IAM modules
├── helm/                 # Helm charts
│   └── pulsar-eks-lab/   # Pulsar + OMB chart
├── docker/               # Container images
│   └── omb/              # OpenMessaging Benchmark
├── scripts/              # Orchestration
│   ├── orchestrator.py   # Main orchestration (kubectl/helm)
│   ├── build-omb-image.sh
│   ├── cost_tracker.py
│   └── report_generator.py
└── docs/                 # Documentation
```

## Prerequisites

### Required Tools

```bash
# AWS CLI
aws --version  # >= 2.0

# kubectl
kubectl version  # >= 1.28

# Helm
helm version  # >= 3.12

# Terraform
terraform --version  # >= 1.0

# Docker (for building OMB image)
docker --version  # >= 20.0

# Python
python3 --version  # >= 3.8
```

### AWS Setup

1. **Configure AWS credentials**:
   ```bash
   aws configure
   # Or use environment variables:
   export AWS_ACCESS_KEY_ID=your_access_key
   export AWS_SECRET_ACCESS_KEY=your_secret_key
   export AWS_DEFAULT_REGION=us-west-2
   ```

2. **IAM Permissions**:
   Your AWS user/role needs permissions for:
   - EKS (create clusters, node groups)
   - EC2 (VPC, subnets, security groups, NAT gateways)
   - IAM (create roles for EKS)
   - Cost Explorer (for cost tracking)

3. **Build OMB Docker Image** (first-time setup):
   ```bash
   # Build the OpenMessaging Benchmark image
   ./scripts/build-omb-image.sh

   # Or with custom registry
   ./scripts/build-omb-image.sh --registry your-ecr-repo --push
   ```

### Python Dependencies

```bash
pip install -r scripts/requirements.txt
```

## Quick Start

### 1. Create EKS Cluster (One-Time)

```bash
python scripts/orchestrator.py cluster-create
```

This creates the EKS cluster (takes 15-20 minutes). The cluster remains running for multiple test cycles.

### 2. Run Full Test Cycle

Execute a test with automatic Pulsar deployment and cleanup:

```bash
python scripts/orchestrator.py full \
  --test-plan config/test-plans/poc.yaml
```

This will:
1. Deploy Pulsar via Helm
2. Wait for all pods to be ready
3. Run benchmark test (20k msgs/sec, 2 min)
4. Collect results
5. Undeploy Pulsar (uninstall Helm chart)

**For comprehensive testing:**
```bash
python scripts/orchestrator.py full \
  --test-plan config/test-plans/baseline.yaml
```

### 3. Manual Workflow

For more control, run each phase separately:

```bash
# Deploy Pulsar to EKS
python scripts/orchestrator.py deploy

# Run tests
python scripts/orchestrator.py run \
  --test-plan config/test-plans/baseline.yaml \
  --experiment-id latest

# Generate report
python scripts/orchestrator.py report \
  --experiment-id latest

# Undeploy Pulsar (keep cluster running)
python scripts/orchestrator.py undeploy \
  --experiment-id latest
```

### 4. Cleanup (When Done Testing)

Destroy the EKS cluster:

```bash
python scripts/orchestrator.py cluster-destroy \
  --experiment-id latest
```

## Configuration

### Infrastructure Configuration

Edit `config/infrastructure.yaml`:

```yaml
experiment:
  id: "my-experiment"
  name: "My Pulsar EKS Test"
  tags:
    team: "data-platform"
    owner: "john.doe"

aws:
  region: "us-west-2"
  availability_zones:
    - "us-west-2a"
    - "us-west-2b"
    - "us-west-2c"

eks:
  cluster_version: "1.31"
  node_group:
    instance_types: ["t3.medium"]
    disk_size: 50
    desired_size: 3
    min_size: 1
    max_size: 5

pulsar:
  zookeeper:
    replicas: 3
  bookkeeper:
    replicas: 3
  broker:
    replicas: 3
```

### Helm Values

Customize Pulsar deployment in `helm/pulsar-eks-lab/values.yaml`:

```yaml
pulsar:
  volumes:
    persistence: true  # Use persistent volumes

  bookkeeper:
    replicaCount: 3
    resources:
      requests:
        memory: "2Gi"
        cpu: "1000m"

  broker:
    replicaCount: 3
    configData:
      managedLedgerDefaultEnsembleSize: "3"
      managedLedgerDefaultWriteQuorum: "3"
```

### Test Plans

Create custom test plans in `config/test-plans/`:

```yaml
name: "my-test-plan"
description: "Custom test scenarios"

base_workload:
  topics: 1
  partitions_per_topic: 16
  message_size: 1024
  producers_per_topic: 1
  consumers_per_topic: 1
  test_duration_minutes: 5

test_runs:
  - name: "baseline"
    type: "fixed_rate"
    producer_rate: 10000

  - name: "high-load"
    type: "fixed_rate"
    producer_rate: 50000
```

## Commands Reference

### Cluster Management
```bash
# Create EKS cluster (one-time)
python scripts/orchestrator.py cluster-create

# Destroy EKS cluster
python scripts/orchestrator.py cluster-destroy --experiment-id <id>

# List experiments
python scripts/orchestrator.py list
```

### Pulsar Deployment
```bash
# Deploy Pulsar to EKS
python scripts/orchestrator.py deploy

# Undeploy Pulsar from EKS
python scripts/orchestrator.py undeploy --experiment-id <id>
```

### Testing
```bash
# Full cycle (deploy → test → undeploy)
python scripts/orchestrator.py full --test-plan <file>

# Run tests only (Pulsar must be deployed)
python scripts/orchestrator.py run --test-plan <file> --experiment-id <id>

# Generate report
python scripts/orchestrator.py report --experiment-id <id>
```

### Kubernetes Operations
```bash
# View Pulsar pods
kubectl get pods -n pulsar

# View Helm releases
helm list -n pulsar

# Check pod logs
kubectl logs -n pulsar <pod-name>

# Port-forward to Grafana
kubectl port-forward -n pulsar svc/grafana 3000:3000
```

## Cost Optimization

### Minimize Costs

1. **Use appropriately sized nodes**: Default is t3.medium (2 vCPU, 4GB RAM)
2. **Adjust node group scaling**: Set min_size to 1 when not testing
3. **Destroy cluster when not in use**: EKS cluster costs ~$0.10/hour
4. **Use spot instances** (future): Save ~70% on node costs

### Cost Tracking

View costs for an experiment:

```bash
python scripts/cost_tracker.py <experiment-id>
```

Costs are automatically included in reports.

## Troubleshooting

### EKS Cluster Issues

```bash
# Check EKS cluster status
aws eks describe-cluster --name pulsar-eks-<experiment-id>

# Update kubectl context
aws eks update-kubeconfig --region us-west-2 --name pulsar-eks-<experiment-id>

# View nodes
kubectl get nodes
```

### Pulsar Deployment Issues

```bash
# Check Helm release status
helm status pulsar -n pulsar

# View pod status
kubectl get pods -n pulsar

# Check pod logs
kubectl logs -n pulsar <pod-name> --tail=100

# Describe pod for events
kubectl describe pod -n pulsar <pod-name>

# Collect all logs for troubleshooting
# (orchestrator does this automatically on failure)
```

### OMB Test Failures

```bash
# View Job status
kubectl get jobs -n pulsar

# Check Job logs
kubectl logs -n pulsar job/omb-<test-name>

# Delete stuck Job
kubectl delete job -n pulsar omb-<test-name>
```

### View Logs

All logs are saved in `~/.pulsar-aws-lab/<experiment-id>/`:
- `orchestrator.log`: Main orchestration log
- `benchmark_results/`: Test results
- `pod_logs/`: Pulsar component logs (collected on failure)

## Advanced Usage

### Manual Terraform Operations

```bash
cd terraform

# Initialize
terraform init

# Plan
terraform plan

# Apply
terraform apply

# Destroy
terraform destroy
```

### Manual Helm Operations

```bash
# Install Pulsar
helm install pulsar ./helm/pulsar-eks-lab -n pulsar --create-namespace

# Upgrade Pulsar
helm upgrade pulsar ./helm/pulsar-eks-lab -n pulsar

# Uninstall Pulsar
helm uninstall pulsar -n pulsar
```

### Build and Push OMB Image

```bash
# Build locally
./scripts/build-omb-image.sh

# Build and push to ECR
./scripts/build-omb-image.sh --registry <account-id>.dkr.ecr.us-west-2.amazonaws.com/pulsar-omb --push

# Build for specific platform
./scripts/build-omb-image.sh --platform linux/amd64
```

## Monitoring

Access Grafana dashboards:

```bash
# Port-forward to Grafana
kubectl port-forward -n pulsar svc/grafana 3000:3000

# Open browser to http://localhost:3000
# Default credentials: admin / admin
```

Pre-configured dashboards:
- Pulsar Overview
- Broker Metrics
- BookKeeper Metrics
- Topic & Namespace Stats
- Consumer & Subscription Stats
- JVM & System Metrics

## Development

### Extend OMB Docker Image

Edit `docker/omb/Dockerfile` and rebuild:

```bash
./scripts/build-omb-image.sh
```

### Custom Helm Values

Override values during deployment:

```bash
python scripts/orchestrator.py deploy --values-file custom-values.yaml
```

## Resources

### Documentation
- [CLAUDE.md](CLAUDE.md) - Complete architecture and development guide
- [Helm Chart Values](helm/pulsar-eks-lab/values.yaml) - All configuration options

### External Resources
- [Apache Pulsar Documentation](https://pulsar.apache.org/docs/)
- [Apache Pulsar Helm Chart](https://pulsar.apache.org/docs/helm-overview/)
- [OpenMessaging Benchmark](https://openmessaging.cloud/docs/benchmarks/)
- [Terraform AWS Provider](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)
- [Terraform EKS Module](https://registry.terraform.io/modules/terraform-aws-modules/eks/aws/)

## Support

For issues and questions:

- GitHub Issues: Report bugs and feature requests
- Pulsar Slack: [Join #general channel](https://pulsar.apache.org/community/)
- Pulsar Mailing List: users@pulsar.apache.org

## License

MIT License - see LICENSE file
