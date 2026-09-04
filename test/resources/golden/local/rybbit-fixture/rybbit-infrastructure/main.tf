terraform {
  required_providers {
    digitalocean = { source = "digitalocean/digitalocean", version = "~> 2.0" }
  }
}
provider "digitalocean" {}

# Discover the configured region's account default at plan/apply time. The UUID
# is deliberately neither configured nor persisted in colors.yml.
data "digitalocean_vpc" "default" {
  name = "default-ams3"
}

resource "digitalocean_droplet" "rybbit" {
  name     = "rybbit-fixture"
  region   = "ams3"
  size     = "s-4vcpu-8gb"
  image    = "ubuntu-24-04-x64"
  vpc_uuid = data.digitalocean_vpc.default.id
  ssh_keys = ["58495393"]
  lifecycle { prevent_destroy = true }
}

resource "digitalocean_firewall" "rybbit" {
  name        = "rybbit-fixture-firewall"
  droplet_ids = [digitalocean_droplet.rybbit.id]
  inbound_rule {
    protocol         = "tcp"
    port_range       = "22"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }
  # 80 and 443 from the HTTP sources, and nothing else. A rule with no source
  # is not "closed" to DigitalOcean but an API error, so the HTTP rules are
  # emitted only when there is a source to name; an empty http-sources list
  # means no public HTTP at all. UDP 443 carries HTTP/3, which Caddy advertises
  # via alt-svc whether or not the port is reachable -- the Vultr template's
  # generated rules open the same hole (the "quic" rule in vultr-firewall-json).
  dynamic "inbound_rule" {
    for_each = length(["0.0.0.0/0", "::/0"]) > 0 ? [
      { protocol = "tcp", port_range = "80" },
      { protocol = "tcp", port_range = "443" },
      { protocol = "udp", port_range = "443" },
    ] : []
    content {
      protocol         = inbound_rule.value.protocol
      port_range       = inbound_rule.value.port_range
      source_addresses = ["0.0.0.0/0", "::/0"]
    }
  }
  outbound_rule {
    protocol              = "tcp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
  outbound_rule {
    protocol              = "udp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
  outbound_rule {
    protocol              = "icmp"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
  lifecycle { prevent_destroy = true }
}

output "params" {
  value = { provider = "digitalocean", ip = digitalocean_droplet.rybbit.ipv4_address, user = "root", sudoer = "root", name = "rybbit-fixture" }
}
