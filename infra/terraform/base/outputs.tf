output "bucket" {
  description = "Media, the stage cache and the demo's lectures."
  value       = google_storage_bucket.media.name
}

# For the repository's Actions variables (GCP_WORKLOAD_IDENTITY_PROVIDER, GCP_DEPLOYER).
output "workload_identity_provider" {
  value = google_iam_workload_identity_pool_provider.github.name
}

output "deployer" {
  value = google_service_account.deployer.email
}
