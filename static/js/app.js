const app = {
    currentView: 'files',
    currentPath: '',
    fileViewMode: 'list',
    selectedItem: null,
    mediaPage: 1,
    audioPlaylist: [],
    audioIndex: 0,
    searchQuery: '',
    // Picasa 风格照片预览
    photoItems: [],
    lbIndex: 0,
    lbScale: 1,
    lbTx: 0,
    lbTy: 0,
    lbDrag: null,
    slideshowTimer: null,

    async init() {
        const status = await this.api('/api/auth/status');
        if (!status.logged_in) {
            window.location.href = '/login';
            return;
        }
        document.getElementById('current-user').textContent = status.username || 'admin';
        this.switchView('files');
    },

    async api(url, options = {}) {
        try {
            const res = await fetch(url, options);
            if (res.status === 401) {
                window.location.href = '/login';
                return {};
            }
            return await res.json();
        } catch (e) {
            this.toast('请求失败: ' + e.message, 'error');
            return { success: false };
        }
    },

    async logout() {
        await this.api('/api/auth/logout', { method: 'POST' });
        window.location.href = '/login';
    },

    switchView(view) {
        this.currentView = view;
        document.querySelectorAll('.nav-menu li').forEach(li => li.classList.remove('active'));
        document.querySelector(`.nav-menu li[data-view="${view}"]`).classList.add('active');

        const titles = {
            files: '文件管理',
            photos: '照片管理',
            videos: '视频管理',
            audios: '音频管理',
            search: '搜索结果',
            stats: '存储统计',
            disks: '磁盘管理'
        };
        document.getElementById('page-title').textContent = titles[view] || view;

        const showUpload = view === 'files';
        const showMkdir = view === 'files';
        document.getElementById('upload-btn').style.display = showUpload ? 'inline-flex' : 'none';
        document.getElementById('mkdir-btn').style.display = showMkdir ? 'inline-flex' : 'none';
        document.getElementById('global-search').style.display = view === 'search' ? 'none' : 'flex';

        if (view === 'files') this.loadFiles(this.currentPath);
        else if (view === 'photos') { this.mediaPage = 1; this.loadMedia('image'); }
        else if (view === 'videos') { this.mediaPage = 1; this.loadMedia('video'); }
        else if (view === 'audios') { this.mediaPage = 1; this.loadMedia('audio'); }
        else if (view === 'search') this.renderSearch();
        else if (view === 'stats') this.loadStats();
        else if (view === 'disks') this.loadDisks();
    },

    // =====================================================
    // 文件管理
    // =====================================================

    async loadFiles(path = '') {
        this.currentPath = path;
        const data = await this.api(`/api/files?path=${encodeURIComponent(path)}`);
        if (!data.success) return;
        this.renderFiles(data.items, path);
    },

    renderFiles(items, path) {
        const container = document.getElementById('content-area');
        const parts = path.split('/').filter(p => p);
        let crumbHtml = '<div class="breadcrumb"><span onclick="app.loadFiles(\'\')">🏠 首页</span>';
        let crumbPath = '';
        parts.forEach(part => {
            crumbPath += (crumbPath ? '/' : '') + part;
            crumbHtml += ` <span class="sep">/</span> <span onclick="app.loadFiles('${crumbPath}')">${this.escapeHtml(part)}</span>`;
        });
        crumbHtml += '</div>';

        const toolbar = `
            <div class="toolbar">
                <div class="view-toggle">
                    <button class="${this.fileViewMode === 'list' ? 'active' : ''}" onclick="app.setFileView('list')">☰ 列表</button>
                    <button class="${this.fileViewMode === 'grid' ? 'active' : ''}" onclick="app.setFileView('grid')">⊞ 网格</button>
                </div>
                <div>${items.length} 个项目</div>
            </div>
        `;

        if (items.length === 0) {
            container.innerHTML = crumbHtml + toolbar + `
                <div class="empty-state">
                    <div class="icon">📂</div>
                    <p>当前目录为空</p>
                </div>
            `;
            return;
        }

        if (this.fileViewMode === 'list') {
            let html = crumbHtml + toolbar + `
                <div class="file-list">
                    <div class="file-list-header">
                        <div>名称</div>
                        <div>类型</div>
                        <div>大小</div>
                        <div>操作</div>
                    </div>
            `;
            items.forEach(item => {
                const icon = this.getFileIcon(item);
                const typeLabel = this.getCategoryLabel(item.category);
                html += `
                    <div class="file-list-item" ondblclick="app.openItem('${this.escapeJs(item.rel_path)}', ${item.is_dir}, '${item.category}')" onclick="app.selectItem(this, '${this.escapeJs(item.rel_path)}')">
                        <div class="name"><span class="icon">${icon}</span> ${this.escapeHtml(item.name)}</div>
                        <div>${typeLabel}</div>
                        <div>${item.size_formatted || '-'}</div>
                        <div class="actions">
                            ${!item.is_dir ? `<button onclick="event.stopPropagation(); app.downloadFile('${this.escapeJs(item.rel_path)}')" title="下载">⬇️</button>` : ''}
                            <button onclick="event.stopPropagation(); app.renameItem('${this.escapeJs(item.rel_path)}', '${this.escapeJs(item.name)}')" title="重命名">✏️</button>
                            <button onclick="event.stopPropagation(); app.deleteItem('${this.escapeJs(item.rel_path)}', ${item.is_dir})" title="删除">🗑️</button>
                        </div>
                    </div>
                `;
            });
            html += '</div>';
            container.innerHTML = html;
        } else {
            let html = crumbHtml + toolbar + '<div class="file-grid">';
            items.forEach(item => {
                const icon = this.getFileIcon(item);
                const thumb = item.thumbnail ? `/api/thumbnail/${this.encodePath(item.rel_path)}` : null;
                html += `
                    <div class="file-grid-item" onclick="app.selectGridItem(this, '${this.escapeJs(item.rel_path)}')" ondblclick="app.openItem('${this.escapeJs(item.rel_path)}', ${item.is_dir}, '${item.category}')">
                        <div class="file-grid-thumb">
                            ${thumb && item.category === 'image' ? `<img src="${thumb}" loading="lazy">` : `<span class="icon">${icon}</span>`}
                        </div>
                        <div class="file-grid-info">
                            <div class="name" title="${this.escapeHtml(item.name)}">${this.escapeHtml(item.name)}</div>
                            <div class="meta">${item.size_formatted || '-'} · ${this.getCategoryLabel(item.category)}</div>
                        </div>
                    </div>
                `;
            });
            html += '</div>';
            container.innerHTML = html;
        }
    },

    setFileView(mode) {
        this.fileViewMode = mode;
        this.loadFiles(this.currentPath);
    },

    selectItem(el, relPath) {
        document.querySelectorAll('.file-list-item').forEach(i => i.classList.remove('selected'));
        el.classList.add('selected');
        this.selectedItem = relPath;
    },

    selectGridItem(el, relPath) {
        document.querySelectorAll('.file-grid-item').forEach(i => i.classList.remove('selected'));
        el.classList.add('selected');
        this.selectedItem = relPath;
    },

    openItem(relPath, isDir, category) {
        if (isDir) {
            this.loadFiles(relPath);
        } else if (category === 'image') {
            this.openPhotoViewer(relPath);
        } else if (category === 'video') {
            this.openLightbox(relPath, 'video');
        } else if (category === 'audio') {
            this.playAudioByPath(relPath);
        } else {
            window.open(`/api/preview/${this.encodePath(relPath)}`, '_blank');
        }
    },

    getFileIcon(item) {
        if (item.is_dir) return '📁';
        const map = {
            image: '🖼️', video: '🎬', audio: '🎵', document: '📄', other: '📎'
        };
        return map[item.category] || '📎';
    },

    getCategoryLabel(category) {
        const map = {
            dir: '文件夹', image: '图片', video: '视频', audio: '音频',
            document: '文档', other: '其他'
        };
        return map[category] || '其他';
    },

    // =====================================================
    // 操作
    // =====================================================

    openMkdirModal() {
        const name = prompt('请输入新文件夹名称:');
        if (name && name.trim()) {
            this.api('/api/files/mkdir', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: this.currentPath, name: name.trim() })
            }).then(data => {
                if (data.success) {
                    this.toast('文件夹创建成功', 'success');
                    this.loadFiles(this.currentPath);
                } else {
                    this.toast(data.message || '创建失败', 'error');
                }
            });
        }
    },

    openUploadModal() {
        const input = document.createElement('input');
        input.type = 'file';
        input.multiple = true;
        input.onchange = async () => {
            if (!input.files.length) return;
            const formData = new FormData();
            formData.append('path', this.currentPath);
            for (const file of input.files) {
                formData.append('files', file);
            }
            this.toast('正在上传...', 'info');
            const data = await this.api('/api/files/upload', {
                method: 'POST',
                body: formData
            });
            if (data.success) {
                const failed = data.results.filter(r => !r.success);
                if (failed.length) {
                    this.toast(`上传完成，${failed.length} 个失败`, 'error');
                } else {
                    this.toast('上传成功', 'success');
                }
                this.loadFiles(this.currentPath);
            } else {
                this.toast(data.message || '上传失败', 'error');
            }
        };
        input.click();
    },

    renameItem(relPath, oldName) {
        const newName = prompt('请输入新名称:', oldName);
        if (newName && newName.trim() && newName.trim() !== oldName) {
            this.api('/api/files/rename', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: relPath, new_name: newName.trim() })
            }).then(data => {
                if (data.success) {
                    this.toast('重命名成功', 'success');
                    this.loadFiles(this.currentPath);
                } else {
                    this.toast(data.message || '重命名失败', 'error');
                }
            });
        }
    },

    deleteItem(relPath, isDir) {
        if (!confirm(`确定要删除 ${isDir ? '文件夹' : '文件'} 吗? 此操作不可恢复。`)) return;
        this.api('/api/files/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: relPath })
        }).then(data => {
            if (data.success) {
                this.toast('删除成功', 'success');
                this.loadFiles(this.currentPath);
            } else {
                this.toast(data.message || '删除失败', 'error');
            }
        });
    },

    downloadFile(relPath) {
        window.open(`/api/files/download?path=${encodeURIComponent(relPath)}`, '_blank');
    },

    async scanStorage() {
        this.toast('正在扫描文件...', 'info');
        const data = await this.api('/api/scan', { method: 'POST' });
        if (data.success) {
            this.toast(`扫描完成，共 ${data.scanned} 个项目`, 'success');
            if (this.currentView === 'files') this.loadFiles(this.currentPath);
        } else {
            this.toast(data.message || '扫描失败', 'error');
        }
    },

    // =====================================================
    // 媒体视图
    // =====================================================

    async loadMedia(category, append = false) {
        if (!append) this.mediaPage = 1;
        const data = await this.api(`/api/media?category=${category}&page=${this.mediaPage}&per_page=40`);
        if (!data.success) return;
        this.renderMedia(category, data.items, append, data.total);
    },

    renderMedia(category, items, append, total) {
        const container = document.getElementById('content-area');
        if (!append) container.innerHTML = '';

        if (items.length === 0 && this.mediaPage === 1) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="icon">${category === 'image' ? '🖼️' : category === 'video' ? '🎬' : '🎵'}</div>
                    <p>暂无${this.getCategoryLabel(category)}文件</p>
                </div>
            `;
            return;
        }

        if (!append) {
            const wrapperClass = category === 'image' ? 'photo-wall' : 'media-grid';
            container.innerHTML = `
                <div class="toolbar">
                    <div>共 ${total} 个${this.getCategoryLabel(category)}</div>
                </div>
                <div class="${wrapperClass}" id="media-grid"></div>
                <div style="text-align:center; padding:20px;">
                    <button class="btn btn-secondary" id="load-more-btn" onclick="app.loadMoreMedia('${category}')">加载更多</button>
                </div>
            `;
        }

        if (category === 'image') {
            if (!append) this.photoItems = [];
            this.photoItems.push(...items);
        }

        const grid = document.getElementById('media-grid');
        items.forEach(item => {
            const el = document.createElement('div');
            el.className = category === 'image' ? 'photo-item' : 'media-item';
            el.onclick = () => this.openMediaItem(item, category);
            const thumb = item.thumbnail ? `/api/thumbnail/${this.encodePath(item.rel_path)}` : null;

            if (category === 'image') {
                el.innerHTML = `
                    <img src="${thumb || '/static/img/placeholder.svg'}" loading="lazy" alt="${this.escapeHtml(item.name)}">
                    <div class="photo-overlay">
                        <div class="photo-name">${this.escapeHtml(item.name)}</div>
                        <div class="photo-meta">${item.width || '-'} × ${item.height || '-'} · ${this.formatSize(item.size)}</div>
                    </div>
                `;
            } else if (category === 'video') {
                el.innerHTML = `
                    <div class="media-thumb">
                        <img src="${thumb || '/static/img/placeholder.svg'}" loading="lazy">
                        <span class="play-icon">▶️</span>
                    </div>
                    <div class="media-info">
                        <div class="name" title="${this.escapeHtml(item.name)}">${this.escapeHtml(item.name)}</div>
                        <div class="meta">${this.formatDuration(item.duration)} · ${this.formatSize(item.size)}</div>
                    </div>
                `;
            } else {
                el.innerHTML = `
                    <div class="media-thumb" style="background:#f1f2f6;">
                        <span class="icon" style="font-size:64px;">🎵</span>
                    </div>
                    <div class="media-info">
                        <div class="name" title="${this.escapeHtml(item.name)}">${this.escapeHtml(item.name)}</div>
                        <div class="meta">${this.formatDuration(item.duration)} · ${this.formatSize(item.size)}</div>
                    </div>
                `;
            }
            grid.appendChild(el);
        });

        if (items.length < 40) {
            const btn = document.getElementById('load-more-btn');
            if (btn) btn.style.display = 'none';
        }
    },

    loadMoreMedia(category) {
        this.mediaPage++;
        this.loadMedia(category, true);
    },

    openMediaItem(item, category) {
        if (category === 'image') {
            this.openPhotoViewer(item.rel_path);
        } else if (category === 'video') {
            this.openLightbox(item.rel_path, 'video');
        } else {
            this.buildAudioPlaylist(item.rel_path);
        }
    },

    // =====================================================
    // 视频灯箱
    // =====================================================

    openLightbox(relPath, type) {
        const url = `/api/preview/${this.encodePath(relPath)}`;
        const overlay = document.createElement('div');
        overlay.className = 'lightbox';
        overlay.id = 'lightbox';
        overlay.innerHTML = `
            <span class="lightbox-close" onclick="app.closeLightbox()">&times;</span>
            <video controls autoplay src="${url}"></video>
        `;
        overlay.onclick = (e) => { if (e.target === overlay) this.closeLightbox(); };
        document.body.appendChild(overlay);
        this._lbEsc = (e) => { if (e.key === 'Escape') app.closeLightbox(); };
        document.addEventListener('keydown', this._lbEsc);
    },

    closeLightbox() {
        const lb = document.getElementById('lightbox');
        if (lb) lb.remove();
        if (this._lbEsc) document.removeEventListener('keydown', this._lbEsc);
    },

    // =====================================================
    // Picasa 风格照片预览
    // =====================================================

    openPhotoViewer(relPath) {
        let items = this.photoItems || [];
        let index = items.findIndex(i => i.rel_path === relPath);
        if (index < 0) {
            // 从文件浏览器等非相册入口打开：单图模式
            items = [{ rel_path: relPath, name: relPath.split('/').pop() }];
            index = 0;
        }
        this.photoItems = items;
        this.lbIndex = index;

        const overlay = document.createElement('div');
        overlay.className = 'pviewer';
        overlay.id = 'pviewer';
        overlay.innerHTML = `
            <div class="pv-topbar">
                <span class="pv-title" id="pv-title"></span>
                <div class="pv-actions">
                    <button class="pv-btn" id="pv-play" title="幻灯片播放 (空格)" onclick="app.toggleSlideshow()">▶</button>
                    <button class="pv-btn active" id="pv-info-btn" title="信息面板 (i)" onclick="app.toggleInfo()">ⓘ</button>
                    <button class="pv-btn" title="关闭 (Esc)" onclick="app.closePhotoViewer()">✕</button>
                </div>
            </div>
            <div class="pv-body">
                <div class="pv-stage" id="pv-stage">
                    <button class="pv-nav prev" onclick="app.pvPrev()">&#10094;</button>
                    <img id="pv-img" draggable="false" alt="">
                    <button class="pv-nav next" onclick="app.pvNext()">&#10095;</button>
                </div>
                <aside class="pv-sidebar" id="pv-sidebar"></aside>
            </div>
            <div class="pv-filmstrip" id="pv-filmstrip"></div>
        `;
        overlay.onclick = (e) => { if (e.target === overlay) this.closePhotoViewer(); };
        document.body.appendChild(overlay);

        if (this.photoItems.length <= 1) {
            overlay.querySelectorAll('.pv-nav').forEach(n => n.style.display = 'none');
        }

        this.renderFilmstrip();
        this.showPhoto(this.lbIndex);

        document.addEventListener('keydown', this.handlePvKey);
        const stage = document.getElementById('pv-stage');
        const img = document.getElementById('pv-img');
        stage.addEventListener('wheel', this.pvWheel, { passive: false });
        img.addEventListener('mousedown', this.pvDragStart);
        img.addEventListener('dblclick', () => app.pvResetZoom());
        window.addEventListener('mousemove', this.pvDragMove);
        window.addEventListener('mouseup', this.pvDragEnd);
    },

    closePhotoViewer() {
        if (this.slideshowTimer) { clearInterval(this.slideshowTimer); this.slideshowTimer = null; }
        const v = document.getElementById('pviewer');
        if (v) v.remove();
        document.removeEventListener('keydown', this.handlePvKey);
        window.removeEventListener('mousemove', this.pvDragMove);
        window.removeEventListener('mouseup', this.pvDragEnd);
    },

    showPhoto(index) {
        const items = this.photoItems;
        if (!items.length) return;
        this.lbIndex = (index + items.length) % items.length;
        const item = items[this.lbIndex];
        this.pvResetZoom();
        const img = document.getElementById('pv-img');
        if (img) img.src = `/api/preview/${this.encodePath(item.rel_path)}`;
        const title = document.getElementById('pv-title');
        if (title) title.textContent = item.name || '';
        document.querySelectorAll('.pv-thumb').forEach((t, i) => {
            const active = i === this.lbIndex;
            t.classList.toggle('active', active);
            if (active) t.scrollIntoView({ inline: 'center', block: 'nearest', behavior: 'smooth' });
        });
        this.renderInfo(item);
    },

    pvPrev() { this.showPhoto(this.lbIndex - 1); },
    pvNext() { this.showPhoto(this.lbIndex + 1); },

    renderFilmstrip() {
        const strip = document.getElementById('pv-filmstrip');
        if (!strip) return;
        strip.innerHTML = this.photoItems.map((it, i) => {
            const src = it.thumbnail
                ? `/api/thumbnail/${this.encodePath(it.rel_path)}`
                : `/api/preview/${this.encodePath(it.rel_path)}`;
            return `<img class="pv-thumb" src="${src}" loading="lazy" onclick="app.showPhoto(${i})">`;
        }).join('');
    },

    renderInfo(item) {
        const sb = document.getElementById('pv-sidebar');
        if (!sb) return;
        const rows = [];
        rows.push(['文件名', item.name || '-']);
        if (item.width && item.height) rows.push(['尺寸', `${item.width} × ${item.height}`]);
        if (item.size != null) rows.push(['大小', this.formatSize(item.size)]);
        if (item.created_at) rows.push(['加入时间', this.formatDate(item.created_at)]);

        let exif = {};
        if (item.exif) { try { exif = JSON.parse(item.exif); } catch (e) {} }
        const get = (k) => exif[k];
        const dt = get('DateTimeOriginal') || get('DateTime');
        if (dt) rows.push(['拍摄时间', dt]);
        const cam = [get('Make'), get('Model')].filter(Boolean).join(' ');
        if (cam) rows.push(['相机', cam]);
        if (get('LensModel')) rows.push(['镜头', get('LensModel')]);
        const fn = get('FNumber'); if (fn) rows.push(['光圈', `f/${this.evalRatio(fn)}`]);
        const et = get('ExposureTime'); if (et) rows.push(['快门', `${et}s`]);
        const iso = get('ISOSpeedRatings') || get('PhotographicSensitivity'); if (iso) rows.push(['ISO', iso]);
        const fl = get('FocalLength'); if (fl) rows.push(['焦距', `${this.evalRatio(fl)}mm`]);

        sb.innerHTML = '<h3>图片信息</h3>' + rows.map(([k, v]) =>
            `<div class="pv-row"><span class="k">${this.escapeHtml(k)}</span><span class="v">${this.escapeHtml(String(v))}</span></div>`
        ).join('');
    },

    evalRatio(v) {
        v = String(v).replace(/[()]/g, '').trim();
        if (v.includes('/')) {
            const [a, b] = v.split('/').map(Number);
            if (b) return +(a / b).toFixed(2);
        }
        const n = Number(v);
        return isNaN(n) ? v : +n.toFixed(2);
    },

    toggleInfo() {
        const v = document.getElementById('pviewer');
        const btn = document.getElementById('pv-info-btn');
        if (!v) return;
        const hidden = v.classList.toggle('info-hidden');
        if (btn) btn.classList.toggle('active', !hidden);
    },

    toggleSlideshow() {
        const btn = document.getElementById('pv-play');
        if (this.slideshowTimer) {
            clearInterval(this.slideshowTimer);
            this.slideshowTimer = null;
            if (btn) { btn.textContent = '▶'; btn.classList.remove('active'); }
        } else {
            this.slideshowTimer = setInterval(() => app.pvNext(), 3000);
            if (btn) { btn.textContent = '⏸'; btn.classList.add('active'); }
            this.pvNext();
        }
    },

    // 缩放 / 平移
    applyTransform() {
        const img = document.getElementById('pv-img');
        if (!img) return;
        img.style.transform = `translate(${this.lbTx}px, ${this.lbTy}px) scale(${this.lbScale})`;
        img.classList.toggle('zoomed', this.lbScale > 1);
    },

    pvResetZoom() {
        this.lbScale = 1; this.lbTx = 0; this.lbTy = 0;
        this.applyTransform();
    },

    pvWheel: (e) => {
        e.preventDefault();
        const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
        app.lbScale = Math.min(8, Math.max(1, app.lbScale * factor));
        if (app.lbScale === 1) { app.lbTx = 0; app.lbTy = 0; }
        app.applyTransform();
    },

    pvDragStart: (e) => {
        if (app.lbScale <= 1) return;
        e.preventDefault();
        app.lbDrag = { x: e.clientX, y: e.clientY, tx: app.lbTx, ty: app.lbTy };
        const img = document.getElementById('pv-img');
        if (img) img.classList.add('grabbing');
    },

    pvDragMove: (e) => {
        if (!app.lbDrag) return;
        app.lbTx = app.lbDrag.tx + (e.clientX - app.lbDrag.x);
        app.lbTy = app.lbDrag.ty + (e.clientY - app.lbDrag.y);
        app.applyTransform();
    },

    pvDragEnd: () => {
        app.lbDrag = null;
        const img = document.getElementById('pv-img');
        if (img) img.classList.remove('grabbing');
    },

    handlePvKey: (e) => {
        switch (e.key) {
            case 'Escape': app.closePhotoViewer(); break;
            case 'ArrowLeft': app.pvPrev(); break;
            case 'ArrowRight': app.pvNext(); break;
            case '+': case '=':
                app.lbScale = Math.min(8, app.lbScale * 1.2); app.applyTransform(); break;
            case '-': case '_':
                app.lbScale = Math.max(1, app.lbScale / 1.2);
                if (app.lbScale === 1) { app.lbTx = 0; app.lbTy = 0; }
                app.applyTransform(); break;
            case ' ': e.preventDefault(); app.toggleSlideshow(); break;
            case 'i': case 'I': app.toggleInfo(); break;
        }
    },

    // =====================================================
    // 音频播放
    // =====================================================

    async buildAudioPlaylist(startRelPath) {
        const data = await this.api('/api/media?category=audio&per_page=1000');
        if (!data.success) return;
        this.audioPlaylist = data.items;
        const index = this.audioPlaylist.findIndex(i => i.rel_path === startRelPath);
        this.audioIndex = index >= 0 ? index : 0;
        this.playCurrentAudio();
        document.getElementById('audio-player').classList.remove('hidden');
    },

    playAudioByPath(relPath) {
        this.buildAudioPlaylist(relPath);
    },

    playCurrentAudio() {
        const item = this.audioPlaylist[this.audioIndex];
        if (!item) return;
        const audio = document.getElementById('audio-element');
        audio.src = `/api/preview/${this.encodePath(item.rel_path)}`;
        audio.play();
        document.getElementById('audio-title').textContent = item.name;
        document.getElementById('audio-meta').textContent = `${this.formatDuration(item.duration)} · ${this.formatSize(item.size)}`;
        document.getElementById('audio-play-btn').textContent = '⏸';
    },

    audioToggle() {
        const audio = document.getElementById('audio-element');
        const btn = document.getElementById('audio-play-btn');
        if (audio.paused) {
            audio.play();
            btn.textContent = '⏸';
        } else {
            audio.pause();
            btn.textContent = '▶️';
        }
    },

    audioNext() {
        if (!this.audioPlaylist.length) return;
        this.audioIndex = (this.audioIndex + 1) % this.audioPlaylist.length;
        this.playCurrentAudio();
    },

    audioPrev() {
        if (!this.audioPlaylist.length) return;
        this.audioIndex = (this.audioIndex - 1 + this.audioPlaylist.length) % this.audioPlaylist.length;
        this.playCurrentAudio();
    },

    // =====================================================
    // 搜索
    // =====================================================

    renderSearch() {
        const container = document.getElementById('content-area');
        container.innerHTML = `
            <div class="search-results-header">
                <h3>搜索: <span id="search-keyword">${this.escapeHtml(this.searchQuery) || '输入关键词开始搜索'}</span></h3>
            </div>
            <div id="search-grid" class="file-grid"></div>
        `;
        if (this.searchQuery) this.doSearch();
    },

    async doSearch() {
        const input = document.getElementById('search-input');
        const q = input ? input.value.trim() : this.searchQuery;
        if (!q) return;
        this.searchQuery = q;

        if (this.currentView !== 'search') {
            this.switchView('search');
            return;
        }

        document.getElementById('search-keyword').textContent = q;
        const grid = document.getElementById('search-grid');
        grid.innerHTML = '<div class="spinner" style="margin:40px auto;"></div>';

        const data = await this.api(`/api/search?q=${encodeURIComponent(q)}&per_page=100`);
        grid.innerHTML = '';
        if (!data.success || !data.items.length) {
            grid.innerHTML = `
                <div class="empty-state" style="grid-column: 1/-1;">
                    <div class="icon">🔍</div>
                    <p>未找到相关文件</p>
                </div>
            `;
            return;
        }

        data.items.forEach(item => {
            const icon = this.getFileIcon(item);
            const thumb = item.thumbnail ? `/api/thumbnail/${this.encodePath(item.rel_path)}` : null;
            const el = document.createElement('div');
            el.className = 'file-grid-item';
            el.ondblclick = () => this.openItem(item.rel_path, item.is_dir, item.category);
            el.innerHTML = `
                <div class="file-grid-thumb">
                    ${thumb && item.category === 'image' ? `<img src="${thumb}" loading="lazy">` : `<span class="icon">${icon}</span>`}
                </div>
                <div class="file-grid-info">
                    <div class="name" title="${this.escapeHtml(item.name)}">${this.escapeHtml(item.name)}</div>
                    <div class="meta">${this.getCategoryLabel(item.category)} · ${item.size ? this.formatSize(item.size) : '-'}</div>
                </div>
            `;
            grid.appendChild(el);
        });
    },

    // =====================================================
    // 统计
    // =====================================================

    async loadStats() {
        const data = await this.api('/api/stats');
        if (!data.success) return;
        const container = document.getElementById('content-area');
        const catLabels = { image: '图片', video: '视频', audio: '音频', document: '文档', other: '其他' };
        const catIcons = { image: '🖼️', video: '🎬', audio: '🎵', document: '📄', other: '📎' };

        let html = `
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="label">文件总数</div>
                    <div class="value">${data.total_files}</div>
                </div>
                <div class="stat-card">
                    <div class="label">文件夹总数</div>
                    <div class="value">${data.total_dirs}</div>
                </div>
                <div class="stat-card">
                    <div class="label">总占用空间</div>
                    <div class="value">${data.total_size_formatted}</div>
                </div>
            </div>
            <h3 style="margin-bottom:16px;">分类详情</h3>
            <div class="stats-grid">
        `;
        for (const [cat, info] of Object.entries(data.categories)) {
            html += `
                <div class="stat-card">
                    <div class="label">${catIcons[cat]} ${catLabels[cat]}</div>
                    <div class="value">${info.count}</div>
                    <div class="sub">占用空间: ${info.size_formatted}</div>
                </div>
            `;
        }
        html += '</div>';
        container.innerHTML = html;
    },

    // =====================================================
    // 磁盘管理
    // =====================================================

    async loadDisks() {
        const data = await this.api('/api/disks');
        if (!data.success) return;
        const container = document.getElementById('content-area');

        let html = '<h3 style="margin-bottom:16px;">💾 磁盘分区</h3><div class="disk-grid">';
        if (!data.disks || data.disks.length === 0) {
            html += `
                <div class="empty-state" style="grid-column:1/-1;">
                    <div class="icon">💾</div>
                    <p>未检测到可用磁盘分区</p>
                </div>
            `;
        } else {
            data.disks.forEach(disk => {
                const colorClass = disk.percent >= 90 ? 'danger' : disk.percent >= 70 ? 'warning' : 'success';
                html += `
                    <div class="disk-card">
                        <div class="disk-header">
                            <div class="disk-title">${this.escapeHtml(disk.device || '未知设备')}</div>
                            <div class="disk-mount" title="${this.escapeHtml(disk.mountpoint)}">${this.escapeHtml(disk.mountpoint)}</div>
                        </div>
                        <div class="disk-meta">
                            <span>文件系统: ${disk.fstype || '-'}</span>
                            <span class="disk-percent ${colorClass}">${disk.percent}%</span>
                        </div>
                        <div class="progress-bar">
                            <div class="progress-fill ${colorClass}" style="width:${disk.percent}%"></div>
                        </div>
                        <div class="disk-size">
                            <span>已用 ${disk.used_formatted}</span>
                            <span>可用 ${disk.free_formatted}</span>
                            <span>共 ${disk.total_formatted}</span>
                        </div>
                    </div>
                `;
            });
        }
        html += '</div>';

        if (data.memory) {
            const memColor = data.memory.percent >= 90 ? 'danger' : data.memory.percent >= 70 ? 'warning' : 'success';
            html += `
                <h3 style="margin: 24px 0 16px;">🧠 内存</h3>
                <div class="disk-card" style="max-width:600px;">
                    <div class="disk-header">
                        <div class="disk-title">系统内存</div>
                        <div class="disk-percent ${memColor}">${data.memory.percent}%</div>
                    </div>
                    <div class="progress-bar">
                        <div class="progress-fill ${memColor}" style="width:${data.memory.percent}%"></div>
                    </div>
                    <div class="disk-size">
                        <span>可用 ${data.memory.available_formatted}</span>
                        <span>共 ${data.memory.total_formatted}</span>
                    </div>
                </div>
            `;
        }

        container.innerHTML = html;
    },

    // =====================================================
    // 工具
    // =====================================================

    toast(message, type = 'info') {
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.textContent = message;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 3000);
    },

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },

    escapeJs(text) {
        return text.replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '\\"');
    },

    encodePath(path) {
        return encodeURIComponent(path).replace(/%2F/g, '/');
    },

    formatSize(bytes) {
        if (bytes == null) return '-';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        let size = bytes;
        for (const unit of units) {
            if (size < 1024) return size.toFixed(2) + ' ' + unit;
            size /= 1024;
        }
        return size.toFixed(2) + ' PB';
    },

    formatDuration(seconds) {
        if (seconds == null) return '-';
        const m = Math.floor(seconds / 60);
        const s = Math.floor(seconds % 60);
        return `${m}:${s.toString().padStart(2, '0')}`;
    },

    formatDate(iso) {
        if (!iso) return '-';
        const d = new Date(iso);
        if (isNaN(d)) return iso;
        const p = (n) => n.toString().padStart(2, '0');
        return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
    }
};

// 音频播放结束时自动下一首
document.addEventListener('DOMContentLoaded', () => {
    const audio = document.getElementById('audio-element');
    if (audio) {
        audio.addEventListener('ended', () => app.audioNext());
    }
    app.init();
});
