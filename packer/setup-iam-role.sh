#!/bin/bash
set -e

echo "========================================="
echo "Setting up SSM IAM Role for Packer"
echo "========================================="

ROLE_NAME="SSMManagedInstanceCore"
INSTANCE_PROFILE_NAME="SSMManagedInstanceCore"

# Check if role already exists
if aws iam get-role --role-name "$ROLE_NAME" &>/dev/null; then
    echo "✓ IAM role '$ROLE_NAME' already exists"
else
    echo "Creating IAM role '$ROLE_NAME'..."

    # Create role with EC2 trust policy
    aws iam create-role \
        --role-name "$ROLE_NAME" \
        --assume-role-policy-document '{
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "ec2.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }]
        }' \
        --description "Allows EC2 instances to use AWS Systems Manager"

    echo "✓ IAM role created"
fi

# Attach the AWS managed policy for SSM
echo "Attaching SSM managed policy..."
aws iam attach-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-arn "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" \
    2>/dev/null && echo "✓ Policy attached" || echo "✓ Policy already attached"

# Check if instance profile exists
if aws iam get-instance-profile --instance-profile-name "$INSTANCE_PROFILE_NAME" &>/dev/null; then
    echo "✓ Instance profile '$INSTANCE_PROFILE_NAME' already exists"
else
    echo "Creating instance profile '$INSTANCE_PROFILE_NAME'..."
    aws iam create-instance-profile \
        --instance-profile-name "$INSTANCE_PROFILE_NAME"

    echo "✓ Instance profile created"
fi

# Add role to instance profile
echo "Adding role to instance profile..."
aws iam add-role-to-instance-profile \
    --instance-profile-name "$INSTANCE_PROFILE_NAME" \
    --role-name "$ROLE_NAME" \
    2>/dev/null && echo "✓ Role added to instance profile" || echo "✓ Role already in instance profile"

# Wait for IAM changes to propagate
echo ""
echo "Waiting 10 seconds for IAM changes to propagate..."
sleep 10

echo ""
echo "========================================="
echo "✓ Setup Complete!"
echo "========================================="
echo ""
echo "IAM Role: $ROLE_NAME"
echo "Instance Profile: $INSTANCE_PROFILE_NAME"
echo ""
echo "You can now run: packer build pulsar-base.pkr.hcl"
echo ""
