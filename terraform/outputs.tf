# Terraform Outputs - EKS Cluster Information

# Network Outputs
output "vpc_id" {
  description = "VPC ID"
  value       = module.network.vpc_id
}

output "public_subnet_ids" {
  description = "List of public subnet IDs"
  value       = module.network.public_subnet_ids
}

output "private_subnet_ids" {
  description = "List of private subnet IDs"
  value       = module.network.private_subnet_ids
}

output "nat_gateway_ips" {
  description = "List of NAT Gateway public IPs"
  value       = module.network.nat_gateway_ips
}

# EKS Cluster Outputs
output "eks_cluster_id" {
  description = "EKS cluster ID"
  value       = module.eks.cluster_id
}

output "eks_cluster_name" {
  description = "EKS cluster name"
  value       = module.eks.cluster_name
}

output "eks_cluster_endpoint" {
  description = "EKS cluster API endpoint"
  value       = module.eks.cluster_endpoint
}

output "eks_cluster_version" {
  description = "EKS cluster Kubernetes version"
  value       = module.eks.cluster_version
}

output "eks_node_group_id" {
  description = "EKS node group ID"
  value       = module.eks.node_group_id
}

output "eks_node_group_status" {
  description = "EKS node group status"
  value       = module.eks.node_group_status
}

# OIDC Provider Outputs
output "oidc_provider_arn" {
  description = "OIDC provider ARN for EKS service accounts"
  value       = module.eks.oidc_provider_arn
}

output "oidc_provider_url" {
  description = "OIDC provider URL"
  value       = module.eks.oidc_provider_url
}

# kubectl Configuration Command
output "configure_kubectl" {
  description = "Command to configure kubectl"
  value       = "aws eks update-kubeconfig --region ${var.aws_region} --name ${module.eks.cluster_name}"
}

# Helm Installation Command
output "helm_install_command" {
  description = "Example command to install Pulsar via Helm"
  value       = "helm install pulsar ./helm/pulsar-eks-lab --namespace pulsar --create-namespace"
}
