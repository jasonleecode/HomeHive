import os
import shutil
import secrets
import subprocess
import threading
import time
import uuid
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
from models import db, FileItem, User, LibraryPath, compute_checksum
from utils import (
    safe_path, rel_path_from_abs, get_mime_type, guess_category,
    generate_thumbnail, extract_image_info, format_file_size, format_duration,
    is_image_file, is_video_file, is_audio_file, ensure_dir_for_file
)

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DATABASE_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'connect_args': {'timeout': 30}}
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
app.config['SECRET_KEY'] = SECRET_KEY

db.init_app(app)

SCAN_JOBS = {}
SCAN_LOCK = threading.Lock()
DB_WRITE_LOCK = threading.Lock()


class ScanCancelled(Exception):
    pass


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
        print(f"Default user created: {DEFAULT_USERNAME}")


def cleanup_misclassified_items():
    updated = FileItem.query.filter(
        FileItem.category == 'video',
        FileItem.name.ilike('%.ts')
    ).update({
        FileItem.category: 'document',
        FileItem.mime_type: 'text/plain',
        FileItem.thumbnail: None,
    }, synchronize_session=False)
    if updated:
        db.session.commit()
        print(f"Fixed {updated} misclassified .ts items")


def is_storage_root(path):
    return Path(path).resolve() == Path(STORAGE_DIR).resolve()


def require_non_root_path(path):
    if is_storage_root(path):
        raise ValueError('不允许对根存储目录执行此操作')


def validate_child_name(name):
    safe_name = secure_filename(name)
    if not safe_name:
        safe_name = name.replace('/', '_').replace('\\', '_').strip()
    if not safe_name or safe_name in ('.', '..'):
        raise ValueError('名称无效')
    return safe_name


def library_prefix(library_id):
    return f'library/{library_id}/'


def rel_path_from_library(abs_path, library):
    root = Path(library.path).resolve()
    target = Path(abs_path).resolve()
    rel = target.relative_to(root).as_posix()
    return library_prefix(library.id) + rel


def resolve_media_path(rel_path):
    if rel_path.startswith('library/'):
        item = FileItem.query.filter_by(rel_path=rel_path).first()
        if not item:
            raise FileNotFoundError('文件不存在')

        parts = rel_path.split('/', 2)
        if len(parts) != 3 or not parts[1].isdigit():
            raise ValueError('Invalid library path')

        library = db.session.get(LibraryPath, int(parts[1]))
        if not library or not library.enabled:
            raise FileNotFoundError('媒体库不存在')

        root = Path(library.path).resolve()
        target = Path(item.path).resolve()
        target.relative_to(root)
        return target

    return safe_path(rel_path)


def update_scan_job(job_id, **updates):
    if not job_id:
        return
    with SCAN_LOCK:
        job = SCAN_JOBS.get(job_id)
        if not job:
            return
        job.update(updates)
        job['updated_at'] = datetime.now(timezone.utc).isoformat()


def get_scan_job(job_id):
    with SCAN_LOCK:
        job = SCAN_JOBS.get(job_id)
        return dict(job) if job else None


def get_active_scan_job_for_library(library_id):
    with SCAN_LOCK:
        for job in SCAN_JOBS.values():
            if job.get('library_id') == library_id and job.get('status') in ('queued', 'running'):
                return dict(job)
    return None


def is_scan_cancel_requested(job_id):
    if not job_id:
        return False
    with SCAN_LOCK:
        job = SCAN_JOBS.get(job_id)
        return bool(job and job.get('status') in ('cancelling', 'cancelled'))


def cancel_scan_job(job_id, message='正在中止扫描'):
    with SCAN_LOCK:
        job = SCAN_JOBS.get(job_id)
        if not job:
            return None
        if job.get('status') in ('done', 'failed', 'cancelled'):
            return dict(job)
        job['status'] = 'cancelling'
        job['message'] = message
        job['updated_at'] = datetime.now(timezone.utc).isoformat()
        return dict(job)


