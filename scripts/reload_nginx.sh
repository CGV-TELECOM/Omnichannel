#!/bin/bash

# Script để reload Nginx sau khi cập nhật cấu hình

echo "Đang kiểm tra cấu hình Nginx..."

# Test cấu hình
nginx -t

if [ $? -eq 0 ]; then
    echo "✓ Cấu hình hợp lệ"
    echo "Đang reload Nginx..."
    systemctl reload nginx
    
    if [ $? -eq 0 ]; then
        echo "✓ Nginx đã được reload thành công!"
        echo "Trạng thái:"
        systemctl status nginx --no-pager -l
    else
        echo "❌ Lỗi khi reload Nginx"
        exit 1
    fi
else
    echo "❌ Cấu hình Nginx không hợp lệ. Vui lòng kiểm tra lại."
    exit 1
fi
