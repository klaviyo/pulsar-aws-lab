# Setting up gp3 StorageClass for EKS

Your Helm chart requires a `gp3` StorageClass, but EKS doesn't create this by default. Here's how to set it up.

## Prerequisites

The **AWS EBS CSI Driver** must be installed in your EKS cluster. This driver enables dynamic provisioning of EBS volumes.

## Quick Setup (Recommended)

### Step 1: Check if EBS CSI Driver is installed

```bash
kubectl get csidriver ebs.csi.aws.com
```

If not installed, see "Installing EBS CSI Driver" section below.

### Step 2: Create gp3 StorageClass

```bash
cd /Users/fabian.haupt/pprojects/pulsar-aws-lab
./scripts/setup-storage-class.sh
```

This script will:
- Verify EBS CSI driver is installed
- Create the gp3 StorageClass
- Set it as the default StorageClass

## Manual Setup

### Option A: Using kubectl (Fastest)

```bash
kubectl apply -f k8s/storage-class-gp3.yaml
```

### Option B: Using Terraform

Add to your EKS Terraform configuration:

1. Copy `terraform/storage-class-gp3.tf` to your Terraform EKS module
2. Ensure you have the OIDC provider ARN (see `terraform/example-outputs.tf`)
3. Apply Terraform:

```bash
terraform plan
terraform apply
```

## Installing EBS CSI Driver

### Option 1: Using AWS EKS Managed Addon (Recommended)

This is the recommended approach for production clusters.

#### Step 1: Create IAM Role for EBS CSI Driver

```bash
# Get your cluster's OIDC provider
OIDC_PROVIDER=$(aws eks describe-cluster \
  --name <cluster-name> \
  --query "cluster.identity.oidc.issuer" \
  --output text | sed 's/https:\/\///')

# Create IAM role with trust policy
cat > trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<account-id>:oidc-provider/${OIDC_PROVIDER}"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "${OIDC_PROVIDER}:aud": "sts.amazonaws.com",
          "${OIDC_PROVIDER}:sub": "system:serviceaccount:kube-system:ebs-csi-controller-sa"
        }
      }
    }
  ]
}
EOF

# Create the role
aws iam create-role \
  --role-name AmazonEKS_EBS_CSI_DriverRole \
  --assume-role-policy-document file://trust-policy.json

# Attach the required policy
aws iam attach-role-policy \
  --role-name AmazonEKS_EBS_CSI_DriverRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy
```

#### Step 2: Install the EBS CSI Driver Addon

```bash
aws eks create-addon \
  --cluster-name <cluster-name> \
  --addon-name aws-ebs-csi-driver \
  --service-account-role-arn arn:aws:iam::<account-id>:role/AmazonEKS_EBS_CSI_DriverRole
```

### Option 2: Using Helm

```bash
helm repo add aws-ebs-csi-driver https://kubernetes-sigs.github.io/aws-ebs-csi-driver
helm repo update

helm install aws-ebs-csi-driver aws-ebs-csi-driver/aws-ebs-csi-driver \
  --namespace kube-system \
  --set controller.serviceAccount.annotations."eks\.amazonaws\.com/role-arn"="arn:aws:iam::<account-id>:role/AmazonEKS_EBS_CSI_DriverRole"
```

## Verification

### Check EBS CSI Driver

```bash
# Check pods
kubectl get pods -n kube-system -l app.kubernetes.io/name=aws-ebs-csi-driver

# Check CSI driver registration
kubectl get csidriver
```

### Check StorageClass

```bash
# List all storage classes
kubectl get storageclass

# Verify gp3 is the default
kubectl get storageclass gp3 -o yaml
```

### Test with a PVC

```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: test-gp3-pvc
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: gp3
  resources:
    requests:
      storage: 10Gi
EOF

# Check status
kubectl get pvc test-gp3-pvc

# Cleanup
kubectl delete pvc test-gp3-pvc
```

## Terraform Integration (Recommended for Production)

For production clusters, manage this via Terraform:

```hcl
# In your EKS module
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 19.0"

  cluster_name    = "my-cluster"
  cluster_version = "1.28"
  # ... other config ...
}

# Add EBS CSI driver addon
module "ebs_csi_driver" {
  source = "./terraform"  # Path to storage-class-gp3.tf

  cluster_name      = module.eks.cluster_name
  oidc_provider_arn = module.eks.oidc_provider_arn
}
```

## StorageClass Parameters

The gp3 StorageClass supports these parameters:

```yaml
parameters:
  type: gp3                    # EBS volume type
  encrypted: "true"            # Enable encryption
  iops: "3000"                 # IOPS (3000-16000)
  throughput: "125"            # Throughput in MiB/s (125-1000)
  fsType: ext4                 # Filesystem type (default: ext4)

  # Optional: Add tags to created volumes
  tagSpecification_1: "Name={{ .PVCName }}"
  tagSpecification_2: "Environment=production"
```

## Troubleshooting

### Issue: Pods stuck in "Pending" state

```bash
# Check PVC status
kubectl get pvc -A

# Check events
kubectl describe pvc <pvc-name> -n <namespace>

# Check EBS CSI driver logs
kubectl logs -n kube-system -l app=ebs-csi-controller --tail=50
```

### Issue: "error creating EBS volume"

**Cause**: EBS CSI driver lacks IAM permissions.

**Fix**: Ensure the IAM role has the `AmazonEBSCSIDriverPolicy` attached.

```bash
# Verify role has policy
aws iam list-attached-role-policies --role-name AmazonEKS_EBS_CSI_DriverRole
```

### Issue: "VolumeBindingWaitForFirstConsumer"

This is **normal behavior** for `WaitForFirstConsumer` binding mode. The volume won't be created until a pod actually uses the PVC. This ensures the volume is created in the same AZ as the pod.

## Cost Optimization

gp3 volumes are cheaper and faster than gp2:

- **gp2**: $0.10/GB-month, up to 16,000 IOPS
- **gp3**: $0.08/GB-month, 3,000 IOPS baseline (can provision more)

For Pulsar workloads, consider:
- **ZooKeeper**: gp3 with default settings (10Gi each)
- **BookKeeper**: gp3 with increased IOPS for journal volumes
- **Broker**: gp3 sufficient for most workloads

## References

- [AWS EBS CSI Driver Documentation](https://github.com/kubernetes-sigs/aws-ebs-csi-driver)
- [EKS Storage Best Practices](https://docs.aws.amazon.com/eks/latest/userguide/storage.html)
- [EBS Volume Types](https://aws.amazon.com/ebs/volume-types/)