def cancel_scan_jobs_for_library(library_id, message='媒体库已移除，正在中止扫描'):
    jobs = []
    with SCAN_LOCK:
        for job in SCAN_JOBS.values():
            if job.get('library_id') == library_id and job.get('status') in ('queued', 'running', 'cancelling'):
                job['status'] = 'cancelling'
                job['message'] = message
                job['updated_at'] = datetime.now(timezone.utc).isoformat()
                jobs.append(dict(job))
    return jobs


def start_library_scan(library_id):
    active = get_active_scan_job_for_library(library_id)
    if active:
        return active['id']

    job_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    with SCAN_LOCK:
        SCAN_JOBS[job_id] = {
            'id': job_id,
            'library_id': library_id,
            'status': 'queued',
            'visited': 0,
            'indexed': 0,
            'skipped': 0,
            'message': '等待扫描',
            'started_at': now,
            'updated_at': now,
            'finished_at': None,
        }

    thread = threading.Thread(target=run_library_scan_job, args=(job_id, library_id), daemon=True)
    thread.start()
    return job_id


def run_library_scan_job(job_id, library_id):
    with app.app_context():
        try:
            library = db.session.get(LibraryPath, library_id)
            if not library:
                raise FileNotFoundError('媒体库不存在')
            update_scan_job(job_id, status='running', message='正在扫描')
            scanned = scan_library(library, job_id=job_id)
            update_scan_job(
                job_id,
                status='done',
                indexed=scanned,
                message='扫描完成',
                finished_at=datetime.now(timezone.utc).isoformat()
            )
        except ScanCancelled:
            update_scan_job(
                job_id,
                status='cancelled',
                message='扫描已中止',
                current_path=None,
                finished_at=datetime.now(timezone.utc).isoformat()
            )
        except Exception as e:
            update_scan_job(
                job_id,
                status='failed',
                message=str(e),
                finished_at=datetime.now(timezone.utc).isoformat()
            )
        finally:
            db.session.remove()


@app.before_request
def check_session():
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return None
    if request.endpoint == 'api_login':
        return None
    if not session.get('logged_in'):
        return None

    expected = session.get('csrf_token')
    supplied = request.headers.get('X-CSRF-Token')
    if not expected or not secrets.compare_digest(expected, supplied or ''):
        return jsonify({'success': False, 'message': 'CSRF token 无效'}), 403
    return None


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
        'username': session.get('username'),
        'csrf_token': session.get('csrf_token') if session.get('logged_in') else None
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
    
    try:
        safe_name = validate_child_name(name)
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
        try:
            safe_name = validate_child_name(file.filename)
        except ValueError as e:
            results.append({'success': False, 'name': file.filename, 'message': str(e)})
            continue
        
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
        require_non_root_path(old_path)
        safe_name = validate_child_name(new_name)
        new_path = old_path.parent / safe_name
        
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
        require_non_root_path(src_path)
        if not dst_dir.is_dir():
            return jsonify({'success': False, 'message': '目标目录不存在'}), 404
        if src_path.is_dir() and dst_dir.resolve().is_relative_to(src_path.resolve()):
            return jsonify({'success': False, 'message': '不能移动目录到自身或子目录'}), 400
        
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
        require_non_root_path(target)
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
    thumb_base = Path(THUMBNAIL_DIR).resolve()
    thumb_path = (thumb_base / (rel_path + '.jpg')).resolve()
    try:
        thumb_path.relative_to(thumb_base)
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid path: directory traversal detected'}), 400

    if thumb_path.exists():
        return send_file(str(thumb_path), mimetype='image/jpeg')
    
    # 没有缩略图则尝试原图
    try:
        target = resolve_media_path(rel_path)
        if target.is_file() and is_image_file(target.name):
            return send_file(str(target))
    except Exception:
        pass
    
    return jsonify({'success': False, 'message': '缩略图不存在'}), 404


