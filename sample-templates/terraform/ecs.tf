## Terraform configuration for "ECS Exec"-enabled containers.

resource "aws_ecs_cluster" "ecs_cluster" {
  name = "${var.project_name}-cluster"
}

resource "aws_ecs_task_definition" "task_def" {
  family                   = "${var.project_name}-task-def"
  network_mode             = "awsvpc"
  task_role_arn            = aws_iam_role.ecs_task_role.arn
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  requires_compatibilities = ["FARGATE"]
  cpu                      = 256
  memory                   = 512
  container_definitions = jsonencode([{
    Name                   = var.container_name
    Essential              = true
    Image                  = var.container_image
    LogConfiguration = {
      LogDriver = "awslogs"
      Options = {
        awslogs-region        = data.aws_region.current.region
        awslogs-group         = aws_cloudwatch_log_group.ecs_logs.id
        awslogs-stream-prefix = "${var.project_name}-logs"
      }
    }
  }])
}

resource "aws_ecs_service" "service" {
  name                               = "${var.project_name}-service"
  cluster                            = aws_ecs_cluster.ecs_cluster.id
  task_definition                    = aws_ecs_task_definition.task_def.arn
  desired_count                      = var.task_count
  #deployment_maximum_percent         = 200
  #deployment_minimum_healthy_percent = 100
  wait_for_steady_state              = true
  launch_type                        = "FARGATE"
  platform_version                   = "LATEST"     # LATEST is 1.4.0 -> ok
  enable_execute_command             = true         # Enable ECS Exec
  enable_ecs_managed_tags            = true
  network_configuration {
    assign_public_ip = true
    subnets         = local.default_subnet_ids
    security_groups = [aws_security_group.ecs_task_sg.id]
  }
}

resource "aws_iam_role" "ecs_task_role" {
  name = "${var.project_name}-task-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Sid    = ""
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_task_role_ssm" {
  role       = aws_iam_role.ecs_task_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role" "ecs_task_execution_role" {
  name = "${var.project_name}-task-execution-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Sid    = ""
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution_role_policy" {
  role       = aws_iam_role.ecs_task_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_security_group" "ecs_task_sg" {
  name        = "${var.project_name}-task-sg"
  description = "Task Security Group"
  vpc_id      = aws_default_vpc.default.id

  # Configure 'ingress' rules as required by your containers
  #ingress {
  #  description     = "Access to ECS tasks"
  #  protocol        = "tcp"
  #  from_port       = 80
  #  to_port         = 80
  #  cidr_blocks     = ["0.0.0.0/0"]
  #}

  egress {
    description = "Outbound access from ECS tasks to SSM service"
    protocol    = "tcp"
    from_port   = 443
    to_port     = 443
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_cloudwatch_log_group" "ecs_logs" {
  name              = "${var.project_name}-logs"
  retention_in_days = 400
}
