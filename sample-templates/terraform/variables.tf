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
