terraform {
  required_providers {
    vultr = { source = "vultr/vultr", version = "~> 2.0" }
  }
}
provider "vultr" {}

# Vultr has no VPC to discover: instances are reachable on their public address
# and the firewall group is what keeps everything but Caddy and SSH off it. The
# datastores are already confined to the private Compose network, so this is a
# second layer rather than the only one.
resource "vultr_firewall_group" "rybbit" {
  description = "rybbit-vultr-fixture"
}

# The rules are generated into firewall.tf.json rather than looped here: Vultr
# takes one resource per protocol, address family and port, splitting a CIDR
# into a separate address and prefix length, so the rule count depends on how
# many sources desired state lists. Vultr firewalls are default-deny inbound
# and model no outbound at all, which is why -- unlike the DigitalOcean
# template -- there are no outbound rules to write.

resource "vultr_instance" "rybbit" {
  # `label` is the console name and updates in place. There is deliberately no
  # `hostname`: Vultr implements a hostname change as an OS reinstall, so the
  # attribute is ForceNew and editing vultr-name would destroy the disk holding
  # ClickHouse and PostgreSQL rather than rename the machine. The playbook sets
  # the hostname on the running system instead.
  label = "rybbit-vultr-fixture"

  region = "ams"
  plan   = "vc2-2c-4gb"
  os_id  = 2284

  # ssh_key_ids is ForceNew too: changing the key set recreates the instance
  # instead of re-authorizing it, so a disposable key lasts the life of the
  # deployment and is rotated by rebuilding.
  ssh_key_ids = ["faa53dae-f289-4bba-bf90-8997131ca40a"]

  firewall_group_id = vultr_firewall_group.rybbit.id
  enable_ipv6       = true
  activation_email  = false

  lifecycle { prevent_destroy = true }
}

output "params" {
  # main_ip, not ipv4_address. Naming the DigitalOcean attribute here fails as
  # an unreachable host rather than as a missing output.
  value = { ip = vultr_instance.rybbit.main_ip, user = "root", sudoer = "root", name = "rybbit-vultr-fixture" }
}
