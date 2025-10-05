terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
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
        Project      = "pulsar-aws-lab"
        ExperimentID = var.experiment_id
        Experiment   = var.experiment_name
        ManagedBy    = "terraform"
      },
      var.additional_tags
    )
  }
}

# Data source for latest Amazon Linux 2 AMI
data "aws_ami" "amazon_linux_2" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["amzn2-ami-hvm-*-x86_64-gp2"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

locals {
  ami_id = var.ami_id != null ? var.ami_id : data.aws_ami.amazon_linux_2.id
  az     = var.availability_zone != null ? var.availability_zone : "${var.aws_region}a"
}

# Network Module
module "network" {
  source = "./modules/network"

  experiment_id        = var.experiment_id
  vpc_cidr            = var.vpc_cidr
  public_subnet_cidr  = var.public_subnet_cidr
  availability_zone   = local.az
  allowed_ssh_cidrs   = var.allowed_ssh_cidrs
}

# Storage Module (BookKeeper volumes)
module "storage" {
  source = "./modules/storage"

  experiment_id = var.experiment_id
  count_nodes   = var.bookkeeper_count
  volume_size   = var.bookkeeper_volume_size
  volume_type   = var.bookkeeper_volume_type
  iops          = var.bookkeeper_iops
  throughput    = var.bookkeeper_throughput
  availability_zone = local.az
}

# Compute Module
module "compute" {
  source = "./modules/compute"

  experiment_id       = var.experiment_id
  ami_id              = local.ami_id
  ssh_key_name        = var.ssh_key_name
  vpc_id              = module.network.vpc_id
  subnet_id           = module.network.public_subnet_id
  security_group_id   = module.network.security_group_id
  use_spot_instances  = var.use_spot_instances
  spot_max_price      = var.spot_max_price

  # ZooKeeper
  zookeeper_count         = var.zookeeper_count
  zookeeper_instance_type = var.zookeeper_instance_type

  # BookKeeper
  bookkeeper_count         = var.bookkeeper_count
  bookkeeper_instance_type = var.bookkeeper_instance_type
  bookkeeper_volume_ids    = module.storage.volume_ids

  # Broker
  broker_count         = var.broker_count
  broker_instance_type = var.broker_instance_type

  # Client
  client_count         = var.client_count
  client_instance_type = var.client_instance_type
}
