terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "ap-southeast-2"
}

resource "aws_budgets_budget" "monthly_cost_budget" {
  name              = "monthly-cost-budget"
  budget_type       = "COST"
  time_unit         = "MONTHLY"
  limit_amount      = "5"
  limit_unit        = "USD"
  time_period_start = "2025-08-27_00:00"

  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "ACTUAL"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = ["quitefrankli@hotmail.com"]
  }
}

resource "aws_security_group" "lazy_wombat_sg" {
  name = "lazy_wombat_sg"
  egress = [
    {
      description      = ""
      cidr_blocks      = ["0.0.0.0/0"]
      from_port        = 0
      ipv6_cidr_blocks = []
      prefix_list_ids  = []
      protocol         = "-1"
      security_groups  = []
      self             = false
      to_port          = 0
    },
  ]
  ingress = [
    {
      description      = ""
      cidr_blocks      = ["0.0.0.0/0"]
      from_port        = 22
      ipv6_cidr_blocks = []
      prefix_list_ids  = []
      protocol         = "tcp"
      security_groups  = []
      self             = false
      to_port          = 22
    },
    {
      description      = ""
      cidr_blocks      = ["0.0.0.0/0"]
      from_port        = 443
      ipv6_cidr_blocks = []
      prefix_list_ids  = []
      protocol         = "tcp"
      security_groups  = []
      self             = false
      to_port          = 443
    },
    {
      description      = ""
      cidr_blocks      = ["0.0.0.0/0"]
      from_port        = 80
      ipv6_cidr_blocks = []
      prefix_list_ids  = []
      protocol         = "tcp"
      security_groups  = []
      self             = false
      to_port          = 80
    },
  ]
  tags     = {}
  tags_all = {}
}

resource "aws_instance" "lazy_wombat" {
  ami                         = "ami-0279a86684f669718" # Ubuntu amd64
  associate_public_ip_address = true
  availability_zone           = "ap-southeast-2c"
  disable_api_stop            = false
  disable_api_termination     = false
  ebs_optimized               = false
  get_password_data           = false
  hibernation                 = false
  instance_type               = "t2.micro"
  key_name                    = "lazy_wombat_key_pair"
  monitoring                  = false
  vpc_security_group_ids      = [aws_security_group.lazy_wombat_sg.id]
  source_dest_check           = true
  tags = {
    Name = "lazy_wombat_server"
  }
  tags_all = {}
}

resource "aws_key_pair" "lazy_wombat_key_pair" {
  key_name   = "lazy_wombat_key_pair"
  public_key = file("~/.ssh/id_rsa.pub")
}

resource "aws_eip" "lazy_wombat_eip" {
  tags = {
    Name = "lazy_wombat_eip"
  }
}

resource "aws_eip_association" "eip_assoc" {
  instance_id   = aws_instance.lazy_wombat.id
  allocation_id = aws_eip.lazy_wombat_eip.id
}

output "server_ip_addr" {
  value = aws_eip.lazy_wombat_eip.public_ip
}
