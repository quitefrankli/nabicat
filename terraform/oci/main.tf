terraform {
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "~> 6.0"
    }
  }
}

provider "oci" {
  tenancy_ocid     = var.tenancy_ocid
  user_ocid        = var.user_ocid
  fingerprint      = var.fingerprint
  private_key_path = var.private_key_path
  region           = var.region
}

# Get availability domains for the region
data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

# Get the latest Oracle Linux image
data "oci_core_images" "ubuntu_image" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "24.04"
  shape                    = var.instance_shape
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

# Budget with alert rule (equivalent to AWS budget)
resource "oci_budget_budget" "monthly_cost_budget" {
  compartment_id = var.tenancy_ocid
  amount         = var.budget_amount
  reset_period   = "MONTHLY"
  description    = "Monthly cost budget for Lazy Wombat"
  display_name   = "monthly-cost-budget"
  target_type    = "COMPARTMENT"
  targets        = [var.compartment_ocid]
}

resource "oci_budget_alert_rule" "budget_alert" {
  budget_id      = oci_budget_budget.monthly_cost_budget.id
  threshold      = 100
  threshold_type = "PERCENTAGE"
  type           = "ACTUAL"
  display_name   = "budget-100-percent-alert"
  message        = "Budget has reached 100% of the monthly limit"
  recipients     = var.notification_email
}

# Virtual Cloud Network (equivalent to AWS VPC)
resource "oci_core_vcn" "lazy_wombat_vcn" {
  compartment_id = var.compartment_ocid
  cidr_blocks    = ["10.0.0.0/16"]
  display_name   = "lazy-wombat-vcn"
  dns_label      = "lazywombat"
}

# Internet Gateway (for public internet access)
resource "oci_core_internet_gateway" "lazy_wombat_igw" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lazy_wombat_vcn.id
  display_name   = "lazy-wombat-internet-gateway"
  enabled        = true
}

# Route Table for public subnet
resource "oci_core_route_table" "lazy_wombat_route_table" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lazy_wombat_vcn.id
  display_name   = "lazy-wombat-route-table"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.lazy_wombat_igw.id
  }
}

# Public Subnet
resource "oci_core_subnet" "lazy_wombat_subnet" {
  compartment_id    = var.compartment_ocid
  vcn_id            = oci_core_vcn.lazy_wombat_vcn.id
  cidr_block        = "10.0.1.0/24"
  display_name      = "lazy-wombat-public-subnet"
  dns_label         = "public"
  route_table_id    = oci_core_route_table.lazy_wombat_route_table.id
  security_list_ids = [oci_core_security_list.lazy_wombat_security_list.id]
}

# Security List (equivalent to AWS Security Group)
resource "oci_core_security_list" "lazy_wombat_security_list" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lazy_wombat_vcn.id
  display_name   = "lazy-wombat-security-list"

  # Allow all outbound traffic (egress)
  egress_security_rules {
    protocol    = "all"
    destination = "0.0.0.0/0"
    description = "Allow all outbound traffic"
  }

  # SSH (port 22)
  ingress_security_rules {
    protocol    = "6" # TCP
    source      = "0.0.0.0/0"
    description = "SSH access"
    tcp_options {
      min = 22
      max = 22
    }
  }

  # HTTP (port 80)
  ingress_security_rules {
    protocol    = "6" # TCP
    source      = "0.0.0.0/0"
    description = "HTTP access"
    tcp_options {
      min = 80
      max = 80
    }
  }

  # HTTPS (port 443)
  ingress_security_rules {
    protocol    = "6" # TCP
    source      = "0.0.0.0/0"
    description = "HTTPS access"
    tcp_options {
      min = 443
      max = 443
    }
  }
}

# Compute Instance (equivalent to AWS EC2)
resource "oci_core_instance" "lazy_wombat" {
  compartment_id      = var.compartment_ocid
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[0].name
  display_name        = "lazy-wombat-server"
  shape               = var.instance_shape

  shape_config {
    ocpus         = var.instance_ocpus
    memory_in_gbs = var.instance_memory_in_gbs
  }

  source_details {
    source_type = "image"
    source_id   = data.oci_core_images.ubuntu_image.images[0].id
  }

  create_vnic_details {
    subnet_id                 = oci_core_subnet.lazy_wombat_subnet.id
    assign_public_ip          = false  # We'll use a reserved public IP instead
    display_name              = "lazy-wombat-vnic"
    assign_private_dns_record = true
    hostname_label            = "lazywombat"
  }

  metadata = {
    ssh_authorized_keys = file(var.ssh_public_key_path)
  }

  preserve_boot_volume = false
}

# Reserved Public IP (equivalent to AWS Elastic IP)
resource "oci_core_public_ip" "lazy_wombat_public_ip" {
  compartment_id = var.compartment_ocid
  display_name   = "lazy-wombat-public-ip"
  lifetime       = "RESERVED"
  private_ip_id  = data.oci_core_private_ips.lazy_wombat_private_ips.private_ips[0].id
}

# Get private IP of the instance for reserved public IP association
data "oci_core_private_ips" "lazy_wombat_private_ips" {
  ip_address = oci_core_instance.lazy_wombat.private_ip
  subnet_id  = oci_core_subnet.lazy_wombat_subnet.id
}

# Outputs (matching AWS output names where applicable)
output "elastic_ip" {
  description = "The public IP address of the instance"
  value       = oci_core_public_ip.lazy_wombat_public_ip.ip_address
}

output "instance_private_ip" {
  description = "Private IP address of the instance"
  value       = oci_core_instance.lazy_wombat.private_ip
}

output "instance_id" {
  description = "OCID of the compute instance"
  value       = oci_core_instance.lazy_wombat.id
}

output "availability_domain" {
  description = "Availability domain where the instance is deployed"
  value       = oci_core_instance.lazy_wombat.availability_domain
}
