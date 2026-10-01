#!/bin/bash
# The demo VM's startup script (ADR 0010), run as root at every boot. It installs Docker, puts
# the stack in /opt/lecture from the instance's metadata and Secret Manager, and starts it. On
# the first boot it loads the demo lectures with sign-in off, so they're public (ADR 0009),
# then turns sign-in on. Its output is in `journalctl -u google-startup-scripts`; after a
# failure, `sudo google_metadata_script_runner startup` runs it again.
set -euo pipefail

meta() {
  curl -fsS -H "Metadata-Flavor: Google" \
    "http://metadata.google.internal/computeMetadata/v1/instance/$1"
}

if ! command -v docker > /dev/null; then
  apt-get update -q
  apt-get install -yq ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  # shellcheck source=/dev/null
  codename=$(. /etc/os-release && echo "$VERSION_CODENAME")
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc]" \
    "https://download.docker.com/linux/debian $codename stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -yq docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

umask 077
mkdir -p /opt/lecture/caddy
cd /opt/lecture
meta attributes/compose > compose.cloud.yaml
meta attributes/caddyfile > caddy/Caddyfile
# Made on the first boot and kept with the disk, which holds the database.
[ -s postgres.password ] || openssl rand -hex 24 > postgres.password
site="$(meta network-interfaces/0/access-configs/0/external-ip | tr . -).sslip.io"
{
  echo "SITE_ADDRESS=$site"
  echo "IMAGE_TAG=$(meta attributes/image-tag)"
  echo "POSTGRES_PASSWORD=$(cat postgres.password)"
  # A secret may not end in a newline; this adds one either way.
  printf '%s\n' "$(gcloud secrets versions access latest --secret=lecture-demo-storage)"
  printf '%s\n' "$(gcloud secrets versions access latest --secret=lecture-demo-env)"
} > .env

compose() { docker compose -f compose.cloud.yaml "$@"; }

if [ ! -e demo-loaded ]; then
  # Sign-in off (the shell's empty AUTH_ISSUER wins over .env's), and no Caddy: nothing is
  # reachable from outside until sign-in is on. If loading fails, the script stops here.
  AUTH_ISSUER='' compose up -d --wait api worker
  compose exec -T api lecture-demo load
  touch demo-loaded
fi
compose up -d --wait
echo "The demo is up at https://$site"
