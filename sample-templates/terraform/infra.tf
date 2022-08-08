data "aws_region" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_default_vpc" "default" {
}

resource "aws_default_subnet" "default" {
  for_each = toset(data.aws_availability_zones.available.names)
  availability_zone = each.key
}

locals {
  default_subnet_ids = [ for entry in aws_default_subnet.default : entry.id ]
}
