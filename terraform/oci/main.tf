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

# Fetch availability domains
data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

# Get latest Ubuntu 22.04 image
data "oci_core_images" "ubuntu" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "24.04"
  shape                    = var.instance_shape
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

locals {
  is_flex_shape = length(regexall("Flex", var.instance_shape)) > 0
}

# VCN
resource "oci_core_vcn" "lazy_wombat_vcn" {
  compartment_id = var.compartment_ocid
  cidr_blocks    = ["10.0.0.0/16"]
  display_name   = "lazy_wombat_vcn"
  dns_label      = "lazywombat"
}

# Internet Gateway
resource "oci_core_internet_gateway" "lazy_wombat_igw" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lazy_wombat_vcn.id
  display_name   = "lazy_wombat_igw"
  enabled        = true
}

# Route Table
resource "oci_core_route_table" "lazy_wombat_rt" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lazy_wombat_vcn.id
  display_name   = "lazy_wombat_rt"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.lazy_wombat_igw.id
  }
}

# Security List
resource "oci_core_security_list" "lazy_wombat_sl" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lazy_wombat_vcn.id
  display_name   = "lazy_wombat_sl"

  # Allow all egress
  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "all"
    stateless   = false
  }

  # SSH
  ingress_security_rules {
    source    = "0.0.0.0/0"
    protocol  = "6" # TCP
    stateless = false
    tcp_options {
      min = 22
      max = 22
    }
  }

  # HTTP
  ingress_security_rules {
    source    = "0.0.0.0/0"
    protocol  = "6" # TCP
    stateless = false
    tcp_options {
      min = 80
      max = 80
    }
  }

  # HTTPS
  ingress_security_rules {
    source    = "0.0.0.0/0"
    protocol  = "6" # TCP
    stateless = false
    tcp_options {
      min = 443
      max = 443
    }
  }

  # ICMP (ping)
  ingress_security_rules {
    source    = "0.0.0.0/0"
    protocol  = "1" # ICMP
    stateless = false
    icmp_options {
      type = 3
      code = 4
    }
  }

  ingress_security_rules {
    source    = "10.0.0.0/16"
    protocol  = "1" # ICMP
    stateless = false
    icmp_options {
      type = 3
    }
  }
}

# Subnet
resource "oci_core_subnet" "lazy_wombat_subnet" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.lazy_wombat_vcn.id
  cidr_block                 = "10.0.1.0/24"
  display_name               = "lazy_wombat_subnet"
  dns_label                  = "subnet"
  prohibit_public_ip_on_vnic = false
  route_table_id             = oci_core_route_table.lazy_wombat_rt.id
  security_list_ids          = [oci_core_security_list.lazy_wombat_sl.id]
  availability_domain        = data.oci_identity_availability_domains.ads.availability_domains[0].name
}

# Compute Instance
resource "oci_core_instance" "lazy_wombat" {
  compartment_id      = var.compartment_ocid
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[0].name
  display_name        = "lazy_wombat_server"
  shape               = var.instance_shape

  dynamic "shape_config" {
    for_each = local.is_flex_shape ? [1] : []
    content {
      ocpus         = var.instance_ocpus
      memory_in_gbs = var.instance_memory_gb
    }
  }

  source_details {
    source_type             = "image"
    source_id               = data.oci_core_images.ubuntu.images[0].id
    boot_volume_size_in_gbs = var.boot_volume_size_gb
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.lazy_wombat_subnet.id
    assign_public_ip = false
    display_name     = "lazy_wombat_vnic"
    hostname_label   = "lazywombat"
  }

  metadata = {
    ssh_authorized_keys = file(var.ssh_public_key_path)
  }

  preserve_boot_volume = false
}

# Get VNIC attachment
data "oci_core_vnic_attachments" "lazy_wombat_vnic_attachments" {
  compartment_id = var.compartment_ocid
  instance_id    = oci_core_instance.lazy_wombat.id
}

# Get private IP
data "oci_core_private_ips" "lazy_wombat_private_ips" {
  vnic_id = data.oci_core_vnic_attachments.lazy_wombat_vnic_attachments.vnic_attachments[0].vnic_id
}

# Reserved Public IP
resource "oci_core_public_ip" "lazy_wombat_public_ip" {
  compartment_id = var.compartment_ocid
  lifetime       = "RESERVED"
  display_name   = "lazy_wombat_public_ip"
  private_ip_id  = data.oci_core_private_ips.lazy_wombat_private_ips.private_ips[0].id
}

output "server_ip_addr" {
  value       = oci_core_public_ip.lazy_wombat_public_ip.ip_address
  description = "Reserved public IP of the instance"
}

output "instance_ocid" {
  value       = oci_core_instance.lazy_wombat.id
  description = "OCID of the instance"
}
