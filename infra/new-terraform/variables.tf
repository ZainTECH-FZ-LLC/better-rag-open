variable "pg_password" {
  description = "PostgreSQL password"
  type        = string
  sensitive   = true
}

variable "pg_admin_password" {
  description = "Postgresql Admin password"
  type = string
  sensitive = true
}

variable "neo4j_password" {
  description = "Neo4j password"
  type = string
  sensitive = true
}


variable "allowed_ips" {
  type    = list(string)
  default = ["14.1.106.21"]  # IPs that are whitelisted for resource access
}
