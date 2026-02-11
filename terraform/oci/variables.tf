# OCI Provider Variables
# These can be set via environment variables or terraform.tfvars

variable "tenancy_ocid" {
  description = "OCID of the OCI tenancy"
  type        = string
}

variable "user_ocid" {
  description = "OCID of the OCI user"
  type        = string
}

variable "fingerprint" {
  description = "Fingerprint of the OCI API key"
  type        = string
}

variable "private_key_path" {
  description = "Path to the OCI API private key"
  type        = string
  default     = "~/.oci/oci_api_key.pem"
}

variable "region" {
  description = "OCI region"
  type        = string
  default     = "ap-sydney-1"
}

variable "compartment_ocid" {
  description = "OCID of the compartment to create resources in"
  type        = string
}

variable "ssh_public_key_path" {
  description = "Path to the SSH public key for instance access"
  type        = string
  default     = "~/.ssh/id_rsa.pub"
}

variable "instance_shape" {
  description = "OCI compute instance shape"
  type        = string
  default     = "VM.Standard.A1.Flex"  # Always Free eligible
}

variable "instance_ocpus" {
  description = "Number of OCPUs for the instance"
  type        = number
  default     = 1
}

variable "instance_memory_in_gbs" {
  description = "Amount of memory in GBs for the instance"
  type        = number
  default     = 1
}

variable "budget_amount" {
  description = "Monthly budget amount in USD"
  type        = number
  default     = 5
}

variable "notification_email" {
  description = "Email address for budget alerts"
  type        = string
  default     = "quitefrankli@hotmail.com"
}
