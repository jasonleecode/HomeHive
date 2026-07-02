#!/bin/bash
# HomeHive 家庭媒体管理平台启动脚本

set -e

cd "$(dirname "$0")"

if [ "$1" = "--install" ]; then
    echo "正在安装依赖..."
    python3 -m pip install -q -r requirements.txt
fi

export HIVE_HOST="${HIVE_HOST:-0.0.0.0}"
export HIVE_PORT="${HIVE_PORT:-5000}"

echo "启动 HomeHive..."
echo "本机访问地址: http://127.0.0.1:${HIVE_PORT}"
echo "局域网访问地址: http://<本机局域网IP>:${HIVE_PORT}"
echo "监听地址: ${HIVE_HOST}:${HIVE_PORT}"
echo "按 Ctrl+C 停止服务"

python3 app.py
