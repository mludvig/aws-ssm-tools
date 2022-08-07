# Sample CloudFormation and Terraform templates

This directory contains sample CloudFormation and Terraform templates
configured for use with `ssm-session` and `ecs-session`.

They include the required IAM roles, ECS configurations, etc.

## template-ecs-task.yml

CloudFormation template that spins up an ECS cluster and a sample
ECS service (nginx container) to which you can login with `ecs-session`.
