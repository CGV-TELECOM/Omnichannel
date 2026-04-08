#!/bin/bash

# Script để cài đặt SSL certificate cho devomnichannelcgv.telesip.vn

DOMAIN="devomnichannelcgv.telesip.vn"
EMAIL="admin@telesip.vn"
NGINX_CONF="/usr/local/contact-center/nginx/devomnichannelcgv.telesip.vn.conf"
NGINX_SITES_DIR="/etc/nginx/sites-available"
NGINX_ENABLED_DIR="/etc/nginx/sites-enabled"

echo "=========================================="
echo "Cài đặt SSL Certificate cho $DOMAIN"
echo "=========================================="

# Kiểm tra quyền root
if [ "$EUID" -ne 0 ]; then 
    echo "Vui lòng chạy script này với quyền root (sudo)"
    exit 1
fi

# Xác định user nginx
NGINX_USER=$(ps aux | grep '[n]ginx: master' | awk '{print $1}' | head -n1)
if [ -z "$NGINX_USER" ]; then
    if id "www-data" &>/dev/null; then
        NGINX_USER="www-data"
    elif id "nginx" &>/dev/null; then
        NGINX_USER="nginx"
    else
        NGINX_USER="www-data"
    fi
fi

echo "Sử dụng user: $NGINX_USER"

# Tạo thư mục cho Let's Encrypt validation
mkdir -p /var/www/certbot
chown -R $NGINX_USER:$NGINX_USER /var/www/certbot
chmod -R 755 /var/www/certbot

# Tạo cấu hình HTTP tạm thời
echo "Đang tạo cấu hình HTTP tạm thời..."
cat > /tmp/${DOMAIN}_http_temp.conf <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }
}
EOF

# Copy cấu hình vào sites-available
if [ ! -d "$NGINX_SITES_DIR" ]; then
    mkdir -p "$NGINX_SITES_DIR"
fi

cp /tmp/${DOMAIN}_http_temp.conf "$NGINX_SITES_DIR/$DOMAIN"

# Tạo symlink vào sites-enabled
if [ ! -d "$NGINX_ENABLED_DIR" ]; then
    mkdir -p "$NGINX_ENABLED_DIR"
fi

ln -sf "$NGINX_SITES_DIR/$DOMAIN" "$NGINX_ENABLED_DIR/$DOMAIN"

# Test và reload Nginx
echo "Đang test cấu hình Nginx..."
nginx -t
if [ $? -ne 0 ]; then
    echo "❌ Lỗi cấu hình Nginx. Vui lòng kiểm tra lại."
    exit 1
fi

echo "Đang reload Nginx..."
systemctl reload nginx
if [ $? -ne 0 ]; then
    echo "❌ Không thể reload Nginx. Vui lòng kiểm tra lại."
    exit 1
fi

echo "✓ Nginx đã được reload"

# Kiểm tra DNS
echo "Đang kiểm tra DNS..."
IP=$(curl -s ifconfig.me)
DOMAIN_IP=$(dig +short $DOMAIN | tail -n1)

if [ -z "$DOMAIN_IP" ]; then
    echo "⚠️  Cảnh báo: Không thể resolve domain $DOMAIN"
    echo "   Vui lòng đảm bảo domain đã trỏ về IP: $IP"
    read -p "Bạn có muốn tiếp tục? (y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
else
    echo "✓ Domain $DOMAIN trỏ về IP: $DOMAIN_IP"
    if [ "$DOMAIN_IP" != "$IP" ]; then
        echo "⚠️  Cảnh báo: Domain IP ($DOMAIN_IP) khác với server IP ($IP)"
        read -p "Bạn có muốn tiếp tục? (y/n): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
fi

# Cấp SSL certificate
echo ""
echo "Đang cấp SSL certificate từ Let's Encrypt..."
echo "Email sẽ được sử dụng: $EMAIL"
echo ""

certbot certonly \
    --webroot \
    --webroot-path=/var/www/certbot \
    --email $EMAIL \
    --agree-tos \
    --no-eff-email \
    --force-renewal \
    -d $DOMAIN

if [ $? -ne 0 ]; then
    echo "❌ Không thể cấp SSL certificate. Vui lòng kiểm tra lại:"
    echo "   1. Domain đã trỏ về server chưa?"
    echo "   2. Port 80 đã mở chưa?"
    echo "   3. Firewall đã cho phép HTTP chưa?"
    exit 1
fi

echo "✓ SSL certificate đã được cấp thành công!"

# Copy cấu hình Nginx đầy đủ (có SSL)
echo "Đang cập nhật cấu hình Nginx với SSL..."
cp "$NGINX_CONF" "$NGINX_SITES_DIR/$DOMAIN"

# Test và reload Nginx
echo "Đang test cấu hình Nginx với SSL..."
nginx -t
if [ $? -ne 0 ]; then
    echo "❌ Lỗi cấu hình Nginx. Vui lòng kiểm tra lại."
    exit 1
fi

echo "Đang reload Nginx..."
systemctl reload nginx
if [ $? -ne 0 ]; then
    echo "❌ Không thể reload Nginx. Vui lòng kiểm tra lại."
    exit 1
fi

echo ""
echo "=========================================="
echo "✓ Hoàn tất cài đặt SSL!"
echo "=========================================="
echo "Domain: https://$DOMAIN"
echo "Certificate location: /etc/letsencrypt/live/$DOMAIN/"
echo ""
echo "Để tự động gia hạn certificate, chạy lệnh:"
echo "  certbot renew --dry-run"
echo ""
echo "Hoặc thêm vào crontab:"
echo "  0 0 1 * * certbot renew --quiet && systemctl reload nginx"
echo "=========================================="
