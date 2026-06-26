#!/bin/bash
# HomeHive 家庭媒体管理平台启动脚本

set -e

cd "$(dirname "$0")"

echo "正在检查依赖..."
pip3 install -q -r requirements.txt

echo "启动 HomeHive..."
echo "默认访问地址: http://127.0.0.1:5000"
echo "默认账号: admin / admin123"
echo "按 Ctrl+C 停止服务"

python3 app.py
