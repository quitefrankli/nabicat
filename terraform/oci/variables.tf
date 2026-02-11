variable "tenancy_ocid" {
  description = "OCID of the tenancy"
  type        = string
}

variable "user_ocid" {
  description = "OCID of the user"
  type        = string
}

variable "fingerprint" {
  description = "Fingerprint of the API key"
  type        = string
}

variable "private_key_path" {
  description = "Path to the private key file"
  type        = string
  default     = "~/.oci/oci_api_key.pem"
}

variable "region" {
  description = "OCI region"
  type        = string
  default     = "ap-sydney-1"
}

variable "compartment_ocid" {
  description = "OCID of the compartment (use tenancy_ocid for root compartment)"
  type        = string
}

variable "ssh_public_key_path" {
  description = "Path to SSH public key"
  type        = string
  default     = "~/.ssh/id_rsa.pub"
}

variable "instance_shape" {
  description = "Shape of the compute instance"
  type        = string
  default     = "VM.Standard.A1.Flex"  # ARM-based, Always Free eligible
}

variable "instance_ocpus" {
  description = "Number of OCPUs (max 4 for Always Free)"
  type        = number
  default     = 4
}

variable "instance_memory_gb" {
  description = "Memory in GB (max 24 for Always Free)"
  type        = number
  default     = 24
}

variable "boot_volume_size_gb" {
  description = "Boot volume size in GB (max 200 total for Always Free)"
  type        = number
  default     = 100
}
