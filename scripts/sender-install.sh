#!/bin/bash
set -e

USER_NAME=${SUDO_USER}

if [ -z "$USER_NAME" ]; then
  echo "Run with sudo"
  exit 1
fi

# Stop service if exists
if systemctl list-unit-files | grep -q milkpi-sender; then
  echo "Removing old MilkPi Sender..."

  systemctl stop milkpi-sender || true
  systemctl disable milkpi-sender || true

  systemctl stop milkpi-listener || true
  systemctl disable milkpi-listener || true

  # Remove systemd unit
  rm -f /etc/systemd/system/milkpi-sender.service
  rm -f /etc/systemd/system/milkpi-listener.service

  # Reload systemd
  systemctl daemon-reload
  systemctl reset-failed || true

  # Remove app + venv
  rm -rf /opt/milkpi/sender
  rm -rf /opt/milkpi/venv

  echo "Old install removed."
fi

echo "Installing MilkPi Sender for $USER_NAME"

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
apt install -y libpam0g-dev python3-venv python3-pip python3-evdev avahi-utils

# Permissions
usermod -aG input $USER_NAME

# App
mkdir -p /opt/milkpi/sender
cp -r sender/* /opt/milkpi/sender

chown -R $USER_NAME:$USER_NAME /opt/milkpi

# Python deps
python3 -m venv /opt/milkpi/venv

/opt/milkpi/venv/bin/pip install --upgrade pip
/opt/milkpi/venv/bin/pip install -r /opt/milkpi/sender/requirements.txt

# systemd
cp sender/*.service /etc/systemd/system/

sed -i "s/%i/$USER_NAME/" /etc/systemd/system/milkpi-*.service

systemctl daemon-reload
systemctl enable milkpi-sender
systemctl enable milkpi-listener

systemctl start milkpi-sender
systemctl start milkpi-listener

echo "Sender installed."
echo "Reboot recommended."