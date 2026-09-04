#!/usr/bin/env bash
# Cài snippet CORS widget Chatwoot (nhiều domain khách, không hardcode).
# Dùng trên MỌI host nginx trước Chatwoot (dev + prod).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/nginx/snippets"
DEST="${CHATWOOT_NGINX_SNIPPETS:-/etc/nginx/snippets}"
VHOST="${CHATWOOT_NGINX_VHOST:-}"

mkdir -p "$DEST"
cp -f "$SRC/chatwoot_widget_cors_map.conf" "$DEST/"
cp -f "$SRC/chatwoot_widget_cors_locations.conf" "$DEST/"
echo "Copied snippets → $DEST"

if [[ -z "$VHOST" ]]; then
  for cand in \
    /etc/nginx/sites-available/devchat.telesip.vn \
    /etc/nginx/sites-available/chat.telesip.vn \
    /etc/nginx/sites-available/chatwoot \
    /etc/nginx/conf.d/chatwoot.conf
  do
    if [[ -f "$cand" ]]; then
      VHOST="$cand"
      break
    fi
  done
fi

if [[ -z "${VHOST:-}" || ! -f "${VHOST:-}" ]]; then
  echo "Không thấy vhost Chatwoot trên máy này."
  echo "Trên server prod Chatwoot:"
  echo "  1) copy 2 file snippets vào /etc/nginx/snippets/"
  echo "  2) đầu file vhost (http context): include /etc/nginx/snippets/chatwoot_widget_cors_map.conf;"
  echo "  3) trong server 443, TRƯỚC location / :"
  echo "       include /etc/nginx/snippets/chatwoot_widget_cors_locations.conf;"
  echo "  4) nginx -t && systemctl reload nginx"
  exit 0
fi

echo "Vhost: $VHOST"
if ! grep -q 'chatwoot_widget_cors_map.conf' "$VHOST"; then
  echo "WARN: $VHOST chưa include map. Thêm dòng (ngoài server {}):"
  echo "  include /etc/nginx/snippets/chatwoot_widget_cors_map.conf;"
fi
if ! grep -q 'chatwoot_widget_cors_locations.conf' "$VHOST" && ! grep -q 'location \^~ /api/v1/widget/' "$VHOST"; then
  echo "WARN: $VHOST chưa có location widget CORS. Thêm trong server 443:"
  echo "  include /etc/nginx/snippets/chatwoot_widget_cors_locations.conf;"
fi

nginx -t
systemctl reload nginx
echo "nginx reloaded"
