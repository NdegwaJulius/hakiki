#!/usr/bin/env bash
# Run once on a fresh Ubuntu 22.04/24.04 VPS as root.
# After this script: push to main and GitHub Actions handles all future deploys.
set -e

APP_DIR=/opt/hakiki
APP_USER=hakiki
REPO_URL=https://github.com/NdegwaJulius/hakiki.git  # ← update this

# ── 1. System packages ────────────────────────────────────────────────────────
apt-get update -qq
apt-get install -y -qq python3.12 python3.12-venv python3-pip git nginx certbot python3-certbot-nginx

# ── 2. App user ───────────────────────────────────────────────────────────────
id -u $APP_USER &>/dev/null || useradd --system --shell /usr/sbin/nologin --home $APP_DIR $APP_USER

# ── 3. Clone repo ─────────────────────────────────────────────────────────────
mkdir -p $APP_DIR
git clone $REPO_URL $APP_DIR
chown -R $APP_USER:$APP_USER $APP_DIR

# ── 4. Python env + deps ──────────────────────────────────────────────────────
cd $APP_DIR
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -q

# ── 5. .env file (fill in secrets after this script runs) ────────────────────
cp .env.example .env
echo ""
echo "  ⚠️  Edit /opt/hakiki/.env with your real values before starting the service"
echo "     Minimum required: SECRET_KEY, ADMIN_EMAIL, DATABASE_URL, APP_URL"
echo ""

# ── 6. Systemd service ────────────────────────────────────────────────────────
cp deploy/hakiki.service /etc/systemd/system/hakiki.service
# Allow hakiki user to restart its own service without full sudo
echo "$APP_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart hakiki, /bin/systemctl is-active hakiki" \
  > /etc/sudoers.d/hakiki
systemctl daemon-reload
systemctl enable hakiki

# ── 7. Nginx ──────────────────────────────────────────────────────────────────
cp deploy/nginx.conf /etc/nginx/sites-available/hakiki
ln -sf /etc/nginx/sites-available/hakiki /etc/nginx/sites-enabled/hakiki
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# ── 8. SSL with Let's Encrypt ─────────────────────────────────────────────────
echo ""
echo "  Run these two commands to get SSL certificates:"
echo "    certbot --nginx -d keradata.plunlix.co.ke"
echo "    certbot --nginx -d api.plunlix.co.ke"
echo ""
echo "  Then start the app:"
echo "    nano /opt/hakiki/.env        # fill in real values"
echo "    systemctl start hakiki"
echo "    systemctl status hakiki"
echo ""
echo "  Done! Push to main to trigger auto-deploy."
