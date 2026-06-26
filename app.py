import os
import json
import shutil
import secrets
from functools import wraps
from pathlib import Path
from datetime import datetime, timezone

from flask import Flask, request, jsonify, send_file, render_template, session, Response
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import psutil

from config import (
    DATABASE_PATH, STORAGE_DIR, THUMBNAIL_DIR, MAX_CONTENT_LENGTH,
    SECRET_KEY, DEFAULT_USERNAME, DEFAULT_PASSWORD, ensure_dirs
)
from models import db, FileItem, User, compute_checksum
from utils import (
    safe_path, rel_path_from_abs, get_mime_type, guess_category,
    generate_thumbnail, extract_image_info, format_file_size, format_duration,
    is_image_file, is_video_file, is_audio_file, ensure_dir_for_file
)

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DATABASE_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
app.config['SECRET_KEY'] = SECRET_KEY

db.init_app(app)


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return jsonify({'success': False, 'message': '请先登录'}), 401
        return f(*args, **kwargs)
    return decorated_function


def init_default_user():
    """初始化默认管理员账号"""
    if User.query.first() is None:
        user = User(
            username=DEFAULT_USERNAME,
            password_hash=generate_password_hash(DEFAULT_PASSWORD),
            is_admin=True
        )
        db.session.add(user)
        db.session.commit()
        print(f"Default user created: {DEFAULT_USERNAME} / {DEFAULT_PASSWORD}")


@app.before_request
def check_session():
    pass


# ============================================================
# 页面路由
# ============================================================

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/login')
def login_page():
    return render_template('login.html')


# ============================================================
# 认证 API
# ============================================================

@app.route('/api/auth/login', methods=['POST'])
def api_login():
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    user = User.query.filter_by(username=username).first()
    if user and check_password_hash(user.password_hash, password):
        session['logged_in'] = True
        session['username'] = username
        session['csrf_token'] = secrets.token_hex(16)
        return jsonify({
            'success': True,
            'username': username,
            'csrf_token': session['csrf_token']
        })
    return jsonify({'success': False, 'message': '用户名或密码错误'}), 401


@app.route('/api/auth/logout', methods=['POST'])
@login_required
def api_logout():
    session.clear()
    return jsonify({'success': True})


@app.route('/api/auth/status')
def api_auth_status():
    return jsonify({
        'logged_in': session.get('logged_in', False),
        'username': session.get('username')
    })


# ============================================================
# 文件管理 API
# ============================================================

