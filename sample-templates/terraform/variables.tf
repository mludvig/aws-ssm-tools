variable "project_name" {
  default = "tf-demo"
}

variable "container_image" {
  default = "docker.io/httpd:latest"
}

variable "container_name" {
  default = "apache"
}

variable "task_count" {
  default = 2
}

variable "instance_type" {
  default = "t4g.small"
}

variable "ami_ssm_parameter" {
  default = "/aws/service/ami-amazon-linux-latest/amzn2-ami-hvm-arm64-gp2"
}
