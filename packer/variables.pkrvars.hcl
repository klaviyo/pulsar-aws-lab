# Packer Variables for Pulsar Base AMI
#
# Usage: packer build -var-file=variables.pkrvars.hcl pulsar-base.pkr.hcl
#
# Uncomment and modify values as needed

# AWS region to build AMI in
# region = "us-west-2"

# Apache Pulsar version to install
# pulsar_version = "3.0.0"

# Instance type for building the AMI
# instance_type = "t3.small"

# AMI name prefix
# ami_prefix = "pulsar-base"

# Example: Build for different region with newer Pulsar version
# region = "us-east-1"
# pulsar_version = "3.1.0"
# instance_type = "t3.medium"
