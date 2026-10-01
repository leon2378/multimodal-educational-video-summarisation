variable "project_id" {
  description = "The Google Cloud project, as in ../base."
  type        = string
}

variable "region" {
  description = "As in ../base: the VM goes in its subnetwork."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "Defaults to the region's zone a."
  type        = string
  default     = null
}

variable "machine_type" {
  description = "2 vCPUs and 8 GB fit the stack; about $0.07 an hour in us-central1."
  type        = string
  default     = "e2-standard-2"
}

variable "image_tag" {
  description = "The release whose images to run (pushed to GHCR by .github/workflows/release.yml)."
  type        = string
}
