import os
import re
import glob
import subprocess
import webbrowser
import threading
import concurrent.futures
import sys
import urllib.request
import zipfile
import io
import shutil
import math
import json
import base64

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QTabWidget, QLabel, QLineEdit, 
                             QTextEdit, QPushButton, QCheckBox, QComboBox, 
                             QFileDialog, QMessageBox, QListWidget, QSlider, QGroupBox, QMenu,
                             QListWidgetItem, QStackedWidget, QToolTip, QGridLayout, QAbstractItemView, QProgressBar)
from PyQt6.QtCore import Qt, QUrl, pyqtSignal, QObject, QTime, QRect, QTimer
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtGui import QKeySequence, QShortcut, QPainter, QColor, QDragEnterEvent, QDropEvent, QIcon, QPixmap, QImage

try:
    import mutagen
    from mutagen.id3 import ID3, TIT2, TPE1, TPE2, TALB, TCON, TDRC, TRCK, TPOS, APIC
    from mutagen.mp4 import MP4, MP4Cover
    from mutagen.oggopus import OggOpus
    from mutagen.flac import Picture
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False

# ==========================================
# 1. 경로 및 설정 관리
# ==========================================
class Config:
    USER_HOME = os.path.expanduser("~")
    BASE_DIR = os.getcwd() 
    
    DEFAULT_PATHS = {
        "도구 폴더 (yt-dlp, FFmpeg)": BASE_DIR,
        "쿠키 파일 (cookies.txt)": os.path.join(BASE_DIR, "cookies", "cookies.txt"),
        "Node.js (node.exe)": r"C:\Program Files\nodejs\node.exe",
        "영상 저장 폴더": os.path.join(USER_HOME, "Videos", "yt-dlp"),
        "음원 저장 폴더": os.path.join(USER_HOME, "Music", "yt-dlp")
    }

    @staticmethod
    def init_folders():
        os.makedirs(Config.DEFAULT_PATHS["영상 저장 폴더"], exist_ok=True)
        os.makedirs(Config.DEFAULT_PATHS["음원 저장 폴더"], exist_ok=True)

# ==========================================
# 2. 스레드 통신용 시그널
# ==========================================
class WorkerSignals(QObject):
    log_msg = pyqtSignal(str)
    finished = pyqtSignal(int, int, int) # [성공, 오류, 취소] 개수 전달
    error = pyqtSignal(str)
    update_ui = pyqtSignal()
    dl_start = pyqtSignal(int)
    dl_progress = pyqtSignal(int, float)

class ExportSignals(QObject):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str)

class ExtractorSignals(QObject):
    item_status = pyqtSignal(int, str)
    progress = pyqtSignal(int, float)
    total_start = pyqtSignal(int)
    finished = pyqtSignal(int, int, int) # [성공, 오류, 취소] 개수 전달

# ==========================================
# 공통 UI 컴포넌트
# ==========================================
class DropVideoWidget(QVideoWidget):
    file_dropped = pyqtSignal(list)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            paths = [u.toLocalFile() for u in urls if u.toLocalFile()]
            if paths:
                event.acceptProposedAction()
                QTimer.singleShot(10, lambda p=paths: self.file_dropped.emit(p))

