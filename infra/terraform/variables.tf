variable "resource_group_name" {
  description = "Name of the Azure resource group"
  type        = string
  default     = "rg-better-rag"
}

variable "location" {
  description = "Azure region"
  type        = string
  default     = "eastus2"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "prod"
}

variable "cluster_name" {
  description = "AKS cluster name"
  type        = string
  default     = "better-rag-aks"
}

variable "kubernetes_version" {
  description = "Kubernetes version"
  type        = string
  default     = "1.30"
}

variable "acr_name" {
  description = "Azure Container Registry name (globally unique)"
  type        = string
  default     = "betterragacr"
}

variable "pg_admin_password" {
  description = "PostgreSQL admin password"
  type        = string
  sensitive   = true
}

variable "neo4j_password" {
  description = "Neo4j password"
  type        = string
  sensitive   = true
}

variable "graph_client_secret" {
  description = "Microsoft Graph client secret"
  type        = string
  sensitive   = true
}

variable "anthropic_api_key" {
  description = "Anthropic API key"
  type        = string
  sensitive   = true
  default     = ""
}

variable "azure_openai_api_key" {
  description = "Azure OpenAI API key"
  type        = string
  sensitive   = true
}

variable "azure_openai_endpoint" {
  description = "Azure OpenAI endpoint URL"
  type        = string
}

variable "ocr_azure_key" {
  description = "Azure Document Intelligence key"
  type        = string
  sensitive   = true
}

variable "ocr_azure_endpoint" {
  description = "Azure Document Intelligence endpoint"
  type        = string
}

variable "dns_zone_name" {
  description = "Azure DNS zone for TLS certificates"
  type        = string
  default     = ""
}

variable "dns_zone_resource_group" {
  description = "Resource group containing the DNS zone"
  type        = string
  default     = ""
}

variable "domain_name" {
  description = "Public domain for the application"
  type        = string
  default     = "rag.contoso.com"
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default = {
    project     = "better-rag"
    managed_by  = "terraform"
  }
}
