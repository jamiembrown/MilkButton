#!/bin/bash
set -e

USER_NAME=${SUDO_USER}

if [ -z "$USER_NAME" ]; then
  echo "Run with sudo"
  exit 1
fi

echo "Installing MilkPi Player for $USER_NAME"

wait_for_apt() {
  while fuser /var/lib/apt/lists/lock >/dev/null 2>&1 \
     || fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 \
     || fuser /var/lib/dpkg/lock >/dev/null 2>&1; do
    echo "Waiting for apt lock..."
    sleep 3
  done
}

wait_for_apt

# Packages
apt update
apt install -y libpam0g-dev python3-venv python3-pip mpg123 avahi-daemon avahi-utils

# App
mkdir -p /opt/milkpi/player
cp -r player/* /opt/milkpi/player

chown -R $USER_NAME:$USER_NAME /opt/milkpi

# Python deps
python3 -m venv /opt/milkpi/venv

/opt/milkpi/venv/bin/pip install --upgrade pip
/opt/milkpi/venv/bin/pip install -r /opt/milkpi/player/requirements.txt

# Avahi
cp player/avahi.milkpi.service /etc/avahi/services/
systemctl restart avahi-daemon

# systemd
cp player/milkpi-player.service /etc/systemd/system/

sed -i "s/%i/$USER_NAME/" /etc/systemd/system/milkpi-player.service

systemctl daemon-reload
systemctl enable milkpi-player
systemctl start milkpi-player

echo "Player installed."