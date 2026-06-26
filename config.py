import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
STORAGE_DIR = os.path.join(DATA_DIR, 'storage')
THUMBNAIL_DIR = os.path.join(DATA_DIR, 'thumbnails')
DATABASE_PATH = os.path.join(DATA_DIR, 'homehive.db')
LEGACY_DATABASE_PATH = os.path.join(DATA_DIR, 'nas_admin.db')

# 上传配置
MAX_CONTENT_LENGTH = 10 * 1024 * 1024 * 1024  # 10GB
ALLOWED_EXTENSIONS = {
    'image': ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'heic', 'raw', 'cr2', 'nef'],
    'video': ['mp4', 'mkv', 'avi', 'mov', 'wmv', 'flv', 'webm', 'm4v', 'mpg', 'mpeg'],
    'audio': ['mp3', 'wav', 'flac', 'aac', 'ogg', 'm4a', 'wma'],
    'document': ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'md'],
}

# 安全密钥（生产环境请修改）。兼容旧的 NAS_ 前缀环境变量。
SECRET_KEY = os.environ.get('HIVE_SECRET_KEY') or os.environ.get(
    'NAS_SECRET_KEY', 'dev-secret-key-change-in-production')

# 默认管理员账号
DEFAULT_USERNAME = os.environ.get('HIVE_ADMIN_USER') or os.environ.get(
    'NAS_ADMIN_USER', 'admin')
DEFAULT_PASSWORD = os.environ.get('HIVE_ADMIN_PASS') or os.environ.get(
    'NAS_ADMIN_PASS', 'admin123')


def ensure_dirs():
    """确保必要的目录存在"""
    for d in [DATA_DIR, STORAGE_DIR, THUMBNAIL_DIR]:
        os.makedirs(d, exist_ok=True)
    _migrate_legacy_db()


def _migrate_legacy_db():
    """将旧的 nas_admin.db 迁移为 homehive.db（仅在新库不存在时执行）"""
    if not os.path.exists(DATABASE_PATH) and os.path.exists(LEGACY_DATABASE_PATH):
        os.rename(LEGACY_DATABASE_PATH, DATABASE_PATH)
        print(f'[HomeHive] 已将旧数据库迁移为 {os.path.basename(DATABASE_PATH)}')
