resource "google_service_account" "runtime" {
  account_id   = "radar-shadow-worker"
  display_name = "Radar shadow worker without Firebase or email permissions"
}

resource "google_service_account" "scheduler" {
  account_id   = "radar-shadow-invoker"
  display_name = "Radar shadow scheduler invoker"
}

resource "google_cloud_run_v2_service" "worker" {
  name                = "radar-shadow-worker"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = true

  template {
    service_account                  = google_service_account.runtime.email
    timeout                          = "55s"
    max_instance_request_concurrency = 4
    scaling {
      max_instance_count = 1
    }
    containers {
      image = var.worker_image
      env {
        name  = "WORKER_MODE"
        value = "shadow"
      }
    }
  }
  lifecycle {
    precondition {
      condition     = strcontains(var.worker_image, "/${var.project_id}/")
      error_message = "The image must come from the approved staging project."
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "scheduler" {
  name     = google_cloud_run_v2_service.worker.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_scheduler_job" "tick" {
  name             = "radar-shadow-tick"
  region           = var.region
  schedule         = "* * * * *"
  time_zone        = "Etc/UTC"
  paused           = true
  attempt_deadline = "55s"

  http_target {
    uri         = "${google_cloud_run_v2_service.worker.uri}/tick"
    http_method = "POST"
    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience              = google_cloud_run_v2_service.worker.uri
    }
  }
  depends_on = [google_cloud_run_v2_service_iam_member.scheduler]
}
