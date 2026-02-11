terraform {
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "~> 6.0"
    }
  }
}

variable "tenancy_ocid" { type = string }
variable "user_ocid" { type = string }
variable "fingerprint" { type = string }
variable "private_key_path" {
  type    = string
  default = "~/.oci/oci_api_key.pem"
}
variable "compartment_ocid" { type = string }
variable "ssh_public_key_path" {
  type    = string
  default = "~/.ssh/id_rsa.pub"
}

provider "oci" {
  tenancy_ocid     = var.tenancy_ocid
  user_ocid        = var.user_ocid
  fingerprint      = var.fingerprint
  private_key_path = var.private_key_path
  region           = "ap-sydney-1"
}

data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

data "oci_core_images" "ubuntu" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "24.04"
  shape                    = "VM.Standard.A1.Flex"
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

resource "oci_core_vcn" "arm_vcn" {
  compartment_id = var.compartment_ocid
  cidr_blocks    = ["10.1.0.0/16"]
  display_name   = "lazy_wombat_arm_vcn"
  dns_label      = "lwarm"
}

resource "oci_core_internet_gateway" "arm_igw" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.arm_vcn.id
  display_name   = "lazy_wombat_arm_igw"
  enabled        = true
}

resource "oci_core_route_table" "arm_rt" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.arm_vcn.id
  display_name   = "lazy_wombat_arm_rt"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.arm_igw.id
  }
}

resource "oci_core_security_list" "arm_sl" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.arm_vcn.id
  display_name   = "lazy_wombat_arm_sl"

  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "all"
    stateless   = false
  }

  ingress_security_rules {
    source    = "0.0.0.0/0"
    protocol  = "6"
    stateless = false
    tcp_options {
      min = 22
      max = 22
    }
  }

  ingress_security_rules {
    source    = "0.0.0.0/0"
    protocol  = "6"
    stateless = false
    tcp_options {
      min = 80
      max = 80
    }
  }

  ingress_security_rules {
    source    = "0.0.0.0/0"
    protocol  = "6"
    stateless = false
    tcp_options {
      min = 443
      max = 443
    }
  }
}

resource "oci_core_subnet" "arm_subnet" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.arm_vcn.id
  cidr_block                 = "10.1.1.0/24"
  display_name               = "lazy_wombat_arm_subnet"
  dns_label                  = "armsubnet"
  prohibit_public_ip_on_vnic = false
  route_table_id             = oci_core_route_table.arm_rt.id
  security_list_ids          = [oci_core_security_list.arm_sl.id]
  availability_domain        = data.oci_identity_availability_domains.ads.availability_domains[0].name
}

resource "oci_core_instance" "arm_instance" {
  compartment_id      = var.compartment_ocid
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[0].name
  display_name        = "lazy_wombat_arm_server"
  shape               = "VM.Standard.A1.Flex"

  shape_config {
    ocpus         = 4
    memory_in_gbs = 24
  }

  source_details {
    source_type             = "image"
    source_id               = data.oci_core_images.ubuntu.images[0].id
    boot_volume_size_in_gbs = 100
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.arm_subnet.id
    assign_public_ip = false
    display_name     = "lazy_wombat_arm_vnic"
    hostname_label   = "lwarm"
  }

  metadata = {
    ssh_authorized_keys = file(var.ssh_public_key_path)
  }

  preserve_boot_volume = false
}

data "oci_core_vnic_attachments" "arm_vnic_attachments" {
  compartment_id = var.compartment_ocid
  instance_id    = oci_core_instance.arm_instance.id
}

data "oci_core_private_ips" "arm_private_ips" {
  vnic_id = data.oci_core_vnic_attachments.arm_vnic_attachments.vnic_attachments[0].vnic_id
}

resource "oci_core_public_ip" "arm_public_ip" {
  compartment_id = var.compartment_ocid
  lifetime       = "RESERVED"
  display_name   = "lazy_wombat_arm_public_ip"
  private_ip_id  = data.oci_core_private_ips.arm_private_ips.private_ips[0].id
}

output "server_ip_addr" {
  value = oci_core_public_ip.arm_public_ip.ip_address
}
