# S3 Module - Bucket for Ansible SSM File Transfers

variable "experiment_id" {
  description = "Experiment ID"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
}

# S3 bucket for Ansible file transfers via SSM
resource "aws_s3_bucket" "ansible_ssm" {
  bucket = "pulsar-lab-ansible-ssm-${var.experiment_id}"

  tags = {
    Name    = "pulsar-lab-ansible-ssm-${var.experiment_id}"
    Purpose = "Ansible SSM file transfers"
  }

  lifecycle {
    ignore_changes = [tags_all]
  }
}

# Enable versioning
resource "aws_s3_bucket_versioning" "ansible_ssm" {
  bucket = aws_s3_bucket.ansible_ssm.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Block all public access
resource "aws_s3_bucket_public_access_block" "ansible_ssm" {
  bucket = aws_s3_bucket.ansible_ssm.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Lifecycle rule to clean up old objects
resource "aws_s3_bucket_lifecycle_configuration" "ansible_ssm" {
  bucket = aws_s3_bucket.ansible_ssm.id

  rule {
    id     = "cleanup-old-files"
    status = "Enabled"

    filter {}  # Apply to all objects

    expiration {
      days = 1
    }

    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }
}

# Server-side encryption
resource "aws_s3_bucket_server_side_encryption_configuration" "ansible_ssm" {
  bucket = aws_s3_bucket.ansible_ssm.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Outputs
output "bucket_name" {
  description = "S3 bucket name"
  value       = aws_s3_bucket.ansible_ssm.id
}

output "bucket_arn" {
  description = "S3 bucket ARN"
  value       = aws_s3_bucket.ansible_ssm.arn
}

output "bucket_region" {
  description = "S3 bucket region"
  value       = aws_s3_bucket.ansible_ssm.region
}
