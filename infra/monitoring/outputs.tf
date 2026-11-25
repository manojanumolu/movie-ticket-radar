output "shadow_worker_url" {
  value = google_cloud_run_v2_service.worker.uri
}
