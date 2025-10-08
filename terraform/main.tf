terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # Optional: Configure S3 backend for state management
  # backend "s3" {
  #   bucket = "pulsar-lab-terraform-state"
  #   key    = "experiments/${var.experiment_id}/terraform.tfstate"
  #   region = "us-west-2"
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = merge(
      {
        Project      = "pulsar-eks-lab"
        ExperimentID = var.experiment_id
        Experiment   = var.experiment_name
        ManagedBy    = "terraform"
      },
      var.additional_tags
    )
  }
}

locals {
  cluster_name = "pulsar-eks-${var.experiment_id}"
}

# Network Module
module "network" {
  source = "./modules/network"

  experiment_id        = var.experiment_id
  cluster_name         = local.cluster_name
  vpc_cidr             = var.vpc_cidr
  availability_zones   = var.availability_zones
  public_subnet_cidrs  = var.public_subnet_cidrs
  private_subnet_cidrs = var.private_subnet_cidrs
}

# IAM Module
module "iam" {
  source = "./modules/iam"

  experiment_id = var.experiment_id
  cluster_name  = local.cluster_name
}

# EKS Module
module "eks" {
  source = "./modules/eks"

  experiment_id          = var.experiment_id
  cluster_name           = local.cluster_name
  cluster_version        = var.cluster_version
  vpc_id                 = module.network.vpc_id
  subnet_ids             = concat(module.network.public_subnet_ids, module.network.private_subnet_ids)
  cluster_role_arn       = module.iam.cluster_role_arn
  node_group_role_arn    = module.iam.node_group_role_arn
  node_group_desired_size = var.node_group_desired_size
  node_group_min_size    = var.node_group_min_size
  node_group_max_size    = var.node_group_max_size
  node_instance_types    = var.node_instance_types
  node_disk_size         = var.node_disk_size

  depends_on = [module.iam]
}

# Generate kubeconfig file for kubectl access
resource "null_resource" "kubeconfig" {
  provisioner "local-exec" {
    command = "aws eks update-kubeconfig --region ${var.aws_region} --name ${module.eks.cluster_name}"
  }

  triggers = {
    cluster_id = module.eks.cluster_id
  }

  depends_on = [module.eks]
}
