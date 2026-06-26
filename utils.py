import os
from pathlib import Path
from PIL import Image, ExifTags
import json
import mimetypes

from config import (
    STORAGE_DIR, THUMBNAIL_DIR, ALLOWED_EXTENSIONS
)

# 初始化 mimetypes
mimetypes.init()


def get_file_extension(filename):
    return Path(filename).suffix.lower().lstrip('.')


def guess_category(filename, mime_type=None):
    """根据文件名或 MIME 类型判断文件分类"""
    ext = get_file_extension(filename)
    if not mime_type:
        mime_type, _ = mimetypes.guess_type(filename)
        mime_type = mime_type or 'application/octet-stream'
    
    if mime_type.startswith('image/') or ext in ALLOWED_EXTENSIONS['image']:
        return 'image'
    if mime_type.startswith('video/') or ext in ALLOWED_EXTENSIONS['video']:
        return 'video'
    if mime_type.startswith('audio/') or ext in ALLOWED_EXTENSIONS['audio']:
        return 'audio'
    if mime_type.startswith('text/') or mime_type == 'application/pdf' or \
       ext in ALLOWED_EXTENSIONS['document']:
        return 'document'
    return 'other'


def safe_path(rel_path):
    """将相对路径转换为绝对路径，并防止目录穿越"""
    base = Path(STORAGE_DIR).resolve()
    target = (base / rel_path).resolve()
    if not str(target).startswith(str(base)):
        raise ValueError('Invalid path: directory traversal detected')
    return target


def rel_path_from_abs(abs_path):
    """从绝对路径获取相对 storage 的相对路径"""
    base = Path(STORAGE_DIR).resolve()
    target = Path(abs_path).resolve()
    try:
        return str(target.relative_to(base))
    except ValueError:
        return None


def ensure_dir_for_file(filepath):
    """确保文件所在目录存在"""
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)


def is_image_file(filename):
    return guess_category(filename) == 'image'


def is_video_file(filename):
    return guess_category(filename) == 'video'


def is_audio_file(filename):
    return guess_category(filename) == 'audio'


def generate_thumbnail(abs_path, rel_path, category, max_size=(320, 320)):
    """生成缩略图，返回相对 THUMBNAIL_DIR 的路径"""
    ext = get_file_extension(abs_path)
    thumb_rel = rel_path + '.jpg'
    thumb_path = Path(THUMBNAIL_DIR) / thumb_rel
    
    if category == 'image':
        try:
            ensure_dir_for_file(thumb_path)
            img = Image.open(abs_path)
            # 处理 EXIF 旋转
            try:
                for orientation in ExifTags.TAGS.keys():
                    if ExifTags.TAGS[orientation] == 'Orientation':
                        break
                exif = img._getexif()
                if exif and orientation in exif:
                    if exif[orientation] == 3:
                        img = img.rotate(180, expand=True)
                    elif exif[orientation] == 6:
                        img = img.rotate(270, expand=True)
                    elif exif[orientation] == 8:
                        img = img.rotate(90, expand=True)
            except Exception:
                pass
            
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            img.save(thumb_path, 'JPEG', quality=85)
            return str(thumb_rel)
        except Exception as e:
            print(f"Image thumbnail failed for {abs_path}: {e}")
            return None
    
    elif category == 'video':
        # 尝试使用 ffmpeg 生成视频缩略图
        try:
            import subprocess
            ensure_dir_for_file(thumb_path)
            cmd = [
                'ffmpeg', '-y', '-i', str(abs_path),
                '-ss', '00:00:01', '-vframes', '1',
                '-vf', f'scale={max_size[0]}:{max_size[1]}:force_original_aspect_ratio=decrease',
                str(thumb_path)
            ]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            if thumb_path.exists():
                return str(thumb_rel)
        except Exception as e:
            print(f"Video thumbnail failed for {abs_path}: {e}")
        return None
    
    return None


def extract_image_info(abs_path):
    """提取图片宽高和 EXIF 信息"""
    info = {'width': None, 'height': None, 'exif': None}
    try:
        with Image.open(abs_path) as img:
            info['width'] = img.width
            info['height'] = img.height
            exif_data = {}
            if hasattr(img, '_getexif') and img._getexif():
                for tag_id, value in img._getexif().items():
                    tag = ExifTags.TAGS.get(tag_id, tag_id)
                    if isinstance(value, bytes):
                        value = value.decode(errors='ignore')
                    exif_data[tag] = str(value)
                info['exif'] = json.dumps(exif_data, ensure_ascii=False)
    except Exception as e:
        print(f"Extract image info failed: {e}")
    return info


def get_mime_type(filename):
    mime, _ = mimetypes.guess_type(filename)
    return mime or 'application/octet-stream'


def format_file_size(size_bytes):
    """格式化文件大小"""
    if size_bytes is None:
        return '-'
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def format_duration(seconds):
    """格式化时长"""
    if seconds is None:
        return '-'
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
