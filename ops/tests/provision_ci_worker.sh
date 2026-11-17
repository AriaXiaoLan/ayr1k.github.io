#!/bin/bash
# Disposable GitHub-hosted runner only. Production provisioning is documented.
set -euo pipefail
test "${GITHUB_ACTIONS:-}" = true
test "$#" = 1
test -d "$1/node/bin"
test ! -e /srv/amber-web-build
test ! -e /srv/amber-web-build-jail
test ! -e /srv/amber-web-requests
test ! -e /opt/amber-web-tools
caller=$(id -un)
caller_group=$(id -gn)
sudo useradd --system --user-group --no-create-home --shell /usr/sbin/nologin amber-build
sudo useradd --system --user-group --no-create-home --shell /usr/sbin/nologin amber-fetch
sudo install -d -o root -g root -m 755 /opt/amber-web-tools /usr/local/lib/amber-sync
sudo cp -a "$1/." /opt/amber-web-tools/
sudo chown -R root:root /opt/amber-web-tools
sudo chmod -R a+rX,go-w /opt/amber-web-tools
sudo install -m 644 ops/amber_sync.py ops/site_builder.py ops/build_worker.py ops/storage.py ops/network_guard.py ops/registry_proxy.py /usr/local/lib/amber-sync/
sudo install -d -o "$caller" -g amber-build -m 2750 /srv/amber-web-requests
sudo install -d -o amber-build -g "$caller_group" -m 2750 /srv/amber-web-build /srv/amber-web-build/results
sudo install -d -o root -g root -m 755 /srv/amber-web-build-jail
printf '%s\n' 'AMBER build jail v1' | sudo tee /srv/amber-web-build-jail/.amber-build-jail >/dev/null
sudo chmod 444 /srv/amber-web-build-jail/.amber-build-jail
sudo ln -s usr/bin /srv/amber-web-build-jail/bin
sudo ln -s usr/sbin /srv/amber-web-build-jail/sbin
sudo ln -s usr/lib /srv/amber-web-build-jail/lib
sudo ln -s usr/lib64 /srv/amber-web-build-jail/lib64
sudo install -m 644 ops/systemd/amber-web-build.service ops/systemd/amber-web-build.path ops/systemd/amber-web-install.service ops/systemd/amber-fetch-proxy.service ops/systemd/amber-fetch-proxy.socket /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start amber-fetch-proxy.socket
sudo systemctl start amber-web-build.path
