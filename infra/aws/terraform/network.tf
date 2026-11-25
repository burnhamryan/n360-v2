# A small VPC with one public subnet and no NAT gateway: the instance gets outbound internet (packages, images,
# plugin, Let's Encrypt) through an internet gateway, and NO inbound access unless a domain is set (then 80/443
# only). Admin and loads use AWS Session Manager. Set subnet_id to use an existing network instead.
locals {
  create_network = var.subnet_id == ""
  public         = var.domain != ""
}

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "this" {
  count                = local.create_network ? 1 : 0
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = { Name = var.name }
}

resource "aws_internet_gateway" "this" {
  count  = local.create_network ? 1 : 0
  vpc_id = aws_vpc.this[0].id
  tags   = { Name = var.name }
}

resource "aws_subnet" "this" {
  count                   = local.create_network ? 1 : 0
  vpc_id                  = aws_vpc.this[0].id
  cidr_block              = var.vpc_cidr
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true
  tags                    = { Name = var.name }
}

resource "aws_route_table" "this" {
  count  = local.create_network ? 1 : 0
  vpc_id = aws_vpc.this[0].id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this[0].id
  }
  tags = { Name = var.name }
}

resource "aws_route_table_association" "this" {
  count          = local.create_network ? 1 : 0
  subnet_id      = aws_subnet.this[0].id
  route_table_id = aws_route_table.this[0].id
}

data "aws_subnet" "existing" {
  count = local.create_network ? 0 : 1
  id    = var.subnet_id
}

locals {
  subnet_id = local.create_network ? aws_subnet.this[0].id : var.subnet_id
  vpc_id    = local.create_network ? aws_vpc.this[0].id : data.aws_subnet.existing[0].vpc_id
  az        = local.create_network ? aws_subnet.this[0].availability_zone : data.aws_subnet.existing[0].availability_zone
}

resource "aws_security_group" "server" {
  name        = "${var.name}-server"
  description = "N360 server: no inbound except HTTPS (and the HTTP certificate challenge) when a domain is set"
  vpc_id      = local.vpc_id
  tags        = { Name = "${var.name}-server" }
}

# Rules as separate resources: an aws_security_group with no inline ingress blocks leaves existing rules in place,
# so clearing the domain would NOT have closed 80/443. These are added and removed exactly with the domain.
resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.server.id
  description       = "outbound: packages, images, Grafana plugin, Lets Encrypt, S3, Session Manager"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "web" {
  for_each          = { for pair in setproduct(local.public ? [443, 80] : [], var.https_allowed_cidrs) : "${pair[0]}-${pair[1]}" => pair }
  security_group_id = aws_security_group.server.id
  description       = each.value[0] == 443 ? "Grafana over HTTPS (Caddy)" : "Lets Encrypt HTTP challenge, redirects to HTTPS"
  ip_protocol       = "tcp"
  from_port         = each.value[0]
  to_port           = each.value[0]
  cidr_ipv4         = each.value[1]
}