# ==========================================
# 3. 탭 1: 미디어 다운로더
# ==========================================
class DownloaderTab(QWidget):
    def __init__(self):
        super().__init__()
        self.total_tasks = 0
        self.task_progress = {}
        self.task_stats = {'success': 0, 'error': 0, 'canceled': 0}
        
        self.is_downloading = False
        self.cancel_requested = False
        self.active_processes = []
        self.proc_lock = threading.Lock()
        self.stats_lock = threading.Lock()
        
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        url_group = QGroupBox("다운로드 주소 입력 (엔터 또는 쉼표로 다중 입력)")
        url_layout = QVBoxLayout()
        self.url_text = QTextEdit()
        self.url_text.setMinimumHeight(120)  
        url_layout.addWidget(self.url_text)
        url_group.setLayout(url_layout)
        layout.addWidget(url_group, stretch=2)

        path_group = QGroupBox("설정 및 경로 (비워두면 시스템 기본 폴더 자동 적용)")
        path_layout = QVBoxLayout()
        
        top_row_layout = QHBoxLayout()
        top_row_layout.addStretch() 
        
        btn_github = QPushButton("⭐ GitHub")
        btn_github.clicked.connect(lambda: webbrowser.open("https://github.com/hwang4826/YtdlpStudio"))
        top_row_layout.addWidget(btn_github)

        self.btn_install_menu = QPushButton("🛠 필수 도구 설치")
        install_menu = QMenu()
        action_install_tools = install_menu.addAction("yt-dlp 및 FFmpeg 자동 설치")
        action_install_tools.triggered.connect(self.install_tools)
        action_install_node = install_menu.addAction("Node.js 공식 다운로드 페이지 열기")
        action_install_node.triggered.connect(lambda: webbrowser.open("https://nodejs.org/ko/download/"))
        self.btn_install_menu.setMenu(install_menu)
        top_row_layout.addWidget(self.btn_install_menu)
        
        path_layout.addLayout(top_row_layout)

        grid = QGridLayout()
        self.path_entries = {}
        
        grid_items = [
            ("도구 폴더 (yt-dlp, FFmpeg)", 0, 0),
            ("영상 저장 폴더", 0, 3),
            ("Node.js (node.exe)", 1, 0),
            ("음원 저장 폴더", 1, 3)
        ]

        for key, row, col in grid_items:
            grid.addWidget(QLabel(key), row, col)
            entry = QLineEdit()
            entry.setPlaceholderText(Config.DEFAULT_PATHS[key])
            self.path_entries[key] = entry
            grid.addWidget(entry, row, col + 1)
            btn_browse = QPushButton("찾아보기")
            btn_browse.clicked.connect(lambda checked, k=key: self.browse_path(k))
            grid.addWidget(btn_browse, row, col + 2)
            
        path_layout.addLayout(grid)
        path_group.setLayout(path_layout)
        layout.addWidget(path_group)

        opt_group = QGroupBox("다운로드 옵션")
        opt_layout = QHBoxLayout()
        
        self.chk_video = QCheckBox("영상 다운로드")
        self.chk_video.setChecked(True)
        self.cb_vid_ext = QComboBox()
        self.cb_vid_ext.addItems(["추천", "mp4", "webm", "mkv", "ts"])
        
        self.chk_audio = QCheckBox("음원 추출")
        self.cb_aud_ext = QComboBox()
        self.cb_aud_ext.addItems(["추천", "mp3", "m4a", "aac", "opus"])
        
        self.chk_playlist = QCheckBox("재생목록 전체 다운로드")
        
        self.chk_cookie = QCheckBox("쿠키 사용")
        self.entry_cookie = QLineEdit()
        self.entry_cookie.setPlaceholderText(Config.DEFAULT_PATHS["쿠키 파일 (cookies.txt)"])
        self.entry_cookie.setVisible(False)
        self.path_entries["쿠키 파일 (cookies.txt)"] = self.entry_cookie
        
        self.btn_cookie = QPushButton("찾아보기")
        self.btn_cookie.setVisible(False)
        self.btn_cookie.clicked.connect(lambda checked, k="쿠키 파일 (cookies.txt)": self.browse_path(k))
        
        self.chk_cookie.toggled.connect(self.entry_cookie.setVisible)
        self.chk_cookie.toggled.connect(self.btn_cookie.setVisible)

        opt_layout.addWidget(self.chk_video)
        opt_layout.addWidget(self.cb_vid_ext)
        opt_layout.addSpacing(15)
        opt_layout.addWidget(self.chk_audio)
        opt_layout.addWidget(self.cb_aud_ext)
        opt_layout.addSpacing(15)
        opt_layout.addWidget(self.chk_playlist)
        opt_layout.addSpacing(15)
        
        cookie_layout = QHBoxLayout()
        cookie_layout.addWidget(self.chk_cookie)
        cookie_layout.addWidget(self.entry_cookie)
        cookie_layout.addWidget(self.btn_cookie)
        cookie_layout.setContentsMargins(0, 0, 0, 0)
        
        opt_layout.addLayout(cookie_layout)
        opt_layout.addStretch()
        opt_group.setLayout(opt_layout)
        layout.addWidget(opt_group)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar { border: 1px solid #555; border-radius: 5px; text-align: center; height: 20px; }
            QProgressBar::chunk { background-color: #00aa00; width: 10px; }
        """)
        layout.addWidget(self.progress_bar)

        btn_layout = QHBoxLayout()
        self.btn_download = QPushButton("다운로드 시작 (병렬 처리)")
        self.btn_download.setMinimumHeight(40)
        self.btn_download.clicked.connect(self.toggle_download)
        
        self.btn_update = QPushButton("yt-dlp 수동 업데이트")
        self.btn_update.setMinimumHeight(40)
        self.btn_update.clicked.connect(self.manual_update)
        
        btn_layout.addWidget(self.btn_download, stretch=3)
        btn_layout.addWidget(self.btn_update, stretch=1)
        layout.addLayout(btn_layout)

        log_group = QGroupBox("실행 로그")
        log_layout = QVBoxLayout()
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setStyleSheet("background-color: black; color: #00FF00; font-family: Consolas;")
        log_layout.addWidget(self.log_area)
        log_group.setLayout(log_layout)
        layout.addWidget(log_group, stretch=1)

        self.setLayout(layout)
        self.signals = WorkerSignals()
        self.signals.log_msg.connect(self.append_log)
        self.signals.finished.connect(self.download_finished)
        self.signals.dl_start.connect(self.on_dl_start)
        self.signals.dl_progress.connect(self.on_dl_progress)

    def browse_path(self, key):
        if "폴더" in key:
            path = QFileDialog.getExistingDirectory(self, f"{key} 선택")
        else:
            path, _ = QFileDialog.getOpenFileName(self, f"{key} 선택")
        if path:
            self.path_entries[key].setText(os.path.normpath(path))

    def get_setting(self, key):
        val = self.path_entries[key].text().strip()
        return val if val else Config.DEFAULT_PATHS[key]

    def get_exe(self, exe_name):
        folder = self.get_setting("도구 폴더 (yt-dlp, FFmpeg)")
        full_path = os.path.join(folder, exe_name)
        return f'"{full_path}"' if os.path.exists(full_path) else f'"{exe_name}"'

    def append_log(self, text):
        self.log_area.insertPlainText(text)
        self.log_area.verticalScrollBar().setValue(self.log_area.verticalScrollBar().maximum())

    def toggle_download(self):
        if self.is_downloading:
            self.cancel_download()
        else:
            self.start_download()

    def start_download(self):
        raw_urls = [u.strip() for u in re.split(r'[\n,]+', self.url_text.toPlainText()) if u.strip()]
        if not raw_urls:
            QMessageBox.warning(self, "경고", "다운로드할 URL을 입력해주세요.")
            return

        urls = []
        for u in raw_urls:
            if not self.chk_playlist.isChecked():
                u = re.sub(r'&list=[^&]+', '', u)
                u = re.sub(r'&index=\d+', '', u)
            urls.append(u)

        self.is_downloading = True
        self.cancel_requested = False
        with self.stats_lock:
            self.task_stats = {'success': 0, 'error': 0, 'canceled': 0}
            
        self.btn_download.setEnabled(False)
        self.btn_download.setText("다운로드 준비 중...")
        threading.Thread(target=self.download_manager, args=(urls,), daemon=True).start()

    def cancel_download(self):
        self.cancel_requested = True
        self.signals.log_msg.emit("\n[시스템] 🛑 사용자가 다운로드 취소를 요청했습니다. 프로세스를 정리 중입니다...\n")
        self.btn_download.setEnabled(False)
        self.btn_download.setText("취소 처리 중...")
        self.btn_download.setStyleSheet("")
        
        with self.proc_lock:
            for p in self.active_processes:
                try:
                    subprocess.run(f'taskkill /F /T /PID {p.pid}', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
                except Exception:
                    pass

    def run_cmd(self, cmd, prefix="", task_id=None):
        process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        
        with self.proc_lock:
            self.active_processes.append(process)
            
        downloaded_file = None
            
        for line in iter(process.stdout.readline, b''):
            if self.cancel_requested:
                break 
            try:
                decoded_line = line.decode('utf-8')
            except UnicodeDecodeError:
                decoded_line = line.decode('cp949', errors='replace')
            
            if task_id is not None:
                m = re.search(r'\[download\]\s+([\d\.]+)%', decoded_line)
                if m:
                    self.signals.dl_progress.emit(task_id, float(m.group(1)))

            m_dest = re.search(r'Destination:\s*(.+)$', decoded_line)
            m_merge = re.search(r'Merging formats into\s*"(.+)"', decoded_line)
            if m_merge:
                downloaded_file = m_merge.group(1).strip()
            elif m_dest and not downloaded_file:
                downloaded_file = m_dest.group(1).strip()

            self.signals.log_msg.emit(f"{prefix}{decoded_line}")
            
        process.stdout.close()
        process.wait()
        
        with self.proc_lock:
            if process in self.active_processes:
                self.active_processes.remove(process)
                
        return process.returncode, downloaded_file

    def install_tools(self):
        def _task():
            tool_dir = self.get_setting("도구 폴더 (yt-dlp, FFmpeg)")
            os.makedirs(tool_dir, exist_ok=True)
            self.signals.log_msg.emit("\n[시스템] 공식 깃허브에서 yt-dlp 직접 다운로드를 시작합니다...\n")
            try:
                ytdlp_url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
                req = urllib.request.Request(ytdlp_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req) as response:
                    with open(os.path.join(tool_dir, "yt-dlp.exe"), 'wb') as out_file:
                        shutil.copyfileobj(response, out_file)
                self.signals.log_msg.emit("[시스템] yt-dlp.exe 설치 완료!\n")
            except Exception as e:
                self.signals.log_msg.emit(f"[시스템] ❌ yt-dlp 다운로드 실패: {e}\n")

            self.signals.log_msg.emit("\n[시스템] FFmpeg 다운로드를 시작합니다. (시간이 조금 걸립니다...)\n")
            try:
                ffmpeg_url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
                req = urllib.request.Request(ffmpeg_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req) as response:
                    zip_data = response.read()
                    with zipfile.ZipFile(io.BytesIO(zip_data)) as z:
                        for file_info in z.infolist():
                            if file_info.filename.endswith("ffmpeg.exe"):
                                with z.open(file_info) as zf, open(os.path.join(tool_dir, "ffmpeg.exe"), 'wb') as f:
                                    shutil.copyfileobj(zf, f)
                            elif file_info.filename.endswith("ffprobe.exe"):
                                with z.open(file_info) as zf, open(os.path.join(tool_dir, "ffprobe.exe"), 'wb') as f:
                                    shutil.copyfileobj(zf, f)
                self.signals.log_msg.emit("[시스템] FFmpeg 설치 완료!\n")
            except Exception as e:
                self.signals.log_msg.emit(f"[시스템] ❌ FFmpeg 다운로드 실패: {e}\n")
            self.signals.log_msg.emit("\n[시스템] 🎉 필수 도구 설치 작업이 끝났습니다!\n")
        threading.Thread(target=_task, daemon=True).start()

    def manual_update(self):
        self.btn_update.setEnabled(False)
        def _task():
            self.signals.log_msg.emit("\n[업데이트] yt-dlp 버전을 확인합니다...\n")
            self.run_cmd(f'{self.get_exe("yt-dlp.exe")} -U')
            self.signals.log_msg.emit("[업데이트] 완료.\n")
            self.btn_update.setEnabled(True)
        threading.Thread(target=_task, daemon=True).start()

    def download_manager(self, urls):
        self.signals.dl_start.emit(len(urls))
        self.signals.log_msg.emit(f"\n{'='*50}\n🚀 총 {len(urls)}개의 작업을 병렬로 시작합니다!\n{'='*50}\n")
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(self.download_task, url, idx + 1) for idx, url in enumerate(urls)]
            concurrent.futures.wait(futures)
        
        if not self.cancel_requested:
            self.signals.log_msg.emit(f"\n{'='*50}\n🎉 모든 다운로드 작업이 완료되었습니다!\n{'='*50}\n")
        self.signals.finished.emit(self.task_stats['success'], self.task_stats['error'], self.task_stats['canceled'])

    def on_dl_start(self, total):
        self.total_tasks = total
        self.task_progress = {i+1: 0.0 for i in range(total)}
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.btn_download.setEnabled(True)
        self.btn_download.setText("다운로드 진행 중... (0%) ❌ 클릭 시 취소")
        self.btn_download.setStyleSheet("background-color: #8b0000; color: white; font-weight: bold;")

    def on_dl_progress(self, task_id, pct):
        if self.cancel_requested: return
        self.task_progress[task_id] = pct
        if self.total_tasks > 0:
            avg = sum(self.task_progress.values()) / self.total_tasks
            self.progress_bar.setValue(int(avg))
            self.btn_download.setText(f"다운로드 진행 중... ({int(avg)}%) ❌ 클릭 시 취소")

    def get_audio_codec(self, filepath):
        ffprobe = self.get_exe("ffprobe.exe").strip('"')
        cmd = f'"{ffprobe}" -v error -select_streams a:0 -show_entries stream=codec_name -of default=nw=1:nk=1 "{filepath}"'
        try:
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, creationflags=0x08000000, stdin=subprocess.DEVNULL)
            return proc.stdout.strip().lower()
        except:
            return "aac"

    def download_task(self, url, task_id):
        if self.cancel_requested: 
            with self.stats_lock: self.task_stats['canceled'] += 1
            return
            
        prefix = f"[작업 {task_id}] "
        try:
            yt_dlp = self.get_exe("yt-dlp.exe")
            ffmpeg_path = self.get_exe("ffmpeg.exe").strip('"')
            opts = []
            if os.path.isabs(ffmpeg_path):
                opts.append(f'--ffmpeg-location "{os.path.dirname(ffmpeg_path)}"')
            
            node_path = self.get_setting("Node.js (node.exe)")
            if os.path.exists(node_path):
                opts.append(f'--js-runtimes node:"{node_path}"')
                
            if self.chk_cookie.isChecked():
                opts.append(f'--cookies "{self.get_setting("쿠키 파일 (cookies.txt)")}"')
            
            if not self.chk_playlist.isChecked():
                opts.append("--no-playlist")
            
            opts.append("--newline")  

            common_opts = " ".join(opts)
            vid_ext = self.cb_vid_ext.currentText()
            only_audio = not self.chk_video.isChecked() and self.chk_audio.isChecked()
            ext_opt = f'--merge-output-format {vid_ext}' if (vid_ext != "추천" and not only_audio) else ""

            if only_audio:
                download_fmt = "ba/bestaudio/b"
            else:
                download_fmt = "bv*+ba/b"

            url_lower = url.lower()
            if "manifest" in url_lower or ".m3u8" in url_lower or ".smil" in url_lower:
                out_tmpl = f"%(title)s_{task_id}.%(ext)s"
            else:
                out_tmpl = "%(title)s.%(ext)s"

            vid_path = os.path.join(self.get_setting("영상 저장 폴더"), out_tmpl)
            cmd = f'{yt_dlp} {common_opts} {ext_opt} -f "{download_fmt}" -o "{vid_path}" "{url}"'
            
            self.signals.log_msg.emit(f"{prefix}📥 다운로드 시작...\n")
            
            retcode, downloaded_file = self.run_cmd(cmd, prefix, task_id)
            
            if self.cancel_requested:
                self.signals.log_msg.emit(f"{prefix}🛑 작업 취소됨\n")
                with self.stats_lock: self.task_stats['canceled'] += 1
                return
                
            if retcode != 0:
                self.signals.log_msg.emit(f"{prefix}❌ 다운로드 실패\n")
                with self.stats_lock: self.task_stats['error'] += 1
                return
            
            if self.chk_audio.isChecked():
                if downloaded_file and os.path.exists(downloaded_file):
                    self.extract_audio_task_safe(downloaded_file, prefix)
                else:
                    self.signals.log_msg.emit(f"{prefix}⚠️️ 정확한 파일명을 찾지 못해 추출에 실패했습니다.\n")
                    with self.stats_lock: self.task_stats['error'] += 1
                    return

            self.signals.log_msg.emit(f"{prefix}✨ 완료되었습니다!\n")
            with self.stats_lock: self.task_stats['success'] += 1
            
        except Exception as e:
            self.signals.log_msg.emit(f"{prefix}❌ 오류: {str(e)}\n")
            with self.stats_lock: self.task_stats['error'] += 1

    def extract_audio_task_safe(self, exact_file, prefix):
        aud_ext = self.cb_aud_ext.currentText()
        original_codec = self.get_audio_codec(exact_file)
        
        if aud_ext == "추천":
            if original_codec == "opus": out_ext = "opus"
            elif original_codec == "mp3": out_ext = "mp3"
            else: out_ext = "m4a"
        else:
            out_ext = aud_ext
            
        name = os.path.splitext(os.path.basename(exact_file))[0]
        output_file = os.path.join(self.get_setting("음원 저장 폴더"), f"{name}.{out_ext}")
        
        ffmpeg = self.get_exe("ffmpeg.exe")
        is_compatible = (aud_ext == "추천") or (out_ext == "opus" and original_codec == "opus") or (out_ext == "mp3" and original_codec == "mp3") or (out_ext == "m4a" and original_codec == "aac")
                        
        if is_compatible:
            ffmpeg_cmd = f'{ffmpeg} -y -i "{exact_file}" -vn -c:a copy "{output_file}"'
        else:
            ffmpeg_cmd = f'{ffmpeg} -y -i "{exact_file}" -vn -b:a 192k "{output_file}"'
        
        self.signals.log_msg.emit(f"{prefix}🎵 음원 추출 진행 중...\n")
        
        self.run_cmd(ffmpeg_cmd, prefix)
        
        if not self.chk_video.isChecked():
            try: os.remove(exact_file)
            except: pass

    def download_finished(self, success, error, canceled):
        self.is_downloading = False
        self.cancel_requested = False
        self.btn_download.setEnabled(True)
        self.btn_download.setText("다운로드 시작 (병렬 처리)")
        self.btn_download.setStyleSheet("")
        self.progress_bar.setVisible(False)
        
        if self.total_tasks > 0:
            if canceled > 0 and success == 0 and error == 0:
                QMessageBox.information(self, "취소됨", "다운로드 작업이 취소되었습니다.")
            elif error > 0:
                QMessageBox.warning(self, "완료 (오류 발생)", f"총 {success + error}개 작업 중\n{success}개 성공, {error}개 실패했습니다.")
            else:
                QMessageBox.information(self, "완료", f"총 {success}개의 다운로드 작업이 성공적으로 완료되었습니다.")


# ==========================================
# 4. 탭 2: 미디어 편집기
# ==========================================
class EditorTimelineSlider(QSlider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setMouseTracking(True) 
        self.sections = [] 
        self.temp_section = None 
        self.duration_ms = 0
        self.player_ref = None
        self.format_time_ref = None

    def set_sections(self, sections_data):
        self.sections = sections_data
        self.update()

    def set_temp_section(self, section_data):
        self.temp_section = section_data
        self.update()

    def set_duration(self, duration):
        self.duration_ms = duration
        self.setRange(0, duration)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.duration_ms <= 0: return

        painter = QPainter(self)
        width = self.width()
        height = self.height()
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)

        for start_ms, end_ms, color in self.sections:
            x1 = int((start_ms / self.duration_ms) * width)
            x2 = int((end_ms / self.duration_ms) * width)
            rect = QRect(x1, 2, max(1, x2 - x1), height - 4)
            painter.fillRect(rect, color)
            
            s_text = self.format_time_ref(start_ms) if self.format_time_ref else QTime(0, 0, 0).addMSecs(start_ms).toString("hh:mm:ss")
            e_text = self.format_time_ref(end_ms) if self.format_time_ref else QTime(0, 0, 0).addMSecs(end_ms).toString("hh:mm:ss")
            fm = painter.fontMetrics()
            text_w = fm.horizontalAdvance(s_text)
            if rect.width() > text_w * 2.5:
                painter.setPen(Qt.GlobalColor.white)
                painter.drawText(x1 + 2, height // 2 + 4, s_text)
                painter.drawText(x2 - text_w - 2, height // 2 + 4, e_text)
            
        if self.temp_section:
            s_ms, e_ms, color = self.temp_section
            x1 = int((s_ms / self.duration_ms) * width)
            x2 = int((e_ms / self.duration_ms) * width)
            if x1 > x2: x1, x2 = x2, x1 
            rect = QRect(x1, 2, max(1, x2 - x1), height - 4)
            painter.fillRect(rect, color)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if self.duration_ms <= 0: return
        hover_x = event.position().x()
        hover_ms = int((hover_x / self.width()) * self.duration_ms)
        for s, e, _ in self.sections:
            if s <= hover_ms <= e:
                s_fmt = self.format_time_ref(s) if self.format_time_ref else s
                e_fmt = self.format_time_ref(e) if self.format_time_ref else e
                QToolTip.showText(event.globalPosition().toPoint(), f"{s_fmt} ~ {e_fmt}", self)
                return
        QToolTip.hideText()

    def mousePressEvent(self, event):
        if self.duration_ms > 0 and event.button() == Qt.MouseButton.LeftButton:
            click_x = event.position().x()
            click_ms = int((click_x / self.width()) * self.duration_ms)
            margin = self.duration_ms * 0.02
            for start_ms, end_ms, color in self.sections:
                if abs(click_ms - start_ms) < margin:
                    self.player_ref.setPosition(start_ms)
                    return
                elif abs(click_ms - end_ms) < margin:
                    self.player_ref.setPosition(end_ms)
                    return
            val = int((click_x / self.width()) * self.maximum())
            self.setValue(val)
            self.sliderMoved.emit(val)
            if self.player_ref: self.player_ref.setPosition(val)
        super().mousePressEvent(event)

class EditorSectionWidget(QWidget):
    def __init__(self, start_text, end_text, list_widget, item, parent_tab, is_pending=False):
        super().__init__()
        self.list_widget = list_widget
        self.item = item
        self.parent_tab = parent_tab
        self.start_time = start_text
        layout = QHBoxLayout()
        layout.setContentsMargins(5, 2, 5, 2)
        self.lbl_text = QLabel(f"{start_text} ~ {end_text}")
        layout.addWidget(self.lbl_text, stretch=1)
        if is_pending:
            self.lbl_text.setStyleSheet("color: #ff5555; font-weight: bold;")
            self.setStyleSheet("background-color: rgba(255, 100, 100, 30); border: 1px dashed red;")
        else:
            btn_del = QPushButton("삭제")
            btn_del.clicked.connect(self.delete_section)
            layout.addWidget(btn_del)
        self.setLayout(layout)
        
    def delete_section(self):
        row = self.list_widget.row(self.item)
        self.parent_tab.sections_list.pop(row)
        self.parent_tab.refresh_section_list_ui()

    def mouseDoubleClickEvent(self, event):
        if hasattr(self, 'start_time'):
            start_ms = self.parent_tab.time_to_ms(self.start_time)
            self.parent_tab.player.setPosition(start_ms)
        super().mouseDoubleClickEvent(event)

class EditorTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.current_file = ""
        self.keyframes = [] 
        self.marking_start_ms = None 
        self.sections_list = [] 
        self.colors = [QColor(255, 50, 50, 150), QColor(50, 255, 50, 150), QColor(50, 50, 255, 150), QColor(255, 200, 0, 150), QColor(200, 50, 255, 150)]
        self.setAcceptDrops(True)
        
        self.is_exporting = False
        self.cancel_requested = False
        self.active_processes = []
        self.proc_lock = threading.Lock()
        
        self.init_ui()
        self.setup_shortcuts()

    def init_ui(self):
        layout = QVBoxLayout()

        file_layout = QHBoxLayout()
        self.lbl_file = QLabel("선택된 파일 없음 (아래 점선 박스에 영상을 드래그 앤 드롭하세요)")
        self.lbl_kf_status = QLabel("")
        self.lbl_kf_status.setStyleSheet("color: gray;")
        btn_open = QPushButton("찾아보기...")
        btn_open.clicked.connect(self.open_file)
        self.btn_close_media = QPushButton("현재 영상 닫기")
        self.btn_close_media.clicked.connect(self.close_media)
        self.btn_close_media.setVisible(False)
        file_layout.addWidget(self.lbl_file, stretch=1)
        file_layout.addWidget(self.lbl_kf_status)
        file_layout.addWidget(btn_open)
        file_layout.addWidget(self.btn_close_media)
        layout.addLayout(file_layout)

        self.video_stack = QStackedWidget()
        self.video_stack.setMinimumHeight(350)
        self.lbl_drop = QLabel("📂 이 곳에 단일 영상/음원 파일을 드래그 앤 드롭하세요")
        self.lbl_drop.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_drop.setStyleSheet("background-color: #1e1e1e; color: #aaaaaa; font-size: 18px; border: 2px dashed #555555;")
        self.video_widget = DropVideoWidget()
        self.video_widget.file_dropped.connect(self.load_media_from_drop)
        self.video_widget.setStyleSheet("background-color: black;")
        self.video_stack.addWidget(self.lbl_drop)
        self.video_stack.addWidget(self.video_widget)
        layout.addWidget(self.video_stack, stretch=1)

        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(0.5)
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.positionChanged.connect(self.position_changed)
        self.player.durationChanged.connect(self.duration_changed)

        control_layout = QHBoxLayout()
        self.slider = EditorTimelineSlider(Qt.Orientation.Horizontal)
        self.slider.player_ref = self.player
        self.slider.format_time_ref = self.format_time
        self.slider.sliderMoved.connect(self.set_position)
        self.lbl_time = QLabel("00:00:00.0 / 00:00:00.0")
        control_layout.addWidget(self.slider)
        control_layout.addWidget(self.lbl_time)
        layout.addLayout(control_layout)

        media_ctrl_layout = QHBoxLayout()
        self.btn_play = QPushButton("▶ 재생 (Space)")
        self.btn_play.setFixedWidth(120) 
        self.btn_play.clicked.connect(self.toggle_play)
        media_ctrl_layout.addWidget(self.btn_play)
        media_ctrl_layout.addSpacing(20)
        media_ctrl_layout.addWidget(QLabel("배속:"))
        self.cb_speed = QComboBox()
        self.cb_speed.addItems(["2.0x", "1.0x", "0.75x", "0.5x", "0.25x", "0.1x"])
        self.cb_speed.setCurrentText("1.0x")
        self.cb_speed.currentTextChanged.connect(lambda t: self.player.setPlaybackRate(float(t.replace('x', ''))))
        media_ctrl_layout.addWidget(self.cb_speed)
        media_ctrl_layout.addSpacing(20)
        media_ctrl_layout.addWidget(QLabel("음량:"))
        self.slider_vol = QSlider(Qt.Orientation.Horizontal)
        self.slider_vol.setRange(0, 100)
        self.slider_vol.setValue(50)
        self.slider_vol.setFixedWidth(100)
        self.slider_vol.valueChanged.connect(lambda v: self.audio_output.setVolume(v / 100.0))
        media_ctrl_layout.addWidget(self.slider_vol)
        media_ctrl_layout.addStretch()
        layout.addLayout(media_ctrl_layout)

        cut_group = QGroupBox("구간 자르기 목록")
        cut_layout = QHBoxLayout()
        self.list_sections = QListWidget()
        cut_layout.addWidget(self.list_sections, stretch=2)

        manual_layout = QVBoxLayout()
        time_input_layout = QHBoxLayout()
        self.entry_start = QLineEdit("00:00:00.0")
        self.entry_end = QLineEdit("00:00:00.0")
        time_input_layout.addWidget(QLabel("시작:"))
        time_input_layout.addWidget(self.entry_start)
        time_input_layout.addWidget(QLabel("종료:"))
        time_input_layout.addWidget(self.entry_end)
        manual_layout.addLayout(time_input_layout)
        
        self.chk_precision = QCheckBox("정밀 자르기 (재인코딩/0.001초 제어)")
        self.chk_precision.stateChanged.connect(self.on_precision_changed)
        manual_layout.addWidget(self.chk_precision)
        
        self.chk_merge = QCheckBox("리스트 모든 구간 병합")
        self.chk_merge.setChecked(True)
        manual_layout.addWidget(self.chk_merge)

        audio_opt_layout = QHBoxLayout()
        self.chk_ext_audio = QCheckBox("음원으로 추출")
        self.cb_ext_audio = QComboBox()
        self.cb_ext_audio.addItems(["추천", "mp3", "m4a", "aac", "opus"])
        audio_opt_layout.addWidget(self.chk_ext_audio)
        audio_opt_layout.addWidget(self.cb_ext_audio)
        manual_layout.addLayout(audio_opt_layout)

        export_setting_layout = QHBoxLayout()
        self.entry_out_dir = QLineEdit()
        self.entry_out_dir.setPlaceholderText("저장 폴더 (기본: 원본 폴더)")
        self.btn_out_dir = QPushButton("변경")
        self.btn_out_dir.clicked.connect(self.change_out_dir)
        self.entry_out_name = QLineEdit()
        self.entry_out_name.setPlaceholderText("파일명: 원본_edited")
        export_setting_layout.addWidget(QLabel("위치:"))
        export_setting_layout.addWidget(self.entry_out_dir, stretch=2)
        export_setting_layout.addWidget(self.btn_out_dir)
        export_setting_layout.addWidget(QLabel("이름:"))
        export_setting_layout.addWidget(self.entry_out_name, stretch=2)
        manual_layout.addLayout(export_setting_layout)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar { border: 1px solid #555; border-radius: 5px; text-align: center; height: 20px; }
            QProgressBar::chunk { background-color: #00aa00; width: 10px; }
        """)
        manual_layout.addWidget(self.progress_bar)

        self.btn_export = QPushButton("내보내기 실행")
        self.btn_export.setMinimumHeight(40)
        self.btn_export.clicked.connect(self.toggle_export)
        manual_layout.addWidget(self.btn_export)

        cut_layout.addLayout(manual_layout, stretch=1)
        cut_group.setLayout(cut_layout)
        layout.addWidget(cut_group)
        self.setLayout(layout)

    def on_precision_changed(self):
        self.refresh_section_list_ui()
        if self.player.duration() > 0:
            self.update_time_label(self.player.position(), self.player.duration())
        try:
            s_ms = self.time_to_ms(self.entry_start.text())
            e_ms = self.time_to_ms(self.entry_end.text())
            self.entry_start.setText(self.format_time(s_ms))
            self.entry_end.setText(self.format_time(e_ms))
        except:
            pass

    def change_out_dir(self):
        path = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", self.entry_out_dir.text() or Config.DEFAULT_PATHS["영상 저장 폴더"])
        if path: self.entry_out_dir.setText(os.path.normpath(path))

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls(): event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            paths = [u.toLocalFile() for u in urls if u.toLocalFile()]
            if paths:
                event.acceptProposedAction()
                QTimer.singleShot(10, lambda p=paths: self.load_media_from_drop(p))

    def load_media_from_drop(self, paths):
        if paths: self.load_media(paths[0])

    def setup_shortcuts(self):
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self.toggle_play)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, lambda: self.seek(-1000))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, lambda: self.seek(1000))
        QShortcut(QKeySequence(Qt.Key.Key_Up), self, lambda: self.seek(-60000))
        QShortcut(QKeySequence(Qt.Key.Key_Down), self, lambda: self.seek(60000))
        QShortcut(QKeySequence("Shift+Left"), self, lambda: self.seek(-100))
        QShortcut(QKeySequence("Shift+Right"), self, lambda: self.seek(100))
        QShortcut(QKeySequence("Ctrl+Left"), self, lambda: self.jump_section(-1))
        QShortcut(QKeySequence("Ctrl+Right"), self, lambda: self.jump_section(1))
        QShortcut(QKeySequence("["), self, self.mark_start)
        QShortcut(QKeySequence("]"), self, self.mark_end)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self, self.delete_selected_list_item)

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "미디어 파일 선택", Config.DEFAULT_PATHS["영상 저장 폴더"])
        if path: self.load_media(path)

    def load_media(self, path):
        self.current_file = path
        self.lbl_file.setText(os.path.basename(path))
        self.entry_out_dir.setText(os.path.dirname(path))
        self.entry_out_name.setText("")
        self.video_stack.setCurrentIndex(1) 
        self.btn_close_media.setVisible(True)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        self.btn_play.setText("⏸ 일시정지 (Space)")
        self.sections_list.clear()
        self.refresh_section_list_ui()
        self.marking_start_ms = None
        self.slider.set_temp_section(None)
        self.extract_keyframes(path)

    def close_media(self):
        self.player.stop()
        self.player.setSource(QUrl())
        self.current_file = ""
        self.video_stack.setCurrentIndex(0)
        self.btn_close_media.setVisible(False)
        self.lbl_file.setText("선택된 파일 없음 (아래 점선 박스에 영상을 드래그 앤 드롭하세요)")
        self.lbl_kf_status.setText("")
        self.sections_list.clear()
        self.refresh_section_list_ui()

    def extract_keyframes(self, path):
        self.keyframes = []
        self.lbl_kf_status.setText("| 키프레임 분석 중...")
        def _kf_task():
            ffprobe = self.main_window.downloader_tab.get_exe("ffprobe.exe").strip('"')
            cmd = f'"{ffprobe}" -loglevel error -skip_frame nokey -select_streams v:0 -show_entries frame=pkt_pts_time -of default=noprint_wrappers=1:nokey=1 "{path}"'
            try:
                proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, creationflags=0x08000000, stdin=subprocess.DEVNULL)
                kfs = [float(line.strip()) * 1000 for line in proc.stdout.splitlines() if line.strip()]
                self.keyframes = sorted(kfs)
                self.lbl_kf_status.setText(f"| 키프레임 {len(self.keyframes)}개 장전 완료")
            except Exception:
                self.lbl_kf_status.setText("| 키프레임 분석 실패")
        threading.Thread(target=_kf_task, daemon=True).start()

    def seek_keyframe(self, direction):
        if not self.keyframes:
            self.seek(direction * 1000); return
        current_pos = self.player.position()
        if direction > 0: 
            for kf in self.keyframes:
                if kf > current_pos + 100: self.player.setPosition(int(kf)); return
        else: 
            for kf in reversed(self.keyframes):
                if kf < current_pos - 100: self.player.setPosition(int(kf)); return

    def jump_section(self, direction):
        if not self.sections_list: return
        pos = self.player.position()
        if direction < 0:
            candidates = [s for s, e in self.sections_list if s < pos - 100]
            if candidates:
                target_s = max(candidates)
                if pos - target_s < 5000:
                    older_candidates = [s for s, e in self.sections_list if s < target_s - 100]
                    if older_candidates: target_s = max(older_candidates)
                self.player.setPosition(target_s)
        else:
            candidates = [s for s, e in self.sections_list if s > pos + 100]
            if candidates: self.player.setPosition(min(candidates))

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause(); self.btn_play.setText("▶ 재생 (Space)")
        else:
            self.player.play(); self.btn_play.setText("⏸ 일시정지 (Space)")

    def seek(self, ms):
        new_pos = max(0, min(self.player.duration(), self.player.position() + ms))
        self.player.setPosition(new_pos)

    def position_changed(self, position):
        if not self.slider.isSliderDown(): self.slider.setValue(position)
        self.update_time_label(position, self.player.duration())
        if self.marking_start_ms is not None:
            self.slider.set_temp_section((self.marking_start_ms, position, QColor(255, 100, 100, 150)))

    def duration_changed(self, duration):
        self.slider.set_duration(duration)
        self.update_time_label(self.player.position(), duration)

    def set_position(self, position): self.player.setPosition(position)

    def format_time(self, ms): 
        t = QTime(0, 0, 0).addMSecs(ms)
        base = t.toString("hh:mm:ss")
        if self.chk_precision.isChecked():
            return f"{base}.{ms % 1000:03d}"
        else:
            return f"{base}.{(ms % 1000) // 100:01d}"

    def time_to_ms(self, t_str): 
        try:
            parts = t_str.split('.')
            ms = QTime.fromString(parts[0], "hh:mm:ss").msecsSinceStartOfDay()
            if len(parts) > 1:
                frac = parts[1].ljust(3, '0')[:3]
                ms += int(frac)
            return ms
        except:
            return 0

    def update_time_label(self, pos, dur):
        self.lbl_time.setText(f"{self.format_time(pos)} / {self.format_time(dur)}")

    def mark_start(self):
        pos = self.player.position()
        for s, e in self.sections_list:
            if s <= pos <= e: return 
        self.marking_start_ms = pos
        self.refresh_section_list_ui()

    def mark_end(self):
        pos = self.player.position()
        S = self.marking_start_ms
        if S is None:
            candidates = [sec[0] for sec in self.sections_list if sec[0] <= pos]
            if not candidates: return 
            S = max(candidates)
        E = pos
        if S > E: S, E = E, S
        self.marking_start_ms = None
        self.slider.set_temp_section(None)
        self.commit_section(S, E)

    def commit_section(self, S, E):
        if S >= E: return
        new_list = []
        for s, e in self.sections_list:
            if max(s, S) < min(e, E): pass 
            else: new_list.append((s, e))
        new_list.append((S, E))
        new_list.sort(key=lambda x: x[0])
        self.sections_list = new_list
        self.refresh_section_list_ui()

    def delete_selected_list_item(self):
        rows = [self.list_sections.row(item) for item in self.list_sections.selectedItems()]
        for row in sorted(rows, reverse=True): self.sections_list.pop(row)
        self.refresh_section_list_ui()

    def refresh_section_list_ui(self):
        self.list_sections.clear()
        sections_data = []
        for i, (s_ms, e_ms) in enumerate(self.sections_list):
            item = QListWidgetItem(self.list_sections)
            item.setSizeHint(QWidget().sizeHint())
            widget = EditorSectionWidget(self.format_time(s_ms), self.format_time(e_ms), self.list_sections, item, self)
            self.list_sections.setItemWidget(item, widget)
            color = self.colors[i % len(self.colors)]
            sections_data.append((s_ms, e_ms, color))
            
        if self.marking_start_ms is not None:
            item = QListWidgetItem(self.list_sections)
            item.setSizeHint(QWidget().sizeHint())
            widget = EditorSectionWidget(self.format_time(self.marking_start_ms), "(지정 중...)", self.list_sections, item, self, is_pending=True)
            self.list_sections.setItemWidget(item, widget)
        self.slider.set_sections(sections_data)

    def _run_ffmpeg_with_progress(self, cmd, signals, prefix="", current_offset_ms=0, total_ms=0):
        process = subprocess.Popen(cmd, shell=True, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace', creationflags=0x08000000)
        
        with self.proc_lock:
            self.active_processes.append(process)
            
        for line in iter(process.stderr.readline, ''):
            if self.cancel_requested:
                break
            m = re.search(r'time=(\d{2,3}):(\d{2}):(\d{2}\.\d+)', line)
            if m:
                h, m_s, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
                ms = int((h * 3600 + m_s * 60 + s) * 1000)
                
                pct = 0
                if total_ms > 0:
                    pct = int(((current_offset_ms + ms) / total_ms) * 100)
                    pct = min(100, max(0, pct))
                
                time_str = f"{h:02d}:{m_s:02d}:{int(s):02d}"
                signals.progress.emit(pct, f"{prefix}{time_str}")
                
        process.stderr.close()
        process.wait()
        
        with self.proc_lock:
            if process in self.active_processes:
                self.active_processes.remove(process)
                
        if self.cancel_requested:
            raise Exception("작업이 취소되었습니다.")
        if process.returncode != 0:
            raise Exception("FFmpeg 처리 중 오류 발생")

    def toggle_export(self):
        if self.is_exporting:
            self.cancel_export()
        else:
            self.execute_export()

    def cancel_export(self):
        self.cancel_requested = True
        self.btn_export.setEnabled(False)
        self.btn_export.setText("취소 처리 중...")
        self.btn_export.setStyleSheet("")
        
        with self.proc_lock:
            for p in self.active_processes:
                try:
                    subprocess.run(f'taskkill /F /T /PID {p.pid}', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
                except Exception:
                    pass

    def execute_export(self):
        if not self.current_file:
            QMessageBox.warning(self, "경고", "먼저 파일을 불러오세요.")
            return
        if not self.sections_list:
            if QMessageBox.question(self, "확인", "전체를 변환할까요?") == QMessageBox.StandardButton.No: return

        self.is_exporting = True
        self.cancel_requested = False
        self.btn_export.setText("작업 시작 준비 중... ❌ 클릭 시 취소")
        self.btn_export.setStyleSheet("background-color: #8b0000; color: white; font-weight: bold;")
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        
        is_audio = self.chk_ext_audio.isChecked()
        audio_ext = self.cb_ext_audio.currentText()
        is_merge = self.chk_merge.isChecked()
        is_precision = self.chk_precision.isChecked()
        out_dir = self.entry_out_dir.text().strip()
        custom_name = self.entry_out_name.text().strip()
        
        if is_audio and audio_ext == "추천":
            ffprobe = self.main_window.downloader_tab.get_exe("ffprobe.exe").strip('"')
            cmd_probe = f'"{ffprobe}" -v error -select_streams a:0 -show_entries stream=codec_name -of default=nw=1:nk=1 "{self.current_file}"'
            try:
                proc = subprocess.run(cmd_probe, shell=True, capture_output=True, text=True, creationflags=0x08000000)
                orig_codec = proc.stdout.strip().lower()
                if orig_codec == 'opus': audio_ext = 'opus'
                elif orig_codec == 'mp3': audio_ext = 'mp3'
                else: audio_ext = 'm4a'
            except:
                audio_ext = 'm4a'
        
        sections_to_process = [(self.format_time(s), self.format_time(e)) for s, e in self.sections_list]
        
        self.export_signals = ExportSignals()
        self.export_signals.progress.connect(self.on_export_progress)
        self.export_signals.finished.connect(self.on_export_finished)
        
        threading.Thread(target=self._export_task, args=(self.current_file, sections_to_process, is_audio, audio_ext, is_merge, is_precision, out_dir, custom_name, self.export_signals), daemon=True).start()

    def on_export_progress(self, pct, text):
        if self.cancel_requested: return
        self.btn_export.setText(f"내보내기 진행 중... ({pct}%) - {text} ❌ 클릭 시 취소")
        if pct >= 0:
            self.progress_bar.setValue(pct)

    def on_export_finished(self, success, msg):
        self.is_exporting = False
        self.cancel_requested = False
        self.progress_bar.setVisible(False)
        self.btn_export.setEnabled(True)
        self.btn_export.setText("내보내기 실행")
        self.btn_export.setStyleSheet("")
        
        if self.cancel_requested:
            QMessageBox.information(self, "취소됨", "미디어 내보내기 작업이 취소되었습니다.")
        elif success:
            QMessageBox.information(self, "완료", "미디어 내보내기가 성공적으로 완료되었습니다.")
        else:
            QMessageBox.warning(self, "오류", f"내보내기 중 오류가 발생했습니다:\n{msg}")

    def _export_task(self, input_file, sections, is_audio, audio_ext, is_merge, is_precision, out_dir, custom_name, signals):
        ffmpeg = self.main_window.downloader_tab.get_exe("ffmpeg.exe").strip('"')
        if not out_dir: out_dir = os.path.dirname(input_file)
        base_name = custom_name if custom_name else f"{os.path.splitext(os.path.basename(input_file))[0]}_edited"
        ext = os.path.splitext(input_file)[1] if not is_audio else f".{audio_ext}"
        created_files = []

        try:
            if not sections: sections = [("00:00:00.000", self.format_time(self.player.duration()))]
            
            total_ms = sum(self.time_to_ms(e) - self.time_to_ms(s) for s, e in sections)
            processed_ms = 0
            
            for idx, (start, end) in enumerate(sections):
                out_path = os.path.join(out_dir, f"{base_name}{ext}") if (len(sections)==1 and not is_merge) else os.path.join(out_dir, f"{base_name}_cut_{idx:03}{ext}")
                cmd = f'"{ffmpeg}" -y -ss {start} -to {end} -i "{input_file}" '
                if is_audio: cmd += f'-vn -c:a {"copy" if audio_ext in ["m4a", "opus"] else "libmp3lame"} '
                else: cmd += f'-c:v libx264 -preset fast -crf 18 ' if is_precision else f'-c copy '
                cmd += f'"{out_path}"'
                
                prefix = f"[{idx+1}/{len(sections)} 구간] " if len(sections) > 1 else ""
                self._run_ffmpeg_with_progress(cmd, signals, prefix, processed_ms, total_ms)
                created_files.append(out_path)
                processed_ms += (self.time_to_ms(end) - self.time_to_ms(start))

            if is_merge and len(created_files) > 1:
                merge_list = os.path.join(out_dir, "merge_list.txt")
                with open(merge_list, "w", encoding="utf-8") as f:
                    for cf in created_files: f.write(f"file '{cf}'\n")
                merged_out = os.path.join(out_dir, f"{base_name}{ext}")
                cmd = f'"{ffmpeg}" -y -f concat -safe 0 -i "{merge_list}" -c copy "{merged_out}"'
                
                signals.progress.emit(99, "최종 구간 병합 중...")
                self._run_ffmpeg_with_progress(cmd, signals, "", 0, 0)
                os.remove(merge_list)
                for cf in created_files: os.remove(cf)
                    
            signals.finished.emit(True, "완료")
        except Exception as e:
            signals.finished.emit(False, str(e))


# ==========================================
# 5. 탭 3: 비디오 병합기 (리스트 전용)
# ==========================================
class MergerItemWidget(QWidget):
    def __init__(self, video_data, list_widget, item, parent_tab):
        super().__init__()
        self.list_widget = list_widget
        self.item = item
        self.parent_tab = parent_tab
        
        layout = QHBoxLayout()
        layout.setContentsMargins(5, 2, 5, 2)
        
        name = os.path.basename(video_data['path'])
        info = f"{name} ({video_data['width']}x{video_data['height']}, {video_data['fps']}fps, {video_data['vcodec']})"
        self.lbl_text = QLabel(info)
        layout.addWidget(self.lbl_text, stretch=1)

        btn_up = QPushButton("▲")
        btn_up.setFixedWidth(30)
        btn_up.clicked.connect(self.move_up)
        layout.addWidget(btn_up)

        btn_down = QPushButton("▼")
        btn_down.setFixedWidth(30)
        btn_down.clicked.connect(self.move_down)
        layout.addWidget(btn_down)

        btn_del = QPushButton("삭제")
        btn_del.clicked.connect(self.delete_item)
        layout.addWidget(btn_del)

        self.setLayout(layout)

    def move_up(self):
        row = self.list_widget.row(self.item)
        if row > 0:
            self.parent_tab.videos.insert(row - 1, self.parent_tab.videos.pop(row))
            self.parent_tab.refresh_list_ui()

    def move_down(self):
        row = self.list_widget.row(self.item)
        if row < len(self.parent_tab.videos) - 1:
            self.parent_tab.videos.insert(row + 1, self.parent_tab.videos.pop(row))
            self.parent_tab.refresh_list_ui()

    def delete_item(self):
        row = self.list_widget.row(self.item)
        self.parent_tab.videos.pop(row)
        self.parent_tab.refresh_list_ui()

class MergerTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.videos = [] 
        self.needs_reencoding = False
        
        self.is_merging = False
        self.cancel_requested = False
        self.active_processes = []
        self.proc_lock = threading.Lock()
        
        self.setAcceptDrops(True)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        top_layout = QHBoxLayout()
        self.lbl_status = QLabel("병합할 영상을 차례대로 추가하세요.")
        btn_add = QPushButton("영상 추가...")
        btn_add.clicked.connect(self.add_files_dialog)
        top_layout.addWidget(self.lbl_status, stretch=1)
        top_layout.addWidget(btn_add)
        layout.addLayout(top_layout)

        self.lbl_drop = QLabel("📂 이 곳에 영상 파일들을 드래그 앤 드롭하세요")
        self.lbl_drop.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_drop.setStyleSheet("background-color: #1e1e1e; color: #aaaaaa; font-size: 18px; border: 2px dashed #555555; padding: 30px;")
        layout.addWidget(self.lbl_drop)

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget, stretch=1)

        opt_group = QGroupBox("병합 설정")
        opt_layout = QHBoxLayout()
        
        self.cb_resolution = QComboBox()
        self.cb_resolution.addItems(["최고 해상도 기준", "최저 해상도 기준", "1920x1080 (FHD)", "1280x720 (HD)"])
        self.cb_fps = QComboBox()
        self.cb_fps.addItems(["최고 프레임 기준", "최저 프레임 기준", "60 FPS", "30 FPS"])
        
        opt_layout.addWidget(QLabel("해상도 맞춤:"))
        opt_layout.addWidget(self.cb_resolution)
        opt_layout.addSpacing(20)
        opt_layout.addWidget(QLabel("프레임 맞춤:"))
        opt_layout.addWidget(self.cb_fps)
        
        self.lbl_size_est = QLabel("예상 용량: 계산 불가 (무손실 병합 대기 중)")
        self.lbl_size_est.setStyleSheet("color: #00aa00; font-weight: bold;")
        opt_layout.addStretch()
        opt_layout.addWidget(self.lbl_size_est)
        opt_group.setLayout(opt_layout)
        layout.addWidget(opt_group)

        export_setting_layout = QHBoxLayout()
        self.entry_out_dir = QLineEdit()
        self.entry_out_dir.setPlaceholderText("저장 폴더 (영상을 넣으면 자동으로 절대경로가 표시됩니다)")
        self.btn_out_dir = QPushButton("폴더 변경")
        self.btn_out_dir.clicked.connect(self.change_out_dir)

        self.entry_out_name = QLineEdit()
        self.entry_out_name.setPlaceholderText("파일명: 첫번째영상_merged")

        export_setting_layout.addWidget(QLabel("저장 위치:"))
        export_setting_layout.addWidget(self.entry_out_dir, stretch=2)
        export_setting_layout.addWidget(self.btn_out_dir)
        export_setting_layout.addSpacing(10)
        export_setting_layout.addWidget(QLabel("파일명:"))
        export_setting_layout.addWidget(self.entry_out_name, stretch=2)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar { border: 1px solid #555; border-radius: 5px; text-align: center; height: 20px; }
            QProgressBar::chunk { background-color: #00aa00; width: 10px; }
        """)

        self.btn_export = QPushButton("병합 실행")
        self.btn_export.setMinimumHeight(40)
        self.btn_export.clicked.connect(self.toggle_export)
        
        bottom_layout = QVBoxLayout()
        bottom_layout.addLayout(export_setting_layout)
        bottom_layout.addWidget(self.progress_bar)
        bottom_layout.addWidget(self.btn_export)
        layout.addLayout(bottom_layout)

        self.setLayout(layout)
        self.cb_resolution.setEnabled(False)
        self.cb_fps.setEnabled(False)

    def change_out_dir(self):
        default_dir = self.entry_out_dir.text() or Config.DEFAULT_PATHS["영상 저장 폴더"]
        path = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", default_dir)
        if path:
            self.entry_out_dir.setText(os.path.normpath(path))

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls(): event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls: 
            paths = [u.toLocalFile() for u in urls if u.toLocalFile()]
            if paths:
                event.acceptProposedAction()
                QTimer.singleShot(10, lambda p=paths: self.process_dropped_files(p))

    def add_files_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "병합할 영상 선택", Config.DEFAULT_PATHS["영상 저장 폴더"])
        if paths: self.process_dropped_files(paths)

    def process_dropped_files(self, paths):
        was_empty = (len(self.videos) == 0)
        
        for path in paths:
            info = self.extract_video_info(path)
            if not info: 
                continue
            
            if self.videos:
                ref = self.videos[0]
                is_compat = (info['width'] == ref['width'] and info['height'] == ref['height'] and 
                             info['fps'] == ref['fps'] and info['vcodec'] == ref['vcodec'])
                
                if not is_compat and not self.needs_reencoding:
                    msg = f"'{os.path.basename(path)}' 영상이 기존 영상들과 해상도/프레임/코덱이 다릅니다.\n병합하려면 재인코딩이 필요합니다. 추가하시겠습니까?"
                    if QMessageBox.question(self, "규격 불일치", msg) == QMessageBox.StandardButton.No:
                        continue
                    self.needs_reencoding = True
            self.videos.append(info)
            
        if self.videos and was_empty:
            first_path = self.videos[0]['path']
            self.entry_out_dir.setText(os.path.abspath(os.path.dirname(first_path)))
            base_name = os.path.splitext(os.path.basename(first_path))[0]
            self.entry_out_name.setText(f"{base_name}_merged")
            
        self.refresh_list_ui()

    def extract_video_info(self, path):
        ffprobe = self.main_window.downloader_tab.get_exe("ffprobe.exe").strip('"')
        cmd = f'"{ffprobe}" -v error -select_streams v:0 -show_entries format=duration:stream=width,height,r_frame_rate,codec_name -print_format json "{path}"'
        try:
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding='utf-8', errors='replace', creationflags=0x08000000, stdin=subprocess.DEVNULL)
            if not proc.stdout.strip() or not proc.stdout.strip().startswith('{'):
                return None
                
            data = json.loads(proc.stdout)
            fmt = data.get('format', {})
            streams = data.get('streams', [])
            if not streams: return None
            
            vs = streams[0]
            w = vs.get('width', 0)
            h = vs.get('height', 0)
            vcodec = vs.get('codec_name', 'unknown')
            fps_str = vs.get('r_frame_rate', '30/1')
            
            dur_val = fmt.get('duration')
            dur = float(dur_val) * 1000 if dur_val else 0
            
            if '/' in fps_str:
                num, den = map(int, fps_str.split('/'))
                fps = round(num / den, 2) if den != 0 else 30
            else: 
                fps = round(float(fps_str), 2)
            
            size_mb = os.path.getsize(path) / (1024*1024)
            return {'path': path, 'width': w, 'height': h, 'fps': fps, 'vcodec': vcodec, 'duration': dur, 'size_mb': size_mb}
        except Exception:
            return None

    def refresh_list_ui(self):
        self.list_widget.clear()
        
        self.needs_reencoding = False
        if len(self.videos) > 1:
            ref = self.videos[0]
            for v in self.videos[1:]:
                if v['width']!=ref['width'] or v['height']!=ref['height'] or v['fps']!=ref['fps'] or v['vcodec']!=ref['vcodec']:
                    self.needs_reencoding = True; break

        total_dur = sum(v['duration'] for v in self.videos)
        
        for i, v in enumerate(self.videos):
            item = QListWidgetItem(self.list_widget)
            item.setSizeHint(QWidget().sizeHint())
            widget = MergerItemWidget(v, self.list_widget, item, self)
            self.list_widget.setItemWidget(item, widget)
            
        if self.needs_reencoding:
            self.cb_resolution.setEnabled(True)
            self.cb_fps.setEnabled(True)
            self.lbl_status.setText(f"총 {len(self.videos)}개 영상 대기 중 (재인코딩 병합 모드)")
            est_mb = (total_dur / 1000) * 1.0 
            self.lbl_size_est.setText(f"예상 용량: 약 {int(est_mb)} MB (설정에 따라 변동)")
            self.lbl_size_est.setStyleSheet("color: #aa5500; font-weight: bold;")
        else:
            self.cb_resolution.setEnabled(False)
            self.cb_fps.setEnabled(False)
            self.lbl_status.setText(f"총 {len(self.videos)}개 영상 대기 중 (무손실 1초 병합 모드)")
            total_mb = sum(v['size_mb'] for v in self.videos)
            self.lbl_size_est.setText(f"예상 용량: 약 {int(total_mb)} MB (무손실 그대로)")
            self.lbl_size_est.setStyleSheet("color: #00aa00; font-weight: bold;")

        if not self.videos:
            self.entry_out_dir.setText("")
            self.entry_out_name.setText("")

    def _run_ffmpeg_with_progress(self, cmd, signals, prefix="", current_offset_ms=0, total_ms=0):
        process = subprocess.Popen(cmd, shell=True, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace', creationflags=0x08000000)
        
        with self.proc_lock:
            self.active_processes.append(process)
            
        for line in iter(process.stderr.readline, ''):
            if self.cancel_requested:
                break
            m = re.search(r'time=(\d{2,3}):(\d{2}):(\d{2}\.\d+)', line)
            if m:
                h, m_s, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
                ms = int((h * 3600 + m_s * 60 + s) * 1000)
                
                pct = 0
                if total_ms > 0:
                    pct = int(((current_offset_ms + ms) / total_ms) * 100)
                    pct = min(100, max(0, pct))
                
                time_str = f"{h:02d}:{m_s:02d}:{int(s):02d}"
                signals.progress.emit(pct, f"{prefix}{time_str}")
                
        process.stderr.close()
        process.wait()
        
        with self.proc_lock:
            if process in self.active_processes:
                self.active_processes.remove(process)
                
        if self.cancel_requested:
            raise Exception("작업이 취소되었습니다.")
        if process.returncode != 0:
            raise Exception("FFmpeg 처리 중 오류 발생")

    def toggle_export(self):
        if self.is_merging:
            self.cancel_export()
        else:
            self.execute_export()

    def cancel_export(self):
        self.cancel_requested = True
        self.btn_export.setEnabled(False)
        self.btn_export.setText("취소 처리 중...")
        self.btn_export.setStyleSheet("")
        
        with self.proc_lock:
            for p in self.active_processes:
                try:
                    subprocess.run(f'taskkill /F /T /PID {p.pid}', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
                except Exception:
                    pass

    def execute_export(self):
        if len(self.videos) < 2:
            QMessageBox.warning(self, "경고", "병합할 영상이 2개 이상 필요합니다.")
            return

        out_dir = self.entry_out_dir.text().strip()
        if not out_dir:
            out_dir = os.path.dirname(self.videos[0]['path'])
            
        custom_name = self.entry_out_name.text().strip()
        base_name = custom_name if custom_name else f"{os.path.splitext(os.path.basename(self.videos[0]['path']))[0]}_merged"
        
        ext = os.path.splitext(self.videos[0]['path'])[1]
        if self.needs_reencoding:
            ext = ".mp4"
            
        out_path = os.path.join(out_dir, f"{base_name}{ext}")

        self.is_merging = True
        self.cancel_requested = False
        self.btn_export.setText("병합 시작 준비 중... ❌ 클릭 시 취소")
        self.btn_export.setStyleSheet("background-color: #8b0000; color: white; font-weight: bold;")
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        
        target_res = self.cb_resolution.currentText()
        target_fps = self.cb_fps.currentText()
        
        self.merge_signals = ExportSignals()
        self.merge_signals.progress.connect(self.on_merge_progress)
        self.merge_signals.finished.connect(self.on_merge_finished)
        
        threading.Thread(target=self._merge_task, args=(out_path, target_res, target_fps, self.merge_signals), daemon=True).start()

    def on_merge_progress(self, pct, text):
        if self.cancel_requested: return
        self.btn_export.setText(f"병합 진행 중... ({pct}%) - {text} ❌ 클릭 시 취소")
        if pct >= 0:
            self.progress_bar.setValue(pct)

    def on_merge_finished(self, success, msg):
        self.is_merging = False
        self.cancel_requested = False
        self.progress_bar.setVisible(False)
        self.btn_export.setEnabled(True)
        self.btn_export.setText("병합 실행")
        self.btn_export.setStyleSheet("")
        
        if self.cancel_requested:
            QMessageBox.information(self, "취소됨", "비디오 병합 작업이 취소되었습니다.")
        elif success:
            QMessageBox.information(self, "완료", "비디오 병합 작업이 성공적으로 완료되었습니다.")
        else:
            QMessageBox.warning(self, "오류", f"병합 중 오류가 발생했습니다:\n{msg}")

    def _merge_task(self, out_path, res_mode, fps_mode, signals):
        ffmpeg = self.main_window.downloader_tab.get_exe("ffmpeg.exe").strip('"')
        out_dir = os.path.dirname(out_path)
        os.makedirs(out_dir, exist_ok=True)
        temp_files = []

        try:
            total_dur = sum(v['duration'] for v in self.videos)
            processed_ms = 0

            if not self.needs_reencoding:
                signals.progress.emit(0, "무손실 초고속 병합 중...")
                merge_list = os.path.join(out_dir, "temp_merge_list.txt")
                with open(merge_list, "w", encoding="utf-8") as f:
                    for v in self.videos: f.write(f"file '{v['path']}'\n")
                cmd = f'"{ffmpeg}" -y -f concat -safe 0 -i "{merge_list}" -c copy "{out_path}"'
                self._run_ffmpeg_with_progress(cmd, signals, "", 0, total_dur)
                os.remove(merge_list)
            else:
                w_list = [v['width'] for v in self.videos]
                h_list = [v['height'] for v in self.videos]
                fps_list = [v['fps'] for v in self.videos]
                
                if "최고" in res_mode: W, H = max(w_list), max(h_list)
                elif "최저" in res_mode: W, H = min(w_list), min(h_list)
                elif "FHD" in res_mode: W, H = 1920, 1080
                else: W, H = 1280, 720
                
                if "최고" in fps_mode: FPS = max(fps_list)
                elif "최저" in fps_mode: FPS = min(fps_list)
                elif "60" in fps_mode: FPS = 60
                else: FPS = 30
                
                for idx, v in enumerate(self.videos):
                    temp_out = os.path.join(out_dir, f"temp_merge_{idx}.mp4")
                    temp_files.append(temp_out)
                    vf = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2"
                    cmd = f'"{ffmpeg}" -y -i "{v["path"]}" -vf "{vf}" -r {FPS} -c:v libx264 -preset fast -crf 18 -c:a copy "{temp_out}"'
                    
                    prefix = f"[{idx+1}/{len(self.videos)} 파일] "
                    self._run_ffmpeg_with_progress(cmd, signals, prefix, processed_ms, total_dur)
                    processed_ms += v['duration']
                
                signals.progress.emit(99, "최종 파일 병합 중...")
                merge_list = os.path.join(out_dir, "temp_merge_list.txt")
                with open(merge_list, "w", encoding="utf-8") as f:
                    for tf in temp_files: f.write(f"file '{tf}'\n")
                cmd = f'"{ffmpeg}" -y -f concat -safe 0 -i "{merge_list}" -c copy "{out_path}"'
                self._run_ffmpeg_with_progress(cmd, signals, "", 0, 0)
                
                os.remove(merge_list)
                for tf in temp_files: os.remove(tf)

            signals.finished.emit(True, "완료")
        except Exception as e:
            signals.finished.emit(False, str(e))


# ==========================================
# 6. 탭 4: 음원 일괄 추출기
# ==========================================
class ExtractorTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.is_extracting = False
        self.cancel_requested = False
        self.active_processes = []
        self.proc_lock = threading.Lock()
        self.stats_lock = threading.Lock()
        
        self.total_tasks = 0
        self.task_progress = {}
        self.task_stats = {'success': 0, 'error': 0, 'canceled': 0}
        
        self.setAcceptDrops(True)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        top_layout = QHBoxLayout()
        self.lbl_status = QLabel("음원으로 추출할 영상들을 차례대로 추가하세요.")
        btn_add = QPushButton("영상 추가...")
        btn_add.clicked.connect(self.add_files_dialog)
        top_layout.addWidget(self.lbl_status, stretch=1)
        top_layout.addWidget(btn_add)
        layout.addLayout(top_layout)

        self.lbl_drop = QLabel("📂 이 곳에 영상 파일들을 드래그 앤 드롭하세요")
        self.lbl_drop.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_drop.setStyleSheet("background-color: #1e1e1e; color: #aaaaaa; font-size: 18px; border: 2px dashed #555555; padding: 30px;")
        layout.addWidget(self.lbl_drop)

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget, stretch=1)

        btn_del = QPushButton("선택 항목 삭제")
        btn_del.clicked.connect(self.remove_selected)
        layout.addWidget(btn_del)

        export_setting_layout = QHBoxLayout()
        self.entry_out_dir = QLineEdit()
        self.entry_out_dir.setText(Config.DEFAULT_PATHS["음원 저장 폴더"])
        self.btn_out_dir = QPushButton("저장 폴더 변경")
        self.btn_out_dir.clicked.connect(self.change_out_dir)

        self.cb_ext_audio = QComboBox()
        self.cb_ext_audio.addItems(["추천", "mp3", "m4a", "aac", "opus"])

        export_setting_layout.addWidget(QLabel("저장 위치:"))
        export_setting_layout.addWidget(self.entry_out_dir, stretch=2)
        export_setting_layout.addWidget(self.btn_out_dir)
        export_setting_layout.addSpacing(10)
        export_setting_layout.addWidget(QLabel("저장 형식:"))
        export_setting_layout.addWidget(self.cb_ext_audio)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar { border: 1px solid #555; border-radius: 5px; text-align: center; height: 20px; }
            QProgressBar::chunk { background-color: #00aa00; width: 10px; }
        """)

        self.btn_export = QPushButton("음원 일괄 추출 실행")
        self.btn_export.setMinimumHeight(40)
        self.btn_export.clicked.connect(self.toggle_export)
        
        bottom_layout = QVBoxLayout()
        bottom_layout.addLayout(export_setting_layout)
        bottom_layout.addWidget(self.progress_bar)
        bottom_layout.addWidget(self.btn_export)
        layout.addLayout(bottom_layout)

        self.setLayout(layout)

    def change_out_dir(self):
        default_dir = self.entry_out_dir.text() or Config.DEFAULT_PATHS["음원 저장 폴더"]
        path = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", default_dir)
        if path:
            self.entry_out_dir.setText(os.path.normpath(path))

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls(): event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls: 
            paths = [u.toLocalFile() for u in urls if u.toLocalFile()]
            if paths:
                event.acceptProposedAction()
                QTimer.singleShot(10, lambda p=paths: self.process_dropped_files(p))

    def add_files_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "추출할 영상 선택", Config.DEFAULT_PATHS["영상 저장 폴더"])
        if paths: self.process_dropped_files(paths)

    def process_dropped_files(self, paths):
        for path in paths:
            item = QListWidgetItem(f"[대기 중] {os.path.basename(path)}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.list_widget.addItem(item)
        self.lbl_status.setText(f"총 {self.list_widget.count()}개의 영상 대기 중")

    def remove_selected(self):
        for item in self.list_widget.selectedItems():
            self.list_widget.takeItem(self.list_widget.row(item))
        self.lbl_status.setText(f"총 {self.list_widget.count()}개의 영상 대기 중")

    def toggle_export(self):
        if self.is_extracting:
            self.cancel_export()
        else:
            self.execute_export()

    def cancel_export(self):
        self.cancel_requested = True
        self.btn_export.setEnabled(False)
        self.btn_export.setText("취소 처리 중...")
        self.btn_export.setStyleSheet("")
        
        with self.proc_lock:
            for p in self.active_processes:
                try:
                    subprocess.run(f'taskkill /F /T /PID {p.pid}', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
                except Exception:
                    pass

    def execute_export(self):
        files = [self.list_widget.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list_widget.count())]
        if not files:
            QMessageBox.warning(self, "경고", "추출할 영상이 없습니다.")
            return

        self.is_extracting = True
        self.cancel_requested = False
        with self.stats_lock:
            self.task_stats = {'success': 0, 'error': 0, 'canceled': 0}
            
        self.btn_export.setText("음원 일괄 추출 준비 중... ❌ 클릭 시 취소")
        self.btn_export.setStyleSheet("background-color: #8b0000; color: white; font-weight: bold;")
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        
        out_dir = self.entry_out_dir.text().strip()
        aud_ext = self.cb_ext_audio.currentText()
        
        self.ext_signals = ExtractorSignals()
        self.ext_signals.item_status.connect(self.on_item_status)
        self.ext_signals.progress.connect(self.on_progress)
        self.ext_signals.total_start.connect(self.on_total_start)
        self.ext_signals.finished.connect(self.on_finished)
        
        threading.Thread(target=self.extractor_manager, args=(files, out_dir, aud_ext, self.ext_signals), daemon=True).start()

    def on_total_start(self, total):
        self.total_tasks = total
        self.task_progress = {i: 0.0 for i in range(total)}
        
    def on_item_status(self, idx, status):
        item = self.list_widget.item(idx)
        if item:
            path = item.data(Qt.ItemDataRole.UserRole)
            item.setText(f"[{status}] {os.path.basename(path)}")

    def on_progress(self, task_idx, pct):
        if self.cancel_requested: return
        self.task_progress[task_idx] = pct
        if self.total_tasks > 0:
            avg = sum(self.task_progress.values()) / self.total_tasks
            self.progress_bar.setValue(int(avg))
            self.btn_export.setText(f"음원 일괄 추출 진행 중... ({int(avg)}%) ❌ 클릭 시 취소")

    def on_finished(self, success, error, canceled):
        self.is_extracting = False
        self.cancel_requested = False
        self.progress_bar.setVisible(False)
        self.btn_export.setEnabled(True)
        self.btn_export.setText("음원 일괄 추출 실행")
        self.btn_export.setStyleSheet("")
        
        if self.total_tasks > 0:
            if canceled > 0 and success == 0 and error == 0:
                QMessageBox.information(self, "취소됨", "음원 일괄 추출 작업이 취소되었습니다.")
            elif error > 0:
                QMessageBox.warning(self, "완료 (오류 발생)", f"총 {success + error}개 작업 중\n{success}개 성공, {error}개 실패했습니다.")
            else:
                QMessageBox.information(self, "완료", f"총 {success}개의 음원 추출 작업이 성공적으로 완료되었습니다.")

    def extractor_manager(self, files, out_dir, aud_ext, signals):
        signals.total_start.emit(len(files))
        os.makedirs(out_dir, exist_ok=True)
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(self.extract_single, path, i, out_dir, aud_ext, signals) for i, path in enumerate(files)]
            concurrent.futures.wait(futures)
            
        signals.finished.emit(self.task_stats['success'], self.task_stats['error'], self.task_stats['canceled'])

    def extract_single(self, path, task_idx, out_dir, aud_ext, signals):
        if self.cancel_requested:
            with self.stats_lock: self.task_stats['canceled'] += 1
            signals.item_status.emit(task_idx, "취소됨")
            return
            
        signals.item_status.emit(task_idx, "추출 중...")
        
        ffmpeg = self.main_window.downloader_tab.get_exe("ffmpeg.exe").strip('"')
        ffprobe = self.main_window.downloader_tab.get_exe("ffprobe.exe").strip('"')
        
        try:
            cmd_probe_dur = f'"{ffprobe}" -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{path}"'
            proc_dur = subprocess.run(cmd_probe_dur, shell=True, capture_output=True, text=True, creationflags=0x08000000)
            try: dur_sec = float(proc_dur.stdout.strip())
            except: dur_sec = 0
            
            cmd_probe_codec = f'"{ffprobe}" -v error -select_streams a:0 -show_entries stream=codec_name -of default=nw=1:nk=1 "{path}"'
            proc_codec = subprocess.run(cmd_probe_codec, shell=True, capture_output=True, text=True, creationflags=0x08000000)
            orig_codec = proc_codec.stdout.strip().lower()
            
            out_ext = aud_ext
            if aud_ext == "추천":
                if orig_codec == "opus": out_ext = "opus"
                elif orig_codec == "mp3": out_ext = "mp3"
                else: out_ext = "m4a"
                
            name = os.path.splitext(os.path.basename(path))[0]
            out_path = os.path.join(out_dir, f"{name}.{out_ext}")
            
            is_compat = (aud_ext == "추천") or (out_ext == "opus" and orig_codec == "opus") or (out_ext == "mp3" and orig_codec == "mp3") or (out_ext == "m4a" and orig_codec == "aac")
            
            if is_compat: cmd = f'"{ffmpeg}" -y -i "{path}" -vn -c:a copy "{out_path}"'
            else: cmd = f'"{ffmpeg}" -y -i "{path}" -vn -b:a 192k "{out_path}"'
            
            process = subprocess.Popen(cmd, shell=True, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace', creationflags=0x08000000)
            
            with self.proc_lock:
                self.active_processes.append(process)
                
            for line in iter(process.stderr.readline, ''):
                if self.cancel_requested: break
                m = re.search(r'time=(\d{2,3}):(\d{2}):(\d{2}\.\d+)', line)
                if m and dur_sec > 0:
                    h, m_s, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
                    cur_sec = h * 3600 + m_s * 60 + s
                    pct = (cur_sec / dur_sec) * 100
                    signals.progress.emit(task_idx, min(100.0, pct))
                    
            process.stderr.close()
            process.wait()
            
            with self.proc_lock:
                if process in self.active_processes:
                    self.active_processes.remove(process)
                    
            if self.cancel_requested:
                with self.stats_lock: self.task_stats['canceled'] += 1
                signals.item_status.emit(task_idx, "취소됨")
                return
                
            if process.returncode == 0:
                with self.stats_lock: self.task_stats['success'] += 1
                signals.progress.emit(task_idx, 100.0)
                signals.item_status.emit(task_idx, "완료")
            else:
                with self.stats_lock: self.task_stats['error'] += 1
                signals.item_status.emit(task_idx, "오류")
                
        except Exception as e:
            with self.stats_lock: self.task_stats['error'] += 1
            signals.item_status.emit(task_idx, "오류")


# ==========================================
# 7. 탭 5: 음원 태그 편집기
# ==========================================
class AlbumArtLabel(QLabel):
    file_dropped = pyqtSignal(str)
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("border: 2px dashed #aaaaaa; background-color: #222222; color: #888888;")
        self.setText("앨범아트(jpg, png)\n드래그 & 드롭")
        self.setFixedSize(200, 200)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path.lower().endswith(('.png', '.jpg', '.jpeg')):
                event.acceptProposedAction()
                self.file_dropped.emit(path)

class TagEditorTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.current_art_path = None
        self.history = {'artist': set(), 'album_artist': set(), 'album': set(), 'genre': set()}
        self.init_ui()

    def init_ui(self):
        main_layout = QHBoxLayout()

        left_layout = QVBoxLayout()
        self.list_files = QListWidget()
        self.list_files.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_files.setAcceptDrops(True)
        self.list_files.itemSelectionChanged.connect(self.on_file_selected)
        
        btn_add = QPushButton("음원 추가...")
        btn_add.clicked.connect(self.add_files)
        btn_del = QPushButton("목록에서 제거")
        btn_del.clicked.connect(self.remove_files)
        
        btn_layout = QHBoxLayout()
        btn_layout.addWidget(btn_add)
        btn_layout.addWidget(btn_del)
        
        left_layout.addWidget(QLabel("📝 편집할 음원 (드래그 앤 드롭 지원)"))
        left_layout.addWidget(self.list_files)
        left_layout.addLayout(btn_layout)
        main_layout.addLayout(left_layout, stretch=1)

        right_layout = QVBoxLayout()
        if not MUTAGEN_AVAILABLE:
            right_layout.addWidget(QLabel("⚠️ 'mutagen' 라이브러리가 설치되지 않아 태그 편집기를 사용할 수 없습니다.\ncmd 창에서 'pip install mutagen'을 실행해주세요."))
            main_layout.addLayout(right_layout, stretch=2)
            self.setLayout(main_layout)
            return

        form_group = QGroupBox("메타데이터 정보 (선택된 파일에 반영됩니다)")
        grid = QGridLayout()
        
        self.fields = {}
        row = 0
        def add_field(key, label_text, is_combo=False):
            nonlocal row
            grid.addWidget(QLabel(label_text), row, 0)
            if is_combo:
                widget = QComboBox()
                widget.setEditable(True)
            else:
                widget = QLineEdit()
            self.fields[key] = widget
            grid.addWidget(widget, row, 1)
            
            btn_batch = QPushButton("선택 곡 일괄 적용")
            btn_batch.setToolTip("목록에서 다중 선택된 모든 곡에 이 값을 덮어씌웁니다.")
            btn_batch.clicked.connect(lambda chk, k=key: self.batch_apply_field(k))
            grid.addWidget(btn_batch, row, 2)
            row += 1

        add_field('title', "1. 제목:", False)
        add_field('artist', "2. 참여 음악가:", True)
        add_field('album_artist', "3. 앨범 음악가:", True)
        add_field('album', "4. 앨범명:", True)
        add_field('genre', "5. 장르:", True)
        add_field('date', "6. 발매일 (yyyyMMdd):", False)
        add_field('track', "7. 곡 번호 (#):", False)
        add_field('disc', "8. 디스크 번호:", False)

        art_layout = QVBoxLayout()
        self.lbl_art = AlbumArtLabel()
        self.lbl_art.file_dropped.connect(self.load_album_art)
        
        btn_batch_art = QPushButton("앨범아트 일괄 적용")
        btn_batch_art.clicked.connect(lambda: self.batch_apply_field('art'))
        
        art_layout.addWidget(self.lbl_art)
        art_layout.addWidget(btn_batch_art)
        art_layout.addStretch()
        grid.addLayout(art_layout, 0, 3, row, 1, Qt.AlignmentFlag.AlignTop)

        form_group.setLayout(grid)
        right_layout.addWidget(form_group)

        parse_group = QGroupBox("파일명으로 정보 자동 추출 (- 기호 기준)")
        parse_layout = QHBoxLayout()
        
        self.format_list = QListWidget()
        self.format_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.format_list.setFixedHeight(100)
        for text in ['음악가', '앨범명', '곡번호', '노래제목']:
            item = QListWidgetItem(text)
            item.setCheckState(Qt.CheckState.Checked)
            self.format_list.addItem(item)
            
        parse_layout.addWidget(QLabel("적용할 형식 순서\n(드래그로 순서 변경)"), stretch=1)
        parse_layout.addWidget(self.format_list, stretch=3)
        
        btn_parse = QPushButton("선택된 파일에 자동 추출 실행")
        btn_parse.setMinimumHeight(60)
        btn_parse.clicked.connect(self.auto_parse_filenames)
        parse_layout.addWidget(btn_parse, stretch=2)
        
        parse_group.setLayout(parse_layout)
        right_layout.addWidget(parse_group)

        self.btn_save = QPushButton("현재 폼에 적힌 정보를 파일에 저장")
        self.btn_save.setMinimumHeight(50)
        self.btn_save.clicked.connect(self.save_current_tags)
        right_layout.addWidget(self.btn_save)

        main_layout.addLayout(right_layout, stretch=2)
        self.setLayout(main_layout)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls(): event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            paths = [u.toLocalFile() for u in urls if u.toLocalFile()]
            self.add_files_to_list(paths)

    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "음원 파일 선택", Config.DEFAULT_PATHS["음원 저장 폴더"], "Audio Files (*.mp3 *.m4a *.aac *.opus)")
        if paths: self.add_files_to_list(paths)

    def add_files_to_list(self, paths):
        for p in paths:
            if p.lower().endswith(('.mp3', '.m4a', '.aac', '.opus')):
                item = QListWidgetItem(os.path.basename(p))
                item.setData(Qt.ItemDataRole.UserRole, p)
                self.list_files.addItem(item)

    def remove_files(self):
        for item in self.list_files.selectedItems():
            self.list_files.takeItem(self.list_files.row(item))

    def on_file_selected(self):
        items = self.list_files.selectedItems()
        if not items:
            self.clear_form()
            return
            
        path = items[0].data(Qt.ItemDataRole.UserRole)
        data = self._read_tags(path)
        
        if len(items) > 1:
            for k in self.fields.keys():
                self.fields[k].setText("")
                if isinstance(self.fields[k], QComboBox):
                    self.fields[k].setPlaceholderText("<다중 선택됨>")
            self.lbl_art.setText("다중 선택됨")
            self.lbl_art.setPixmap(QPixmap())
        else:
            self.fields['title'].setText(data.get('title', ''))
            self.fields['artist'].setCurrentText(data.get('artist', ''))
            self.fields['album_artist'].setCurrentText(data.get('album_artist', ''))
            self.fields['album'].setCurrentText(data.get('album', ''))
            self.fields['genre'].setCurrentText(data.get('genre', ''))
            self.fields['date'].setText(data.get('date', ''))
            self.fields['track'].setText(data.get('track', ''))
            self.fields['disc'].setText(data.get('disc', ''))
            
            self.current_art_path = None
            if data.get('art_data'):
                pixmap = QPixmap()
                pixmap.loadFromData(data['art_data'])
                self.lbl_art.setPixmap(pixmap.scaled(200, 200, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            else:
                self.lbl_art.clear()
                self.lbl_art.setText("앨범아트 없음\n(드래그 & 드롭)")

    def clear_form(self):
        for k, v in self.fields.items():
            v.clear()
        self.lbl_art.clear()
        self.lbl_art.setText("앨범아트(jpg, png)\n드래그 & 드롭")
        self.current_art_path = None

    def load_album_art(self, path):
        self.current_art_path = path
        pixmap = QPixmap(path)
        self.lbl_art.setPixmap(pixmap.scaled(200, 200, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def update_history(self, key, value):
        if not value: return
        if value not in self.history[key]:
            self.history[key].add(value)
            self.fields[key].addItem(value)

    def get_form_data(self):
        return {
            'title': self.fields['title'].text().strip(),
            'artist': self.fields['artist'].currentText().strip(),
            'album_artist': self.fields['album_artist'].currentText().strip(),
            'album': self.fields['album'].currentText().strip(),
            'genre': self.fields['genre'].currentText().strip(),
            'date': self.fields['date'].text().strip(),
            'track': self.fields['track'].text().strip(),
            'disc': self.fields['disc'].text().strip(),
            'art_path': self.current_art_path
        }

    def save_current_tags(self):
        items = self.list_files.selectedItems()
        if not items: return
        data = self.get_form_data()
        
        self.update_history('artist', data['artist'])
        self.update_history('album_artist', data['album_artist'])
        self.update_history('album', data['album'])
        self.update_history('genre', data['genre'])

        count = 0
        for item in items:
            path = item.data(Qt.ItemDataRole.UserRole)
            if self._write_tags(path, data):
                count += 1
                
        QMessageBox.information(self, "완료", f"{count}개의 파일에 태그를 저장했습니다.")

    def batch_apply_field(self, field_key):
        items = self.list_files.selectedItems()
        if not items: return
        
        data_to_apply = {}
        if field_key == 'art':
            data_to_apply['art_path'] = self.current_art_path
            msg = "현재 앨범아트를"
        else:
            val = self.fields[field_key].text().strip() if isinstance(self.fields[field_key], QLineEdit) else self.fields[field_key].currentText().strip()
            data_to_apply[field_key] = val
            msg = f"[{val}] 값을"
            if field_key in self.history: self.update_history(field_key, val)

        if QMessageBox.question(self, "일괄 적용", f"{msg} 선택된 {len(items)}개 파일에 일괄 덮어쓰시겠습니까?") == QMessageBox.StandardButton.Yes:
            for item in items:
                path = item.data(Qt.ItemDataRole.UserRole)
                self._write_tags(path, data_to_apply, merge=True)
            QMessageBox.information(self, "완료", "일괄 적용 완료!")

    def auto_parse_filenames(self):
        items = self.list_files.selectedItems()
        if not items: return
        
        format_keys = []
        for i in range(self.format_list.count()):
            item = self.format_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                format_keys.append(item.text())

        count = 0
        for item in items:
            path = item.data(Qt.ItemDataRole.UserRole)
            fname = os.path.splitext(os.path.basename(path))[0]
            parts = [p.strip() for p in fname.split('-')]
            
            data = {}
            for i, val in enumerate(parts):
                if i < len(format_keys):
                    k = format_keys[i]
                    if k == '음악가': data['artist'] = val
                    elif k == '앨범명': data['album'] = val
                    elif k == '곡번호': data['track'] = val
                    elif k == '노래제목': data['title'] = val
            
            if data:
                self._write_tags(path, data, merge=True)
                count += 1
                for key in ['artist', 'album']:
                    if key in data: self.update_history(key, data[key])
                    
        self.on_file_selected() 
        QMessageBox.information(self, "완료", f"{count}개의 파일에 자동 추출 태그를 적용했습니다.")

    def _read_tags(self, path):
        res = {}
        try:
            audio = mutagen.File(path)
            if audio is None or audio.tags is None: return res
            ext = os.path.splitext(path)[1].lower()

            if ext == '.mp3':
                tags = audio.tags
                if 'TIT2' in tags: res['title'] = str(tags['TIT2'])
                if 'TPE1' in tags: res['artist'] = str(tags['TPE1'])
                if 'TPE2' in tags: res['album_artist'] = str(tags['TPE2'])
                if 'TALB' in tags: res['album'] = str(tags['TALB'])
                if 'TCON' in tags: res['genre'] = str(tags['TCON'])
                if 'TDRC' in tags: res['date'] = str(tags['TDRC'])
                if 'TRCK' in tags: res['track'] = str(tags['TRCK'])
                if 'TPOS' in tags: res['disc'] = str(tags['TPOS'])
                apic = tags.getall('APIC')
                if apic: res['art_data'] = apic[0].data

            elif ext in ['.m4a', '.aac']:
                tags = audio.tags
                if '\xa9nam' in tags: res['title'] = tags['\xa9nam'][0]
                if '\xa9ART' in tags: res['artist'] = tags['\xa9ART'][0]
                if 'aART' in tags: res['album_artist'] = tags['aART'][0]
                if '\xa9alb' in tags: res['album'] = tags['\xa9alb'][0]
                if '\xa9gen' in tags: res['genre'] = tags['\xa9gen'][0]
                if '\xa9day' in tags: res['date'] = tags['\xa9day'][0]
                if 'trkn' in tags and tags['trkn']: res['track'] = str(tags['trkn'][0][0])
                if 'disk' in tags and tags['disk']: res['disc'] = str(tags['disk'][0][0])
                if 'covr' in tags and tags['covr']: res['art_data'] = tags['covr'][0]

            elif ext == '.opus':
                tags = audio.tags
                if 'TITLE' in tags: res['title'] = tags['TITLE'][0]
                if 'ARTIST' in tags: res['artist'] = tags['ARTIST'][0]
                if 'ALBUMARTIST' in tags: res['album_artist'] = tags['ALBUMARTIST'][0]
                if 'ALBUM' in tags: res['album'] = tags['ALBUM'][0]
                if 'GENRE' in tags: res['genre'] = tags['GENRE'][0]
                if 'DATE' in tags: res['date'] = tags['DATE'][0]
                if 'TRACKNUMBER' in tags: res['track'] = tags['TRACKNUMBER'][0]
                if 'DISCNUMBER' in tags: res['disc'] = tags['DISCNUMBER'][0]
                if 'metadata_block_picture' in tags:
                    for b64_data in tags['metadata_block_picture']:
                        try:
                            p = Picture(base64.b64decode(b64_data))
                            res['art_data'] = p.data
                            break
                        except: pass
        except Exception as e: print("Tag Read Error:", e)
        return res

    def _write_tags(self, path, data, merge=False):
        try:
            audio = mutagen.File(path, easy=False)
            if audio is None: return False
            if audio.tags is None: audio.add_tags()
            tags = audio.tags
            ext = os.path.splitext(path)[1].lower()

            art_data, mime = None, None
            if 'art_path' in data and data['art_path'] and os.path.exists(data['art_path']):
                with open(data['art_path'], 'rb') as f: art_data = f.read()
                mime = 'image/jpeg' if data['art_path'].lower().endswith(('.jpg', '.jpeg')) else 'image/png'

            if ext == '.mp3':
                if 'title' in data and data['title']: tags.add(TIT2(encoding=3, text=data['title']))
                if 'artist' in data and data['artist']: tags.add(TPE1(encoding=3, text=data['artist']))
                if 'album_artist' in data and data['album_artist']: tags.add(TPE2(encoding=3, text=data['album_artist']))
                if 'album' in data and data['album']: tags.add(TALB(encoding=3, text=data['album']))
                if 'genre' in data and data['genre']: tags.add(TCON(encoding=3, text=data['genre']))
                if 'date' in data and data['date']: tags.add(TDRC(encoding=3, text=data['date']))
                if 'track' in data and data['track']: tags.add(TRCK(encoding=3, text=data['track']))
                if 'disc' in data and data['disc']: tags.add(TPOS(encoding=3, text=data['disc']))
                if art_data: tags.add(APIC(encoding=3, mime=mime, type=3, desc=u'Cover', data=art_data))
                
            elif ext in ['.m4a', '.aac']:
                if 'title' in data and data['title']: tags['\xa9nam'] = data['title']
                if 'artist' in data and data['artist']: tags['\xa9ART'] = data['artist']
                if 'album_artist' in data and data['album_artist']: tags['aART'] = data['album_artist']
                if 'album' in data and data['album']: tags['\xa9alb'] = data['album']
                if 'genre' in data and data['genre']: tags['\xa9gen'] = data['genre']
                if 'date' in data and data['date']: tags['\xa9day'] = data['date']
                if 'track' in data and data['track']:
                    try: tags['trkn'] = [(int(data['track'].split('/')[0]), 0)]
                    except: pass
                if 'disc' in data and data['disc']:
                    try: tags['disk'] = [(int(data['disc'].split('/')[0]), 0)]
                    except: pass
                if art_data:
                    fmt = mutagen.mp4.MP4Cover.FORMAT_JPEG if mime == 'image/jpeg' else mutagen.mp4.MP4Cover.FORMAT_PNG
                    tags['covr'] = [mutagen.mp4.MP4Cover(art_data, imageformat=fmt)]

            elif ext == '.opus':
                if 'title' in data and data['title']: tags['TITLE'] = data['title']
                if 'artist' in data and data['artist']: tags['ARTIST'] = data['artist']
                if 'album_artist' in data and data['album_artist']: tags['ALBUMARTIST'] = data['album_artist']
                if 'album' in data and data['album']: tags['ALBUM'] = data['album']
                if 'genre' in data and data['genre']: tags['GENRE'] = data['genre']
                if 'date' in data and data['date']: tags['DATE'] = data['date']
                if 'track' in data and data['track']: tags['TRACKNUMBER'] = data['track']
                if 'disc' in data and data['disc']: tags['DISCNUMBER'] = data['disc']
                if art_data:
                    p = Picture()
                    p.type = 3
                    p.mime = mime
                    p.desc = 'Cover'
                    p.data = art_data
                    tags['metadata_block_picture'] = [base64.b64encode(p.write()).decode('ascii')]
            audio.save()
            return True
        except Exception as e:
            print("Tag Write Error:", e)
            return False

# ==========================================
# 8. 메인 윈도우 (탭 관리자)
# ==========================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("yt-dlp 미디어 통합 매니저 v1.3.0")
        self.resize(1000, 800)
        
        screen_geo = QApplication.primaryScreen().availableGeometry()
        self.move((screen_geo.width() - self.width()) // 2, (screen_geo.height() - self.height()) // 2)
        
        Config.init_folders() 

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.downloader_tab = DownloaderTab()
        self.editor_tab = EditorTab(self)
        self.merger_tab = MergerTab(self)
        self.extractor_tab = ExtractorTab(self) 
        self.tag_tab = TagEditorTab(self)

        self.tabs.addTab(self.downloader_tab, "⬇️ 다운로더")
        self.tabs.addTab(self.editor_tab, "✂️ 미디어 편집기")
        self.tabs.addTab(self.merger_tab, "🔗 비디오 병합기")
        self.tabs.addTab(self.extractor_tab, "🎙️ 음원 일괄 추출기") 
        self.tabs.addTab(self.tag_tab, "🎵 음원 태그 편집기")
        
        self.statusBar().showMessage("준비 완료")

    def closeEvent(self, event):
        active_tasks = []
        if self.downloader_tab.is_downloading: active_tasks.append("다운로드")
        if self.editor_tab.is_exporting: active_tasks.append("미디어 편집")
        if self.merger_tab.is_merging: active_tasks.append("비디오 병합")
        if self.extractor_tab.is_extracting: active_tasks.append("음원 추출")
        
        if active_tasks:
            task_names = ", ".join(active_tasks)
            reply = QMessageBox.question(
                self, '프로그램 종료 경고',
                f'현재 백그라운드에서 [{task_names}] 작업이 진행 중입니다.\n\n작업을 강제로 중단하고 프로그램을 종료하시겠습니까?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                if self.downloader_tab.is_downloading: self.downloader_tab.cancel_download()
                if self.editor_tab.is_exporting: self.editor_tab.cancel_export()
                if self.merger_tab.is_merging: self.merger_tab.cancel_export()
                if self.extractor_tab.is_extracting: self.extractor_tab.cancel_export()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

def resource_path(relative_path):
    try: base_path = sys._MEIPASS
    except Exception: base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(resource_path("ytdlp_studio.ico")))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())