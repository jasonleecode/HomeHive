# 🐝 HomeHive

一个轻量级的家庭 NAS 网页管理控制台，支持照片、视频、音频和文件的统一管理。把家里的媒体都聚到一个「蜂巢」里。

## 功能特性

- 📁 **文件管理**：浏览、上传、下载、重命名、删除、新建文件夹，支持列表/网格视图
- 🖼️ **照片管理**：照片墙展示、图片灯箱预览、EXIF 信息提取、自动缩略图
- 🎬 **视频管理**：视频库展示、在线播放、自动缩略图（需安装 ffmpeg）
- 🎵 **音频管理**：音乐库、底部播放器、播放列表、上一首/下一首
- 🔍 **全局搜索**：按文件名快速搜索
- 📊 **存储统计**：按分类统计文件数量和占用空间
- 🔄 **扫描同步**：一键扫描存储目录，同步文件系统变更到数据库

## 技术栈

- 后端：Python 3 + Flask + SQLAlchemy + SQLite
- 前端：原生 HTML5 + CSS3 + JavaScript（单页应用风格）
- 图片处理：Pillow
- 系统信息：psutil
- 视频缩略图：ffmpeg（可选）

## 快速开始

### 1. 安装依赖

```bash
pip3 install -r requirements.txt
```

### 2. 启动服务

```bash
./run.sh
# 或
python3 app.py
```

服务默认运行在 `http://0.0.0.0:5000`。

### 3. 登录

打开浏览器访问 `http://127.0.0.1:5000`，使用默认账号登录：

- 用户名：`admin`
- 密码：`admin123`

> ⚠️ 请在首次登录后尽快修改默认密码。

## 配置说明

可通过环境变量调整配置：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `HIVE_SECRET_KEY` | Flask 安全密钥 | `dev-secret-key-change-in-production` |
| `HIVE_ADMIN_USER` | 默认管理员用户名 | `admin` |
| `HIVE_ADMIN_PASS` | 默认管理员密码 | `admin123` |

> 旧的 `NAS_SECRET_KEY` / `NAS_ADMIN_USER` / `NAS_ADMIN_PASS` 仍向后兼容。

## 目录结构

```
nas_admin/
├── app.py                 # Flask 后端主程序
├── config.py              # 配置文件
├── models.py              # 数据库模型
├── utils.py               # 工具函数
├── requirements.txt       # Python 依赖
├── run.sh                 # 启动脚本
├── data/
│   ├── storage/           # 文件存储目录
│   ├── thumbnails/        # 缩略图缓存
│   └── homehive.db        # SQLite 数据库
├── static/
│   ├── css/style.css      # 前端样式
│   ├── js/app.js          # 前端交互逻辑
│   └── img/               # 静态图片
└── templates/
    ├── index.html         # 主应用页面
    └── login.html         # 登录页面
```

## 注意事项

- 当前使用 Flask 内置开发服务器，建议在生产环境使用 Gunicorn 等 WSGI 服务器
- 大文件上传受 `MAX_CONTENT_LENGTH` 配置限制（默认 10GB）
- 视频缩略图需要系统安装 ffmpeg
- 请妥善保管管理员账号，建议通过反向代理添加 HTTPS

## 许可证

MIT
