## Terraform configuration for EC2 Auto Scaling Group with spot instances

# Get the latest Amazon Linux 2 AMI ID from SSM Parameter Store
data "aws_ssm_parameter" "amazon_linux_2_ami" {
  name = var.ami_ssm_parameter
}

# IAM role for EC2 instances to allow SSM session
resource "aws_iam_role" "ec2_ssm_role" {
  name = "${var.project_name}-ec2-ssm-role"

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
}

# Attach the SSM managed policy to the role
resource "aws_iam_role_policy_attachment" "ec2_ssm_policy" {
  role       = aws_iam_role.ec2_ssm_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Instance profile for the EC2 instances
resource "aws_iam_instance_profile" "ec2_ssm_profile" {
  name = "${var.project_name}-ec2-ssm-profile"
  role = aws_iam_role.ec2_ssm_role.name
}

# Security group for EC2 instances
resource "aws_security_group" "ec2_sg" {
  name        = "${var.project_name}-ec2-sg"
  description = "Security group for EC2 instances"
  vpc_id      = local.default_vpc_id

  # Outbound access for SSM agent
  egress {
    description = "Outbound HTTPS for SSM"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # Outbound access for package updates
  egress {
    description = "Outbound HTTP for package updates"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-ec2-sg"
  }
}

# Launch template for the ASG
resource "aws_launch_template" "ec2_template" {
  name_prefix   = "${var.project_name}-lt-"
  image_id      = data.aws_ssm_parameter.amazon_linux_2_ami.value
  instance_type = var.instance_type

  vpc_security_group_ids = [aws_security_group.ec2_sg.id]

  iam_instance_profile {
    name = aws_iam_instance_profile.ec2_ssm_profile.name
  }

  # Configure spot instance
  instance_market_options {
    market_type = "spot"
    spot_options {
      spot_instance_type = "one-time"
    }
  }

  #   # User data to ensure SSM agent is running
  #   user_data = base64encode(<<-EOF
  #     #!/bin/bash
  #     yum update -y
  #     yum install -y amazon-ssm-agent
  #     systemctl enable amazon-ssm-agent
  #     systemctl start amazon-ssm-agent
  #   EOF
  #   )

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "${var.project_name}-spot-instance"
      Type = "spot"
    }
  }

  tags = {
    Name = "${var.project_name}-launch-template"
  }
}

# Auto Scaling Group
resource "aws_autoscaling_group" "ec2_asg" {
  name                      = "${var.project_name}-asg"
  vpc_zone_identifier       = local.default_subnet_ids
  target_group_arns         = []
  health_check_type         = "EC2"
  health_check_grace_period = 300

  min_size         = 1
  max_size         = 1
  desired_capacity = 1

  launch_template {
    id      = aws_launch_template.ec2_template.id
    version = "$Latest"
  }

  tag {
    key                 = "Name"
    value               = "${var.project_name}-asg"
    propagate_at_launch = false
  }

  tag {
    key                 = "Project"
    value               = var.project_name
    propagate_at_launch = true
  }
}

# Output the instance information
output "asg_name" {
  description = "Name of the Auto Scaling Group"
  value       = aws_autoscaling_group.ec2_asg.name
}

output "launch_template_id" {
  description = "ID of the launch template"
  value       = aws_launch_template.ec2_template.id
}

output "ec2_role_arn" {
  description = "ARN of the IAM role for EC2 instances"
  value       = aws_iam_role.ec2_ssm_role.arn
}