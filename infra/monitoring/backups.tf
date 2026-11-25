variable "enable_shadow_backup_schedule" {
  type        = bool
  default     = false
  description = "Opt in only after reviewing existing schedules and staging budget."
}

resource "google_firestore_backup_schedule" "daily" {
  count     = var.enable_shadow_backup_schedule ? 1 : 0
  project   = var.project_id
  database  = "(default)"
  retention = "1209600s"

  daily_recurrence {}

  lifecycle {
    prevent_destroy = true
  }
}
