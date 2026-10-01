# What the demo keeps between sessions (ADR 0010), applied once with `make cloud-base`: the
# bucket for media and the stage cache, the VM's identity and secrets, its network, and the
# access GitHub Actions deploys with. The VM itself is ../demo, made and deleted per session.

terraform {
  required_version = ">= 1.10"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.5"
    }
  }
  # The state bucket is made before the first apply (`make cloud-base`) and passed at init.
  backend "gcs" {
    prefix = "base"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_project_service" "apis" {
  for_each = toset([
    "compute.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
    "sts.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# The VM's identity: it reads the demo's secrets and uses the bucket, and nothing else.
resource "google_service_account" "vm" {
  account_id   = "lecture-demo-vm"
  display_name = "Lecture summariser demo VM"
  depends_on   = [google_project_service.apis]
}

# Media, the stage cache and the demo's lectures (lecture_core.storage has the layout).
resource "google_storage_bucket" "media" {
  name                        = "${var.project_id}-lectures"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  # Browsers upload and play straight from the bucket, through presigned URLs, from whichever
  # address the demo has this session.
  cors {
    origin          = ["*"]
    method          = ["GET", "HEAD", "PUT"]
    response_header = ["Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"]
    max_age_seconds = 3600
  }
  depends_on = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "vm" {
  bucket = google_storage_bucket.media.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.vm.email}"
}

# The app reaches storage through its S3-compatible API, with an HMAC key of the VM's account.
resource "google_storage_hmac_key" "vm" {
  service_account_email = google_service_account.vm.email
}

# Settings the VM's startup script adds to the stack's .env: storage from here, and the app's
# own secrets (Gemini, Clerk, Modal) from infra/cloud.env through `make cloud-secrets`.
resource "google_secret_manager_secret" "storage" {
  secret_id = "lecture-demo-storage"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "storage" {
  secret      = google_secret_manager_secret.storage.id
  secret_data = <<-EOT
    S3_ENDPOINT_URL=https://storage.googleapis.com
    S3_REGION=auto
    S3_BUCKET=${google_storage_bucket.media.name}
    S3_ACCESS_KEY_ID=${google_storage_hmac_key.vm.access_id}
    S3_SECRET_ACCESS_KEY=${google_storage_hmac_key.vm.secret}
  EOT
}

resource "google_secret_manager_secret" "app" {
  secret_id = "lecture-demo-env"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "vm" {
  for_each  = { storage = google_secret_manager_secret.storage.id, app = google_secret_manager_secret.app.id }
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.vm.email}"
}

# Its own network, open to the web on 80 and 443 only, and to SSH through Google's IAP proxy
# (`gcloud compute ssh --tunnel-through-iap`), not from the internet.
resource "google_compute_network" "demo" {
  name                    = "lecture-demo"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "demo" {
  name          = "lecture-demo"
  region        = var.region
  network       = google_compute_network.demo.id
  ip_cidr_range = "10.10.0.0/24"
}

resource "google_compute_firewall" "web" {
  name          = "lecture-demo-web"
  network       = google_compute_network.demo.id
  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["lecture-demo"]
  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }
}

resource "google_compute_firewall" "iap_ssh" {
  name          = "lecture-demo-iap-ssh"
  network       = google_compute_network.demo.id
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["lecture-demo"]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

# GitHub Actions deploys through Workload Identity Federation: a run of this repository's
# workflows gets short-lived credentials, so no service account key exists.
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }
  attribute_condition = "assertion.repository == '${var.github_repository}'"
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "deployer" {
  account_id   = "lecture-demo-deployer"
  display_name = "Deploys the lecture summariser demo from GitHub Actions"
  depends_on   = [google_project_service.apis]
}

resource "google_service_account_iam_member" "deployer_github" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

# Enough to make and delete the VM, run it as the VM's account, keep the Terraform state, and
# clear a session's uploads from the bucket.
resource "google_project_iam_member" "deployer_compute" {
  project = var.project_id
  role    = "roles/compute.instanceAdmin.v1"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_service_account_iam_member" "deployer_runs_vm" {
  service_account_id = google_service_account.vm.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_storage_bucket_iam_member" "deployer" {
  for_each = toset([google_storage_bucket.media.name, var.state_bucket])
  bucket   = each.value
  role     = "roles/storage.objectAdmin"
  member   = "serviceAccount:${google_service_account.deployer.email}"
}
