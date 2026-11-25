variable "project_id" {
  type = string
  validation {
    condition     = can(regex("^radar-staging-[a-z0-9-]+$", var.project_id))
    error_message = "Use an isolated radar-staging-* project."
  }
}
variable "region" {
  type = string
}
variable "worker_image" {
  type        = string
  description = "Artifact Registry worker image pinned by digest."
  validation {
    condition     = can(regex("-docker[.]pkg[.]dev/[^@]+@sha256:[a-f0-9]{64}$", var.worker_image))
    error_message = "Provide an Artifact Registry image pinned by SHA-256."
  }
}
