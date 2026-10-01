output "url" {
  description = "Up once the startup script has loaded the demo lectures, about 10 minutes in."
  value       = "https://${replace(google_compute_instance.demo.network_interface[0].access_config[0].nat_ip, ".", "-")}.sslip.io"
}
