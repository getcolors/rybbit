terraform {
  required_providers {
    digitalocean = { source = "digitalocean/digitalocean", version = "~> 2.0" }
  }
}
provider "digitalocean" {}

# Discover the configured region's account default at plan/apply time. The UUID
# is deliberately neither configured nor persisted in colors.yml.
data "digitalocean_vpc" "default" {
  name = "default-<{ digitalocean-region }>"
}

resource "digitalocean_droplet" "rybbit" {
  name     = "<{ digitalocean-name }>"
  region   = "<{ digitalocean-region }>"
  size     = "<{ digitalocean-size }>"
  # DigitalOcean cannot shrink a disk, so the provider default of
  # resizing it too makes any downsize fail with "This size is not
  # available because it has a smaller disk". False is the CPU-and-RAM
  # only resize: the droplet keeps the disk it has, so moving down a
  # plan is possible and never destroys the volume.
  resize_disk = <{ digitalocean-resize-disk }>
  image    = "<{ digitalocean-image }>"
  vpc_uuid = data.digitalocean_vpc.default.id
  ssh_keys = ["<{ digitalocean-ssh-keys }>"]
  lifecycle { prevent_destroy = <{ compute-prevent-destroy }> }
}

resource "digitalocean_firewall" "rybbit" {
  name        = "<{ digitalocean-name }>-firewall"
  droplet_ids = [digitalocean_droplet.rybbit.id]
  inbound_rule {
    protocol         = "tcp"
    port_range       = "22"
    source_addresses = <{ ssh-sources-hcl|safe }>
  }
  inbound_rule {
    protocol         = "tcp"
    port_range       = "80"
    source_addresses = <{ http-sources-hcl|safe }>
  }
  inbound_rule {
    protocol         = "tcp"
    port_range       = "443"
    source_addresses = <{ http-sources-hcl|safe }>
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
  lifecycle { prevent_destroy = <{ compute-prevent-destroy }> }
}

output "params" {
  value = { ip = digitalocean_droplet.rybbit.ipv4_address, user = "root", sudoer = "root", name = "<{ profile }>" }
}
