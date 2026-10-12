data "aws_ssm_parameter" "ubuntu_ami" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
}

data "aws_caller_identity" "current" {}

resource "aws_vpc" "scanner" {
  cidr_block           = var.scanner_vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${var.name}-vpc" }
}

resource "aws_subnet" "scanner" {
  vpc_id                  = aws_vpc.scanner.id
  cidr_block              = var.scanner_vpc_cidr
  map_public_ip_on_launch = false
  tags                    = { Name = "${var.name}-subnet" }
}

resource "aws_internet_gateway" "scanner" {
  vpc_id = aws_vpc.scanner.id
  tags   = { Name = "${var.name}-igw" }
}

resource "aws_vpc_peering_connection" "lab" {
  vpc_id      = aws_vpc.scanner.id
  peer_vpc_id = var.lab_vpc_id
  auto_accept = true
  tags        = { Name = "${var.name}-lab" }
}

resource "aws_route_table" "scanner" {
  vpc_id = aws_vpc.scanner.id
  tags   = { Name = "${var.name}-routes" }
}

resource "aws_route_table_association" "scanner" {
  subnet_id      = aws_subnet.scanner.id
  route_table_id = aws_route_table.scanner.id
}

resource "aws_route" "internet" {
  route_table_id         = aws_route_table.scanner.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.scanner.id
}

resource "aws_route" "lab" {
  route_table_id            = aws_route_table.scanner.id
  destination_cidr_block    = "${var.lab_private_ip}/32"
  vpc_peering_connection_id = aws_vpc_peering_connection.lab.id
}

resource "aws_route" "scanner_from_lab" {
  for_each                  = var.lab_route_table_ids
  route_table_id            = each.value
  destination_cidr_block    = var.scanner_vpc_cidr
  vpc_peering_connection_id = aws_vpc_peering_connection.lab.id
}

resource "aws_security_group" "scanner" {
  #checkov:skip=CKV_AWS_382:Network vulnerability scans require arbitrary target ports/protocols; no ingress is allowed
  name_prefix = "${var.name}-"
  description = "Greenbone scanner: no inbound access"
  vpc_id      = aws_vpc.scanner.id
  ingress     = []

  # Feed and image downloads plus public-target scans. The instance has a public
  # address for outbound traffic, but no ingress rule or exposed Greenbone UI.
  egress {
    description = "Feed downloads and vulnerability probes to configured targets"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${var.name}-sg" }
}

resource "aws_vpc_security_group_ingress_rule" "lab_ssh" {
  #checkov:skip=CKV_AWS_24:SSH is restricted to the scanner security group through peering; no public CIDR is allowed
  security_group_id            = var.lab_security_group_id
  referenced_security_group_id = aws_security_group.scanner.id
  description                  = "Dedicated Greenbone authenticated SSH scan"
  ip_protocol                  = "tcp"
  from_port                    = 22
  to_port                      = 22
  depends_on                   = [aws_vpc_peering_connection.lab]
}

resource "aws_default_security_group" "scanner" {
  vpc_id  = aws_vpc.scanner.id
  ingress = []
  egress  = []
}

resource "aws_cloudwatch_log_group" "flows" {
  #checkov:skip=CKV_AWS_158:CloudWatch encrypts these network metadata logs at rest; a customer key adds a recurring lab cost
  #checkov:skip=CKV_AWS_338:Thirty days of scan traffic is sufficient for lab diagnostics and limits operational log retention
  name              = "/aws/vpc/${var.name}-flows"
  retention_in_days = 30
}

resource "aws_iam_role" "flows" {
  name_prefix = "${var.name}-flows-"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "vpc-flow-logs.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "flows" {
  name = "${var.name}-flows"
  role = aws_iam_role.flows.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
        Resource = "${aws_cloudwatch_log_group.flows.arn}:*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:DescribeLogGroups"]
        Resource = "*"
      }
    ]
  })
}

resource "aws_flow_log" "scanner" {
  vpc_id               = aws_vpc.scanner.id
  traffic_type         = "ALL"
  iam_role_arn         = aws_iam_role.flows.arn
  log_destination      = aws_cloudwatch_log_group.flows.arn
  log_destination_type = "cloud-watch-logs"
  depends_on           = [aws_iam_role_policy.flows]
}
