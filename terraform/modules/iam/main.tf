# IAM Module - SSM Role and Instance Profile

variable "experiment_id" {
  description = "Experiment ID"
  type        = string
}

# IAM Role for EC2 instances to use SSM
resource "aws_iam_role" "ssm_role" {
  name = "pulsar-lab-ssm-role-${var.experiment_id}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name = "pulsar-lab-ssm-role-${var.experiment_id}"
  }

  lifecycle {
    ignore_changes = [tags_all]
  }
}

# Attach AWS managed policy for SSM
resource "aws_iam_role_policy_attachment" "ssm_managed_policy" {
  role       = aws_iam_role.ssm_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# IAM Instance Profile
resource "aws_iam_instance_profile" "ssm_profile" {
  name = "pulsar-lab-ssm-profile-${var.experiment_id}"
  role = aws_iam_role.ssm_role.name

  tags = {
    Name = "pulsar-lab-ssm-profile-${var.experiment_id}"
  }

  lifecycle {
    ignore_changes = [tags_all]
  }
}

# Outputs
output "instance_profile_name" {
  description = "IAM instance profile name"
  value       = aws_iam_instance_profile.ssm_profile.name
}

output "instance_profile_arn" {
  description = "IAM instance profile ARN"
  value       = aws_iam_instance_profile.ssm_profile.arn
}

output "role_arn" {
  description = "IAM role ARN"
  value       = aws_iam_role.ssm_role.arn
}
