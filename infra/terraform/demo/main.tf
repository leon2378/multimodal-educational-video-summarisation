# The demo's VM (ADR 0010), made for a session by `make deploy` or the deploy workflow and
# deleted after by `make destroy`. Everything that outlasts a session is in ../base.

terraform {
  required_version = ">= 1.10"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.5"
    }
  }
  backend "gcs" {
    prefix = "demo"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

data "google_service_account" "vm" {
  account_id = "lecture-demo-vm"
}

data "google_compute_subnetwork" "demo" {
  name   = "lecture-demo"
  region = var.region
}

resource "google_compute_instance" "demo" {
  name         = "lecture-demo"
  zone         = coalesce(var.zone, "${var.region}-a")
  machine_type = var.machine_type
  tags         = ["lecture-demo"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 30
      type  = "pd-balanced"
    }
  }

  network_interface {
    subnetwork = data.google_compute_subnetwork.demo.self_link
    # An ephemeral public IP, which names the demo: <ip with dashes>.sslip.io.
    access_config {}
  }

  service_account {
    email  = data.google_service_account.vm.email
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  # The startup script reads the rest from here, then the secrets from Secret Manager.
  metadata = {
    startup-script = file("${path.module}/startup.sh")
    compose        = file("${path.module}/../../compose.cloud.yaml")
    caddyfile      = file("${path.module}/../../caddy/Caddyfile")
    image-tag      = var.image_tag
    enable-oslogin = "TRUE"
  }
}