@app.route('/api/preview/<path:rel_path>')
@login_required
def api_preview(rel_path):
    try:
        target = resolve_media_path(rel_path)
        if not target.is_file():
            return jsonify({'success': False, 'message': '文件不存在'}), 404
        
        mime = get_mime_type(target.name)
        return send_file(str(target), mimetype=mime)
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except FileNotFoundError as e:
        return jsonify({'success': False, 'message': str(e)}), 404
    except Exception as e:
        return jsonify({'success': False, 'message': f'预览失败: {str(e)}'}), 500


@app.route('/api/transcode/<path:rel_path>')
@login_required
def api_transcode(rel_path):
    """将浏览器可能不支持的视频临时转为 H.264/AAC fragmented MP4 流"""
    try:
        target = resolve_media_path(rel_path)
        if not target.is_file():
            return jsonify({'success': False, 'message': '文件不存在'}), 404
        if guess_category(target.name) != 'video':
            return jsonify({'success': False, 'message': '不是视频文件'}), 400
        if not shutil.which('ffmpeg'):
            return jsonify({'success': False, 'message': 'ffmpeg 未安装，无法转码播放'}), 500

        cmd = [
            'ffmpeg',
            '-hide_banner',
            '-loglevel', 'error',
            '-i', str(target),
            '-map', '0:v:0',
            '-map', '0:a?',
            '-c:v', 'libx264',
            '-preset', 'veryfast',
            '-tune', 'zerolatency',
            '-pix_fmt', 'yuv420p',
            '-c:a', 'aac',
            '-b:a', '160k',
            '-movflags', 'frag_keyframe+empty_moov+faststart',
            '-f', 'mp4',
            'pipe:1',
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        def generate():
            try:
                while True:
                    chunk = proc.stdout.read(1024 * 256)
                    if not chunk:
                        break
                    yield chunk
            finally:
                if proc.poll() is None:
                    proc.terminate()

        return Response(generate(), mimetype='video/mp4', headers={
            'Cache-Control': 'no-store',
            'X-Accel-Buffering': 'no',
        })
    except FileNotFoundError as e:
        return jsonify({'success': False, 'message': str(e)}), 404
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': f'转码失败: {str(e)}'}), 500


# ============================================================
# 索引与扫描工具
# ============================================================

def sync_single_file(abs_path, rel_path=None, cancel_check=None):
    """同步单个文件/目录到数据库"""
    rel = rel_path or rel_path_from_abs(abs_path)
    if rel is None:
        return None
    if cancel_check and cancel_check():
        raise ScanCancelled()
    
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
    
    if cancel_check and cancel_check():
        db.session.rollback()
        raise ScanCancelled()
    with DB_WRITE_LOCK:
        if cancel_check and cancel_check():
            db.session.rollback()
            raise ScanCancelled()
        db.session.commit()
    return item


def sync_library_file(abs_path, library, job_id=None):
    rel = rel_path_from_library(abs_path, library)
    return sync_single_file(abs_path, rel, cancel_check=lambda: is_scan_cancel_requested(job_id))


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
            item.name = Path(item.rel_path).name
        item.path = str(Path(STORAGE_DIR) / item.rel_path)
    
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
    for item in FileItem.query.filter(~FileItem.rel_path.like('library/%')).all():
        abs_path = safe_path(item.rel_path)
        if not abs_path.exists():
            db.session.delete(item)
    db.session.commit()
    
    return scanned


def scan_library(library, job_id=None):
    """扫描外部媒体库路径，并同步到数据库索引"""
    root = Path(library.path).resolve()
    if not root.is_dir():
        raise ValueError('媒体库路径不存在或不是目录')

    visited = 0
    indexed = 0
    skipped = 0
    last_update = 0
    for entry in root.rglob('*'):
        if is_scan_cancel_requested(job_id):
            db.session.rollback()
            raise ScanCancelled()
        if not entry.is_file():
            continue
        visited += 1
        category = guess_category(entry.name)
        if category == 'other':
            skipped += 1
            now = time.monotonic()
            if job_id and now - last_update >= 0.5:
                update_scan_job(
                    job_id,
                    visited=visited,
                    indexed=indexed,
                    skipped=skipped,
                    current_path=str(entry),
                    message='正在扫描'
                )
                last_update = now
            continue
        try:
            sync_library_file(entry, library, job_id=job_id)
            indexed += 1
        except ScanCancelled:
            raise
        except Exception as e:
            print(f"Library scan error for {entry}: {e}")
            skipped += 1

        now = time.monotonic()
        if job_id and now - last_update >= 0.5:
            update_scan_job(
                job_id,
                visited=visited,
                indexed=indexed,
                skipped=skipped,
                current_path=str(entry),
                message='正在扫描'
            )
            last_update = now

    if is_scan_cancel_requested(job_id):
        db.session.rollback()
        raise ScanCancelled()

    prefix = library_prefix(library.id)
    for item in FileItem.query.filter(FileItem.rel_path.like(prefix + '%')).all():
        if is_scan_cancel_requested(job_id):
            db.session.rollback()
            raise ScanCancelled()
        try:
            target = Path(item.path).resolve()
            target.relative_to(root)
            if not target.exists() or guess_category(target.name) == 'other':
                db.session.delete(item)
        except Exception:
            db.session.delete(item)

    library.last_scan = datetime.now(timezone.utc)
    if is_scan_cancel_requested(job_id):
        db.session.rollback()
        raise ScanCancelled()
    with DB_WRITE_LOCK:
        if is_scan_cancel_requested(job_id):
            db.session.rollback()
            raise ScanCancelled()
        db.session.commit()
    update_scan_job(
        job_id,
        visited=visited,
        indexed=indexed,
        skipped=skipped,
        current_path=None,
        message='扫描完成'
    )
    return indexed


# ============================================================
# 磁盘管理 API
# ============================================================

@app.route('/api/libraries', methods=['GET'])
@login_required
def api_list_libraries():
    libraries = LibraryPath.query.order_by(LibraryPath.created_at.desc()).all()
    items = []
    for library in libraries:
        info = library.to_dict()
        prefix = library_prefix(library.id)
        info['file_count'] = FileItem.query.filter(FileItem.rel_path.like(prefix + '%')).count()
        info['exists'] = Path(library.path).is_dir()
        info['scan_job'] = get_active_scan_job_for_library(library.id)
        items.append(info)
    return jsonify({'success': True, 'items': items})


@app.route('/api/libraries', methods=['POST'])
@login_required
def api_add_library():
    data = request.get_json() or {}
    raw_path = data.get('path', '').strip()
    name = data.get('name', '').strip()

    if not raw_path:
        return jsonify({'success': False, 'message': '路径不能为空'}), 400

    requested = Path(raw_path).expanduser()
    if not requested.is_absolute():
        return jsonify({'success': False, 'message': '请输入绝对路径'}), 400

    target = requested.resolve()
    if not target.is_dir():
        return jsonify({'success': False, 'message': '路径不存在或不是目录'}), 400

    normalized = str(target)
    library = LibraryPath.query.filter_by(path=normalized).first()
    if not library:
        library = LibraryPath(
            name=name or target.name or normalized,
            path=normalized,
            enabled=True
        )
        db.session.add(library)
        with DB_WRITE_LOCK:
            db.session.commit()
    else:
        library.name = name or library.name
        library.enabled = True
        with DB_WRITE_LOCK:
            db.session.commit()

    item = library.to_dict()
    item['file_count'] = FileItem.query.filter(FileItem.rel_path.like(library_prefix(library.id) + '%')).count()
    item['exists'] = True
    job_id = start_library_scan(library.id)
    return jsonify({'success': True, 'item': item, 'job_id': job_id})


@app.route('/api/libraries/<int:library_id>/scan', methods=['POST'])
@login_required
def api_scan_library(library_id):
    library = db.session.get(LibraryPath, library_id)
    if not library:
        return jsonify({'success': False, 'message': '媒体库不存在'}), 404

    if not Path(library.path).is_dir():
        return jsonify({'success': False, 'message': '媒体库路径不存在或不是目录'}), 400

    job_id = start_library_scan(library.id)
    return jsonify({'success': True, 'job_id': job_id})


@app.route('/api/scan-jobs/<job_id>', methods=['GET'])
@login_required
def api_scan_job_status(job_id):
    job = get_scan_job(job_id)
    if not job:
        return jsonify({'success': False, 'message': '扫描任务不存在'}), 404
    return jsonify({'success': True, 'job': job})


@app.route('/api/scan-jobs/<job_id>/cancel', methods=['POST'])
@login_required
def api_cancel_scan_job(job_id):
    job = cancel_scan_job(job_id)
    if not job:
        return jsonify({'success': False, 'message': '扫描任务不存在'}), 404
    return jsonify({'success': True, 'job': job})


@app.route('/api/libraries/<int:library_id>', methods=['DELETE'])
@login_required
def api_delete_library(library_id):
    library = db.session.get(LibraryPath, library_id)
    if not library:
        return jsonify({'success': False, 'message': '媒体库不存在'}), 404

    cancel_scan_jobs_for_library(library.id)
    library.enabled = False
    prefix = library_prefix(library.id)
    with DB_WRITE_LOCK:
        db.session.flush()
        FileItem.query.filter(FileItem.rel_path.like(prefix + '%')).delete(synchronize_session=False)
        db.session.delete(library)
        db.session.commit()

    with SCAN_LOCK:
        for job in SCAN_JOBS.values():
            if job.get('library_id') == library_id and job.get('status') in ('queued', 'running', 'cancelling'):
                job['status'] = 'cancelled'
                job['message'] = '媒体库已移除，扫描已中止'
                job['current_path'] = None
                job['finished_at'] = datetime.now(timezone.utc).isoformat()
                job['updated_at'] = job['finished_at']
    return jsonify({'success': True})


@app.route('/api/directories', methods=['GET'])
@login_required
def api_list_directories():
    raw_path = request.args.get('path', '').strip()
    target = Path(raw_path).expanduser() if raw_path else Path('/')
    if not target.is_absolute():
        return jsonify({'success': False, 'message': '请输入绝对路径'}), 400

    try:
        target = target.resolve()
        if not target.is_dir():
            return jsonify({'success': False, 'message': '目录不存在'}), 404

        dirs = []
        for entry in sorted(target.iterdir(), key=lambda e: e.name.lower()):
            try:
                if not entry.is_dir():
                    continue
                dirs.append({
                    'name': entry.name,
                    'path': str(entry.resolve()),
                })
            except (OSError, PermissionError):
                continue

        parent = target.parent if target.parent != target else None
        return jsonify({
            'success': True,
            'path': str(target),
            'parent': str(parent) if parent else None,
            'dirs': dirs,
        })
    except PermissionError:
        return jsonify({'success': False, 'message': '没有权限访问该目录'}), 403
    except Exception as e:
        return jsonify({'success': False, 'message': f'读取目录失败: {str(e)}'}), 500


def should_show_partition(part):
    device = part.device or ''
    mountpoint = part.mountpoint or ''
    fstype = (part.fstype or '').lower()

    if device.startswith('/dev/loop'):
        return False
    if fstype in {'squashfs', 'tmpfs', 'devtmpfs', 'overlay', 'proc', 'sysfs', 'cgroup', 'cgroup2', 'autofs'}:
        return False
    if mountpoint.startswith(('/snap/', '/proc', '/sys', '/dev', '/run')):
        return False
    return True


@app.route('/api/disks', methods=['GET'])
@login_required
def api_disks():
    """获取磁盘分区使用情况"""
    show_all = request.args.get('all', '0') == '1'
    
    disks = []
    for part in psutil.disk_partitions(all=show_all):
        if not show_all and not should_show_partition(part):
            continue
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
    cleanup_misclassified_items()


if __name__ == '__main__':
    host = os.environ.get('HIVE_HOST', '127.0.0.1')
    port = int(os.environ.get('HIVE_PORT', '5000'))
    debug = os.environ.get('HIVE_DEBUG', '').lower() in ('1', 'true', 'yes', 'on')
    app.run(host=host, port=port, debug=debug)
