terraform {
  required_version = "= 1.13.9"
}
variable "request_command" { type = string }
variable "spec" { type = any }
variable "image_sha256" { type = string }
resource "terraform_data" "vm" {
  input = { spec = var.spec, image_sha256 = var.image_sha256 }
  provisioner "local-exec" {
    command = var.request_command
    interpreter = ["/bin/sh", "-c"]
  }
}