@app.route('/api/files', methods=['GET'])
@login_required
def api_list_files():
    path = request.args.get('path', '')
    try:
        target_dir = safe_path(path)
        if not target_dir.is_dir():
            return jsonify({'success': False, 'message': '目录不存在'}), 404
        
        items = []
        for entry in sorted(target_dir.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            rel = rel_path_from_abs(entry)
            db_item = FileItem.query.filter_by(rel_path=rel).first()
            if db_item:
                info = db_item.to_dict()
            else:
                info = {
                    'name': entry.name,
                    'rel_path': rel,
                    'is_dir': entry.is_dir(),
                    'size': entry.stat().st_size if entry.is_file() else 0,
                    'category': 'dir' if entry.is_dir() else guess_category(entry.name),
                    'thumbnail': None,
                }
            info['size_formatted'] = format_file_size(info.get('size'))
            items.append(info)
        
        return jsonify({
            'success': True,
            'path': path,
            'items': items
        })
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': f'列出文件失败: {str(e)}'}), 500


@app.route('/api/files/mkdir', methods=['POST'])
@login_required
def api_mkdir():
    data = request.get_json() or {}
    path = data.get('path', '')
    name = data.get('name', '').strip()
    
    if not name:
        return jsonify({'success': False, 'message': '目录名称不能为空'}), 400
    
    safe_name = secure_filename(name)
    if not safe_name:
        safe_name = name.replace('/', '_').replace('\\', '_')
    
    try:
        parent = safe_path(path)
        new_dir = parent / safe_name
        new_dir.mkdir(parents=True, exist_ok=True)
        sync_single_file(new_dir)
        return jsonify({'success': True, 'path': rel_path_from_abs(new_dir)})
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': f'创建目录失败: {str(e)}'}), 500


@app.route('/api/files/upload', methods=['POST'])
@login_required
def api_upload():
    path = request.form.get('path', '')
    try:
        target_dir = safe_path(path)
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    
    if not target_dir.is_dir():
        return jsonify({'success': False, 'message': '目标目录不存在'}), 404
    
    files = request.files.getlist('files')
    results = []
    
    for file in files:
        if not file.filename:
            continue
        safe_name = secure_filename(file.filename)
        if not safe_name:
            safe_name = file.filename.replace('/', '_').replace('\\', '_')
        
        dest = target_dir / safe_name
        counter = 1
        stem = dest.stem
        suffix = dest.suffix
        while dest.exists():
            dest = target_dir / f"{stem}_{counter}{suffix}"
            counter += 1
        
        try:
            ensure_dir_for_file(dest)
            file.save(str(dest))
            info = sync_single_file(dest)
            results.append({'success': True, 'name': safe_name, 'path': info.rel_path})
        except Exception as e:
            results.append({'success': False, 'name': safe_name, 'message': str(e)})
    
    return jsonify({'success': True, 'results': results})


@app.route('/api/files/rename', methods=['POST'])
@login_required
def api_rename():
    data = request.get_json() or {}
    rel_path = data.get('path', '')
    new_name = data.get('new_name', '').strip()
    
    if not new_name:
        return jsonify({'success': False, 'message': '新名称不能为空'}), 400
    
    try:
        old_path = safe_path(rel_path)
        new_path = old_path.parent / secure_filename(new_name)
        if not new_path.name:
            new_path = old_path.parent / new_name.replace('/', '_').replace('\\', '_')
        
        if new_path.exists():
            return jsonify({'success': False, 'message': '目标已存在'}), 400
        
        shutil.move(str(old_path), str(new_path))
        update_path_index(old_path, new_path)
        return jsonify({'success': True, 'new_path': rel_path_from_abs(new_path)})
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': f'重命名失败: {str(e)}'}), 500


@app.route('/api/files/move', methods=['POST'])
@login_required
def api_move():
    data = request.get_json() or {}
    source = data.get('source', '')
    target = data.get('target', '')
    
    try:
        src_path = safe_path(source)
        dst_dir = safe_path(target)
        if not dst_dir.is_dir():
            return jsonify({'success': False, 'message': '目标目录不存在'}), 404
        
        dst_path = dst_dir / src_path.name
        if dst_path.exists():
            return jsonify({'success': False, 'message': '目标位置已存在同名文件'}), 400
        
        shutil.move(str(src_path), str(dst_path))
        update_path_index(src_path, dst_path)
        return jsonify({'success': True, 'new_path': rel_path_from_abs(dst_path)})
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': f'移动失败: {str(e)}'}), 500


@app.route('/api/files/delete', methods=['POST'])
@login_required
def api_delete():
    data = request.get_json() or {}
    rel_path = data.get('path', '')
    
    try:
        target = safe_path(rel_path)
        if not target.exists():
            return jsonify({'success': False, 'message': '文件不存在'}), 404
        
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        
        remove_from_index(target)
        return jsonify({'success': True})
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': f'删除失败: {str(e)}'}), 500


@app.route('/api/files/download')
@login_required
def api_download():
    rel_path = request.args.get('path', '')
    try:
        target = safe_path(rel_path)
        if not target.is_file():
            return jsonify({'success': False, 'message': '文件不存在'}), 404
        return send_file(str(target), as_attachment=True, download_name=target.name)
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': f'下载失败: {str(e)}'}), 500


# ============================================================
# 媒体与搜索 API
# ============================================================

@app.route('/api/media', methods=['GET'])
@login_required
def api_media():
    category = request.args.get('category', 'image')
    page = max(1, int(request.args.get('page', 1)))
    per_page = min(100, max(1, int(request.args.get('per_page', 40))))
    
    query = FileItem.query.filter_by(category=category, is_dir=False)
    total = query.count()
    items = query.order_by(FileItem.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    
    return jsonify({
        'success': True,
        'category': category,
        'total': total,
        'page': page,
        'per_page': per_page,
        'items': [item.to_dict() for item in items]
    })


@app.route('/api/search', methods=['GET'])
@login_required
def api_search():
    q = request.args.get('q', '').strip()
    category = request.args.get('category', '')
    page = max(1, int(request.args.get('page', 1)))
    per_page = min(100, max(1, int(request.args.get('per_page', 40))))
    
    if not q:
        return jsonify({'success': False, 'message': '搜索关键词不能为空'}), 400
    
    query = FileItem.query.filter(FileItem.name.contains(q))
    if category:
        query = query.filter_by(category=category)
    
    total = query.count()
    items = query.order_by(FileItem.updated_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    
    return jsonify({
        'success': True,
        'q': q,
        'total': total,
        'page': page,
        'per_page': per_page,
        'items': [item.to_dict() for item in items]
    })


@app.route('/api/stats', methods=['GET'])
@login_required
def api_stats():
    total_files = FileItem.query.filter_by(is_dir=False).count()
    total_dirs = FileItem.query.filter_by(is_dir=True).count()
    total_size = db.session.query(db.func.sum(FileItem.size)).filter_by(is_dir=False).scalar() or 0
    
    categories = {}
    for cat in ['image', 'video', 'audio', 'document', 'other']:
        count = FileItem.query.filter_by(category=cat, is_dir=False).count()
        size = db.session.query(db.func.sum(FileItem.size)).filter_by(category=cat, is_dir=False).scalar() or 0
        categories[cat] = {'count': count, 'size': size, 'size_formatted': format_file_size(size)}
    
    return jsonify({
        'success': True,
        'total_files': total_files,
        'total_dirs': total_dirs,
        'total_size': total_size,
        'total_size_formatted': format_file_size(total_size),
        'categories': categories
    })


@app.route('/api/scan', methods=['POST'])
@login_required
def api_scan():
    """触发全盘扫描，同步文件系统到数据库"""
    try:
        scanned = scan_storage()
        return jsonify({'success': True, 'scanned': scanned})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ============================================================
# 缩略图 / 预览
# ============================================================

@app.route('/api/thumbnail/<path:rel_path>')
@login_required
def api_thumbnail(rel_path):
    thumb_path = Path(THUMBNAIL_DIR) / (rel_path + '.jpg')
    if thumb_path.exists():
        return send_file(str(thumb_path), mimetype='image/jpeg')
    
    # 没有缩略图则尝试原图
    try:
        target = safe_path(rel_path)
        if target.is_file() and is_image_file(target.name):
            return send_file(str(target))
    except Exception:
        pass
    
    return jsonify({'success': False, 'message': '缩略图不存在'}), 404


@app.route('/api/preview/<path:rel_path>')
@login_required
def api_preview(rel_path):
    try:
        target = safe_path(rel_path)
        if not target.is_file():
            return jsonify({'success': False, 'message': '文件不存在'}), 404
        
        mime = get_mime_type(target.name)
        return send_file(str(target), mimetype=mime)
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': f'预览失败: {str(e)}'}), 500


# ============================================================
# 索引与扫描工具
# ============================================================

def sync_single_file(abs_path):
    """同步单个文件/目录到数据库"""
    rel = rel_path_from_abs(abs_path)
    if rel is None:
        return None
    
    is_dir = abs_path.is_dir()
    stat = abs_path.stat() if abs_path.exists() else None
    
    item = FileItem.query.filter_by(rel_path=rel).first()
    if not item:
        item = FileItem(rel_path=rel)
        db.session.add(item)
    
    item.name = abs_path.name
    item.path = str(abs_path)
    item.is_dir = is_dir
    item.size = stat.st_size if stat and not is_dir else 0
    item.mime_type = 'inode/directory' if is_dir else get_mime_type(abs_path.name)
    item.category = 'dir' if is_dir else guess_category(abs_path.name, item.mime_type)
    item.updated_at = datetime.now(timezone.utc)
    item.last_scan = item.updated_at
    
    if not is_dir and abs_path.exists():
        item.checksum = compute_checksum(str(abs_path))
        
        # 图片信息
        if item.category == 'image':
            info = extract_image_info(str(abs_path))
            item.width = info.get('width')
            item.height = info.get('height')
            item.exif = info.get('exif')
            thumb_rel = generate_thumbnail(str(abs_path), rel, 'image')
            item.thumbnail = thumb_rel
        
        # 视频缩略图
        elif item.category == 'video':
            thumb_rel = generate_thumbnail(str(abs_path), rel, 'video')
            item.thumbnail = thumb_rel
    
    db.session.commit()
    return item


def remove_from_index(abs_path):
    """从数据库中删除索引"""
    rel = rel_path_from_abs(abs_path)
    if rel:
        FileItem.query.filter(
            db.or_(FileItem.rel_path == rel, FileItem.rel_path.like(rel + '/%'))
        ).delete(synchronize_session=False)
        db.session.commit()


def update_path_index(old_abs, new_abs):
    """移动/重命名后更新数据库索引"""
    old_rel = rel_path_from_abs(old_abs)
    new_rel = rel_path_from_abs(new_abs)
    if not old_rel or not new_rel:
        return
    
    # 更新自身和所有子项
    old_prefix = old_rel if old_rel.endswith('/') else old_rel + '/'
    new_prefix = new_rel if new_rel.endswith('/') else new_rel + '/'
    
    items = FileItem.query.filter(
        db.or_(FileItem.rel_path == old_rel, FileItem.rel_path.like(old_prefix + '%'))
    ).all()
    
    for item in items:
        if item.rel_path == old_rel:
            item.rel_path = new_rel
            item.name = new_abs.name
        else:
            item.rel_path = new_prefix + item.rel_path[len(old_prefix):]
        item.path = str(STORAGE_DIR / item.rel_path)
    
    db.session.commit()


def scan_storage():
    """扫描整个存储目录并同步到数据库"""
    base = Path(STORAGE_DIR).resolve()
    if not base.exists():
        return 0
    
    scanned = 0
    for entry in base.rglob('*'):
        if entry.is_file() or entry.is_dir():
            try:
                sync_single_file(entry)
                scanned += 1
            except Exception as e:
                print(f"Scan error for {entry}: {e}")
    
    # 清理不存在的条目
    for item in FileItem.query.all():
        abs_path = safe_path(item.rel_path)
        if not abs_path.exists():
            db.session.delete(item)
    db.session.commit()
    
    return scanned


# ============================================================
# 磁盘管理 API
# ============================================================

@app.route('/api/disks', methods=['GET'])
@login_required
def api_disks():
    """获取磁盘分区使用情况"""
    show_all = request.args.get('all', '0') == '1'
    
    disks = []
    for part in psutil.disk_partitions(all=show_all):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append({
                'device': part.device,
                'mountpoint': part.mountpoint,
                'fstype': part.fstype,
                'opts': part.opts,
                'total': usage.total,
                'used': usage.used,
                'free': usage.free,
                'percent': usage.percent,
                'total_formatted': format_file_size(usage.total),
                'used_formatted': format_file_size(usage.used),
                'free_formatted': format_file_size(usage.free),
            })
        except PermissionError:
            continue
        except Exception as e:
            print(f"Disk usage error for {part.mountpoint}: {e}")
            continue
    
    # 总体统计
    try:
        mem = psutil.virtual_memory()
        memory_info = {
            'total': mem.total,
            'available': mem.available,
            'percent': mem.percent,
            'total_formatted': format_file_size(mem.total),
            'available_formatted': format_file_size(mem.available),
        }
    except Exception:
        memory_info = None
    
    return jsonify({
        'success': True,
        'disks': disks,
        'memory': memory_info
    })


# ============================================================
# 启动
# ============================================================

with app.app_context():
    ensure_dirs()
    db.create_all()
    init_default_user()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
