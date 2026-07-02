from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone
import os
import hashlib

db = SQLAlchemy()


class FileItem(db.Model):
    """文件/文件夹元数据表"""
    __tablename__ = 'file_items'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    path = db.Column(db.String(1024), nullable=False, index=True)
    rel_path = db.Column(db.String(1024), nullable=False, unique=True, index=True)
    is_dir = db.Column(db.Boolean, default=False)
    size = db.Column(db.BigInteger, default=0)
    mime_type = db.Column(db.String(128))
    category = db.Column(db.String(32), index=True)  # image/video/audio/document/other/dir
    checksum = db.Column(db.String(64), index=True)
    thumbnail = db.Column(db.String(1024))  # 缩略图相对路径
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    last_scan = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # 媒体元数据
    width = db.Column(db.Integer)
    height = db.Column(db.Integer)
    duration = db.Column(db.Float)  # 视频/音频时长（秒）
    exif = db.Column(db.Text)  # JSON 格式的 EXIF 信息

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'path': self.path,
            'rel_path': self.rel_path,
            'is_dir': self.is_dir,
            'size': self.size,
            'mime_type': self.mime_type,
            'category': self.category,
            'checksum': self.checksum,
            'thumbnail': self.thumbnail,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'width': self.width,
            'height': self.height,
            'duration': self.duration,
            'exif': self.exif,
        }


class User(db.Model):
    """用户表"""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    is_admin = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class LibraryPath(db.Model):
    """外部媒体库路径"""
    __tablename__ = 'library_paths'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    path = db.Column(db.String(1024), nullable=False, unique=True, index=True)
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_scan = db.Column(db.DateTime)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'path': self.path,
            'enabled': self.enabled,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_scan': self.last_scan.isoformat() if self.last_scan else None,
        }


def compute_checksum(filepath):
    """计算文件 SHA256 校验和"""
    h = hashlib.sha256()
    try:
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None
