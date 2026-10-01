variable "project_id" {
  description = "The Google Cloud project, the one Gemini already bills to."
  type        = string
}

variable "region" {
  description = "Where the bucket, network and VM live."
  type        = string
  default     = "us-central1"
}

variable "state_bucket" {
  description = "The bucket holding Terraform's state, made by `make cloud-base`."
  type        = string
}

variable "github_repository" {
  description = "The repository whose workflows may deploy, as owner/name."
  type        = string
  default     = "leon2378/multimodal-educational-video-summarisation"
}
