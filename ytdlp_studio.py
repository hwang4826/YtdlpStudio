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

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QTabWidget, QLabel, QLineEdit, 
                             QTextEdit, QPushButton, QCheckBox, QComboBox, 
                             QFileDialog, QMessageBox, QListWidget, QSlider, QGroupBox, QMenu,
                             QListWidgetItem, QStackedWidget, QToolTip)
from PyQt6.QtCore import Qt, QUrl, pyqtSignal, QObject, QTime, QRect
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget
# PyQt6.QtGui 임포트 줄을 찾아서 맨 끝에 QIcon을 추가합니다.
from PyQt6.QtGui import QKeySequence, QShortcut, QPainter, QColor, QDragEnterEvent, QDropEvent, QIcon

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
    finished = pyqtSignal()
    error = pyqtSignal(str)

# ==========================================
# 3. 탭 1: 미디어 다운로더
# ==========================================
class DownloaderTab(QWidget):
    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        url_group = QGroupBox("다운로드 주소 입력 (엔터 또는 쉼표로 다중 입력)")
        url_layout = QVBoxLayout()
        self.url_text = QTextEdit()
        self.url_text.setFixedHeight(80)
        url_layout.addWidget(self.url_text)
        url_group.setLayout(url_layout)
        layout.addWidget(url_group)

        path_group = QGroupBox("설정 및 경로 (비워두면 시스템 기본 폴더 자동 적용)")
        path_layout = QVBoxLayout()
        
        top_row_layout = QHBoxLayout()
        top_row_layout.addStretch() 
        
        self.btn_install_menu = QPushButton("🛠 필수 도구 설치")
        install_menu = QMenu()
        
        action_install_tools = install_menu.addAction("yt-dlp 및 FFmpeg(ffplay, ffprobe) 자동 설치")
        action_install_tools.triggered.connect(self.install_tools)
        
        action_install_node = install_menu.addAction("Node.js 공식 다운로드 페이지 열기")
        action_install_node.triggered.connect(lambda: webbrowser.open("https://nodejs.org/ko/download/"))
        
        self.btn_install_menu.setMenu(install_menu)
        top_row_layout.addWidget(self.btn_install_menu)
        path_layout.addLayout(top_row_layout)

        self.path_entries = {}
        
        for key, default_val in Config.DEFAULT_PATHS.items():
            row_layout = QHBoxLayout()
            row_layout.addWidget(QLabel(key))
            entry = QLineEdit()
            entry.setPlaceholderText(default_val)
            self.path_entries[key] = entry
            row_layout.addWidget(entry)
            
            btn_browse = QPushButton("찾아보기")
            btn_browse.clicked.connect(lambda checked, k=key: self.browse_path(k))
            row_layout.addWidget(btn_browse)
            path_layout.addLayout(row_layout)
            
        path_group.setLayout(path_layout)
        layout.addWidget(path_group)

        opt_group = QGroupBox("다운로드 옵션")
        opt_layout = QHBoxLayout()
        
        self.chk_video = QCheckBox("영상 다운로드")
        self.chk_video.setChecked(True)
        self.cb_vid_ext = QComboBox()
        self.cb_vid_ext.addItems(["추천", "mp4", "webm", "mkv", "ts"])
        
        self.chk_audio = QCheckBox("음원 다운로드 (추출)")
        self.cb_aud_ext = QComboBox()
        self.cb_aud_ext.addItems(["추천", "mp3", "m4a", "aac", "opus"])
        
        self.chk_cookie = QCheckBox("쿠키 사용")

        opt_layout.addWidget(self.chk_video)
        opt_layout.addWidget(QLabel("영상 확장자:"))
        opt_layout.addWidget(self.cb_vid_ext)
        opt_layout.addSpacing(20)
        opt_layout.addWidget(self.chk_audio)
        opt_layout.addWidget(QLabel("음원 확장자:"))
        opt_layout.addWidget(self.cb_aud_ext)
        opt_layout.addSpacing(20)
        opt_layout.addWidget(self.chk_cookie)
        opt_layout.addStretch()
        opt_group.setLayout(opt_layout)
        layout.addWidget(opt_group)

        btn_layout = QHBoxLayout()
        self.btn_download = QPushButton("다운로드 시작 (병렬 처리)")
        self.btn_download.setMinimumHeight(40)
        self.btn_download.clicked.connect(self.start_download)
        
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
        layout.addWidget(log_group)

        self.setLayout(layout)
        self.signals = WorkerSignals()
        self.signals.log_msg.connect(self.append_log)
        self.signals.finished.connect(self.download_finished)

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

    def run_cmd(self, cmd, prefix=""):
        process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for line in iter(process.stdout.readline, b''):
            try:
                decoded_line = line.decode('utf-8')
            except UnicodeDecodeError:
                decoded_line = line.decode('cp949', errors='replace')
            self.signals.log_msg.emit(f"{prefix}{decoded_line}")
        process.stdout.close()
        process.wait()
        return process.returncode

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

            self.signals.log_msg.emit("\n[시스템] BtbN 깃허브에서 FFmpeg 다운로드를 시작합니다.\n(약 100MB의 압축 파일을 받으므로 시간이 조금 걸립니다...)\n")
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
                            elif file_info.filename.endswith("ffplay.exe"):
                                with z.open(file_info) as zf, open(os.path.join(tool_dir, "ffplay.exe"), 'wb') as f:
                                    shutil.copyfileobj(zf, f)
                self.signals.log_msg.emit("[시스템] FFmpeg 및 FFprobe 설치 완료!\n")
            except Exception as e:
                self.signals.log_msg.emit(f"[시스템] ❌ FFmpeg 다운로드 실패: {e}\n")
            self.signals.log_msg.emit("\n[시스템] 🎉 모든 필수 도구 설치 작업이 끝났습니다!\n")
            
        threading.Thread(target=_task, daemon=True).start()

    def manual_update(self):
        self.btn_update.setEnabled(False)
        def _task():
            self.signals.log_msg.emit("\n[업데이트] yt-dlp 버전을 확인합니다...\n")
            self.run_cmd(f'{self.get_exe("yt-dlp.exe")} -U')
            self.signals.log_msg.emit("[업데이트] 완료.\n")
            self.btn_update.setEnabled(True)
        threading.Thread(target=_task, daemon=True).start()

    def start_download(self):
        urls = [u.strip() for u in re.split(r'[\n,]+', self.url_text.toPlainText()) if u.strip()]
        if not urls:
            QMessageBox.warning(self, "경고", "다운로드할 URL을 입력해주세요.")
            return

        self.btn_download.setEnabled(False)
        self.btn_download.setText("다운로드 진행 중...")
        threading.Thread(target=self.download_manager, args=(urls,), daemon=True).start()

    def download_manager(self, urls):
        self.signals.log_msg.emit(f"\n{'='*50}\n🚀 총 {len(urls)}개의 작업을 병렬로 시작합니다!\n{'='*50}\n")
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(self.download_task, url, idx + 1) for idx, url in enumerate(urls)]
            concurrent.futures.wait(futures)
        self.signals.log_msg.emit(f"\n{'='*50}\n🎉 모든 다운로드 작업이 완료되었습니다!\n{'='*50}\n")
        self.signals.finished.emit()

    def get_best_hls_format(self, yt_dlp, url, common_opts, prefix):
        self.signals.log_msg.emit(f"{prefix}🔍 최고 화질 탐색 중...\n")
        cmd = f'{yt_dlp} {common_opts} -F "{url}"'
        process = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
        
        best_id, best_height = None, -1
        for line in process.stdout.splitlines():
            line = line.strip()
            m = re.match(r'^(hls-[a-zA-Z0-9_-]+)', line)
            if not m: continue
            fmt_id = m.group(1)

            h = re.search(r'(\d{3,4})x(\d{3,4})', line)
            if h:
                height = int(h.group(2))
            else:
                height = 9999 if "original" in fmt_id.lower() else int(re.search(r'hls-(\d+)', fmt_id).group(1) if re.search(r'hls-(\d+)', fmt_id) else 0)

            if height > best_height:
                best_height = height
                best_id = fmt_id
        return best_id

    def get_audio_codec(self, filepath):
        ffprobe = self.get_exe("ffprobe.exe").strip('"')
        cmd = f'"{ffprobe}" -v error -select_streams a:0 -show_entries stream=codec_name -of default=nw=1:nk=1 "{filepath}"'
        try:
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, creationflags=0x08000000)
            return proc.stdout.strip().lower()
        except:
            return "aac"

    def download_task(self, url, task_id):
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
            
            common_opts = " ".join(opts)
            vid_ext = self.cb_vid_ext.currentText()
            ext_opt = f'--merge-output-format {vid_ext}' if vid_ext != "추천" else ""

            download_fmt = "best"
            url_lower = url.lower()
            if "youtube.com" in url_lower or "youtu.be" in url_lower:
                download_fmt = "bv*+ba/b"
            elif "soop" in url_lower or "afreecatv" in url_lower or "chzzk" in url_lower:
                best_hls = self.get_best_hls_format(yt_dlp, url, common_opts, prefix)
                if best_hls:
                    download_fmt = best_hls

            vid_path = os.path.join(self.get_setting("영상 저장 폴더"), "%(title)s.%(ext)s")
            cmd = f'{yt_dlp} {common_opts} {ext_opt} -f "{download_fmt}" -o "{vid_path}" "{url}"'
            
            self.signals.log_msg.emit(f"{prefix}📥 다운로드 시작...\n")
            self.run_cmd(cmd, prefix)
            
            if self.chk_audio.isChecked():
                self.extract_audio_task(vid_path, prefix)

            self.signals.log_msg.emit(f"{prefix}✨ 완료되었습니다!\n")
        except Exception as e:
            self.signals.log_msg.emit(f"{prefix}❌ 오류: {str(e)}\n")

    def extract_audio_task(self, template_path, prefix):
        base_dir = os.path.dirname(template_path)
        files = glob.glob(os.path.join(base_dir, "*"))
        if not files: return
        latest_file = max(files, key=os.path.getctime)
        
        aud_ext = self.cb_aud_ext.currentText()
        if aud_ext == "추천":
            codec = self.get_audio_codec(latest_file)
            if codec == "opus": out_ext = "opus"
            elif codec == "mp3": out_ext = "mp3"
            else: out_ext = "m4a"
        else:
            out_ext = aud_ext
            
        name = os.path.splitext(os.path.basename(latest_file))[0]
        output_file = os.path.join(self.get_setting("음원 저장 폴더"), f"{name}.{out_ext}")
        
        ffmpeg = self.get_exe("ffmpeg.exe")
        ffmpeg_cmd = f'{ffmpeg} -y -i "{latest_file}" -vn -b:a 192k "{output_file}"'
        
        self.signals.log_msg.emit(f"{prefix}🎵 음원 추출 진행 중...\n")
        self.run_cmd(ffmpeg_cmd, prefix)
        
        if not self.chk_video.isChecked():
            try: os.remove(latest_file)
            except: pass

    def download_finished(self):
        self.btn_download.setEnabled(True)
        self.btn_download.setText("다운로드 시작 (병렬 처리)")
        QMessageBox.information(self, "완료", "모든 작업이 완료되었습니다.")


# ==========================================
# 커스텀 위젯 (미리보기 드래그앤드롭 지원)
# ==========================================
class DropVideoWidget(QVideoWidget):
    file_dropped = pyqtSignal(str)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                self.file_dropped.emit(path)

# ==========================================
# 커스텀 슬라이더 (색상 렌더링, 텍스트 표시, 툴팁)
# ==========================================
class TimelineSlider(QSlider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setMouseTracking(True) 
        self.sections = [] 
        self.temp_section = None 
        self.duration_ms = 0
        self.player_ref = None

    def set_sections(self, sections_data):
        self.sections = sections_data
        self.update()

    def set_temp_section(self, section_data):
        self.temp_section = section_data
        self.update()

    def set_duration(self, duration):
        self.duration_ms = duration
        self.setRange(0, duration)

    def format_time(self, ms):
        return QTime(0, 0, 0).addMSecs(ms).toString("hh:mm:ss")

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
            
            s_text = self.format_time(start_ms)
            e_text = self.format_time(end_ms)
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
                QToolTip.showText(event.globalPosition().toPoint(), f"{self.format_time(s)} ~ {self.format_time(e)}", self)
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
            if self.player_ref:
                self.player_ref.setPosition(val)
        super().mousePressEvent(event)

# ==========================================
# 커스텀 리스트 아이템 (삭제 버튼, 지정 중 효과, 더블클릭 이동)
# ==========================================
class SectionWidget(QWidget):
    def __init__(self, start_text, end_text, list_widget, item, parent_tab, is_pending=False):
        super().__init__()
        self.list_widget = list_widget
        self.item = item
        self.parent_tab = parent_tab
        self.start_time = start_text
        self.end_time = end_text

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


# ==========================================
# 4. 탭 2: 미디어 편집기
# ==========================================
class EditorTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.current_file = ""
        self.keyframes = [] 
        self.marking_start_ms = None 
        
        self.sections_list = [] 
        
        self.setAcceptDrops(True)
        
        self.colors = [QColor(255, 50, 50, 150), QColor(50, 255, 50, 150), QColor(50, 50, 255, 150), 
                       QColor(255, 200, 0, 150), QColor(200, 50, 255, 150)]
        self.init_ui()
        self.setup_shortcuts()

    def init_ui(self):
        layout = QVBoxLayout()

        # [파일 선택 및 닫기]
        file_layout = QHBoxLayout()
        self.lbl_file = QLabel("선택된 파일 없음 (아래 점선 박스에 영상을 드래그 앤 드롭하세요)")
        self.lbl_kf_status = QLabel("")
        self.lbl_kf_status.setStyleSheet("color: gray;")
        
        btn_open = QPushButton("찾아보기...")
        btn_open.clicked.connect(self.open_file)
        
        self.btn_close_media = QPushButton("현재 영상 닫기 (새 작업)")
        self.btn_close_media.clicked.connect(self.close_media)
        self.btn_close_media.setVisible(False)
        
        file_layout.addWidget(self.lbl_file, stretch=1)
        file_layout.addWidget(self.lbl_kf_status)
        file_layout.addWidget(btn_open)
        file_layout.addWidget(self.btn_close_media)
        layout.addLayout(file_layout)

        # [비디오 플레이어 스택]
        self.video_stack = QStackedWidget()
        self.video_stack.setMinimumHeight(350)
        
        self.lbl_drop = QLabel("📂 이 곳에 영상/음원 파일을 드래그 앤 드롭하세요")
        self.lbl_drop.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_drop.setStyleSheet("background-color: #1e1e1e; color: #aaaaaa; font-size: 18px; border: 2px dashed #555555;")
        
        self.video_widget = DropVideoWidget()
        self.video_widget.file_dropped.connect(self.load_media)
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

        # [타임라인 바]
        control_layout = QHBoxLayout()
        self.slider = TimelineSlider(Qt.Orientation.Horizontal)
        self.slider.player_ref = self.player
        self.slider.sliderMoved.connect(self.set_position)

        self.lbl_time = QLabel("00:00:00 / 00:00:00")
        
        control_layout.addWidget(self.slider)
        control_layout.addWidget(self.lbl_time)
        layout.addLayout(control_layout)

        # [재생 및 기타 컨트롤]
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

        # [구간 설정 UI]
        cut_group = QGroupBox("구간 자르기 목록 (단축키: '[' 시작 지정, ']' 종료 즉시 추가, 'Shift+방향키' 키프레임 점프, 'Ctrl+방향키' 구간 점프)")
        cut_layout = QHBoxLayout()
        
        self.list_sections = QListWidget()
        cut_layout.addWidget(self.list_sections, stretch=2)

        manual_layout = QVBoxLayout()
        time_input_layout = QHBoxLayout()
        self.entry_start = QLineEdit("00:00:00.000")
        self.entry_end = QLineEdit("00:00:00.000")
        time_input_layout.addWidget(QLabel("시작:"))
        time_input_layout.addWidget(self.entry_start)
        time_input_layout.addWidget(QLabel("종료:"))
        time_input_layout.addWidget(self.entry_end)
        manual_layout.addLayout(time_input_layout)
        
        self.chk_precision = QCheckBox("정밀 자르기 (체크 안 하면 키프레임 기준 무손실 복사)")
        manual_layout.addWidget(self.chk_precision)
        
        self.chk_merge = QCheckBox("리스트의 모든 구간을 하나로 병합")
        self.chk_merge.setChecked(True)
        manual_layout.addWidget(self.chk_merge)

        self.chk_ext_audio = QCheckBox("음원으로 추출")
        self.cb_ext_audio = QComboBox()
        self.cb_ext_audio.addItems(["추천", "mp3", "m4a", "aac", "opus"])
        
        audio_opt_layout = QHBoxLayout()
        audio_opt_layout.addWidget(self.chk_ext_audio)
        audio_opt_layout.addWidget(self.cb_ext_audio)
        manual_layout.addLayout(audio_opt_layout)

        # [저장 위치 및 이름 커스텀]
        export_setting_layout = QHBoxLayout()
        self.entry_out_dir = QLineEdit()
        self.entry_out_dir.setPlaceholderText("저장될 폴더 (기본: 원본 폴더)")
        self.btn_out_dir = QPushButton("폴더 변경")
        self.btn_out_dir.clicked.connect(self.change_out_dir)

        self.entry_out_name = QLineEdit()
        self.entry_out_name.setPlaceholderText("기본값: 원본파일명_edited")

        export_setting_layout.addWidget(QLabel("저장 위치:"))
        export_setting_layout.addWidget(self.entry_out_dir, stretch=2)
        export_setting_layout.addWidget(self.btn_out_dir)
        export_setting_layout.addSpacing(10)
        export_setting_layout.addWidget(QLabel("파일명:"))
        export_setting_layout.addWidget(self.entry_out_name, stretch=2)
        manual_layout.addLayout(export_setting_layout)

        self.btn_export = QPushButton("내보내기 실행")
        self.btn_export.setMinimumHeight(40)
        self.btn_export.clicked.connect(self.execute_export)
        manual_layout.addWidget(self.btn_export)

        cut_layout.addLayout(manual_layout, stretch=1)
        cut_group.setLayout(cut_layout)
        layout.addWidget(cut_group)

        self.setLayout(layout)

    def change_out_dir(self):
        path = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", self.entry_out_dir.text() or Config.DEFAULT_PATHS["영상 저장 폴더"])
        if path:
            self.entry_out_dir.setText(os.path.normpath(path))

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path: self.load_media(path)

    def setup_shortcuts(self):
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self.toggle_play)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, lambda: self.seek(-5000))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, lambda: self.seek(5000))
        
        QShortcut(QKeySequence(Qt.Key.Key_Up), self, lambda: self.seek(60000))
        QShortcut(QKeySequence(Qt.Key.Key_Down), self, lambda: self.seek(-60000))
        
        QShortcut(QKeySequence("Shift+Left"), self, lambda: self.seek_keyframe(-1))
        QShortcut(QKeySequence("Shift+Right"), self, lambda: self.seek_keyframe(1))
        
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
        
        input_dir = os.path.dirname(path)
        self.entry_out_dir.setText(input_dir)
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
        self.lbl_kf_status.setText("| 키프레임 분석 중... (잠시만 기다려주세요)")
        
        def _kf_task():
            ffprobe = self.main_window.downloader_tab.get_exe("ffprobe.exe").strip('"')
            cmd = f'"{ffprobe}" -loglevel error -skip_frame nokey -select_streams v:0 -show_entries frame=pkt_pts_time -of default=noprint_wrappers=1:nokey=1 "{path}"'
            try:
                proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, creationflags=0x08000000)
                kfs = [float(line.strip()) * 1000 for line in proc.stdout.splitlines() if line.strip()]
                self.keyframes = sorted(kfs)
                self.lbl_kf_status.setText(f"| 키프레임 {len(self.keyframes)}개 장전 완료 (Shift+방향키 탐색 가능)")
            except Exception:
                self.lbl_kf_status.setText("| 키프레임 분석 실패 (일반 탐색만 가능)")
                
        threading.Thread(target=_kf_task, daemon=True).start()

    def seek_keyframe(self, direction):
        if not self.keyframes:
            self.seek(direction * 1000) 
            return
            
        current_pos = self.player.position()
        if direction > 0: 
            for kf in self.keyframes:
                if kf > current_pos + 100: 
                    self.player.setPosition(int(kf))
                    return
        else: 
            for kf in reversed(self.keyframes):
                if kf < current_pos - 100:
                    self.player.setPosition(int(kf))
                    return

    def jump_section(self, direction):
        if not self.sections_list: return
        pos = self.player.position()
        
        if direction < 0:
            candidates = [s for s, e in self.sections_list if s < pos - 100]
            if candidates:
                target_s = max(candidates)
                # 5초(5000ms) 이내에 있다면, 그 이전 구간으로 건너뛰기
                if pos - target_s < 5000:
                    older_candidates = [s for s, e in self.sections_list if s < target_s - 100]
                    if older_candidates:
                        target_s = max(older_candidates)
                self.player.setPosition(target_s)
        else:
            candidates = [s for s, e in self.sections_list if s > pos + 100]
            if candidates: self.player.setPosition(min(candidates))

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.btn_play.setText("▶ 재생 (Space)")
        else:
            self.player.play()
            self.btn_play.setText("⏸ 일시정지 (Space)")

    def seek(self, ms):
        new_pos = max(0, min(self.player.duration(), self.player.position() + ms))
        self.player.setPosition(new_pos)

    def position_changed(self, position):
        if not self.slider.isSliderDown():
            self.slider.setValue(position)
        self.update_time_label(position, self.player.duration())
        
        if self.marking_start_ms is not None:
            self.slider.set_temp_section((self.marking_start_ms, position, QColor(255, 100, 100, 150)))

    def duration_changed(self, duration):
        self.slider.set_duration(duration)
        self.update_time_label(self.player.position(), duration)

    def set_position(self, position):
        self.player.setPosition(position)

    def format_time(self, ms):
        return QTime(0, 0, 0).addMSecs(ms).toString("hh:mm:ss.zzz")

    def time_to_ms(self, t_str):
        return QTime.fromString(t_str, "hh:mm:ss.zzz").msecsSinceStartOfDay()

    def update_time_label(self, pos, dur):
        self.lbl_time.setText(f"{self.format_time(pos)} / {self.format_time(dur)}")

    # --------------------------------------------------
    # 겹침 방지 및 기준점 자동 연산 로직
    # --------------------------------------------------
    def mark_start(self):
        pos = self.player.position()
        for s, e in self.sections_list:
            if s <= pos <= e:
                return 

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
        
        # (지정 중...) 잔상 제거를 위해 변수 초기화부터 선행
        self.marking_start_ms = None
        self.slider.set_temp_section(None)
        self.commit_section(S, E)

    def commit_section(self, S, E):
        if S >= E: return

        new_list = []
        for s, e in self.sections_list:
            if max(s, S) < min(e, E): 
                pass # 겹치면 버림
            else:
                new_list.append((s, e))

        new_list.append((S, E))
        new_list.sort(key=lambda x: x[0])
        self.sections_list = new_list
        self.refresh_section_list_ui()

    def delete_selected_list_item(self):
        rows = [self.list_sections.row(item) for item in self.list_sections.selectedItems()]
        if rows:
            for row in sorted(rows, reverse=True):
                self.sections_list.pop(row)
            self.refresh_section_list_ui()

    def refresh_section_list_ui(self):
        self.list_sections.clear()
        sections_data = []
        
        for i, (s_ms, e_ms) in enumerate(self.sections_list):
            item = QListWidgetItem(self.list_sections)
            item.setSizeHint(QWidget().sizeHint())
            s_str = self.format_time(s_ms)
            e_str = self.format_time(e_ms)
            
            widget = SectionWidget(s_str, e_str, self.list_sections, item, self)
            self.list_sections.setItemWidget(item, widget)
            color = self.colors[i % len(self.colors)]
            sections_data.append((s_ms, e_ms, color))
            
        if self.marking_start_ms is not None:
            item = QListWidgetItem(self.list_sections)
            item.setSizeHint(QWidget().sizeHint())
            s_str = self.format_time(self.marking_start_ms)
            widget = SectionWidget(s_str, "(지정 중...)", self.list_sections, item, self, is_pending=True)
            self.list_sections.setItemWidget(item, widget)

        self.slider.set_sections(sections_data)

    def get_audio_codec(self, filepath):
        ffprobe = self.main_window.downloader_tab.get_exe("ffprobe.exe").strip('"')
        cmd = f'"{ffprobe}" -v error -select_streams a:0 -show_entries stream=codec_name -of default=nw=1:nk=1 "{filepath}"'
        try:
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, creationflags=0x08000000)
            return proc.stdout.strip().lower()
        except:
            return "aac"

    def execute_export(self):
        if not self.current_file:
            QMessageBox.warning(self, "경고", "먼저 파일을 불러오세요.")
            return
            
        if not self.sections_list:
            reply = QMessageBox.question(self, "확인", "지정된 구간이 없습니다. 영상 전체를 변환하시겠습니까?", 
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.No: return

        self.btn_export.setEnabled(False)
        self.btn_export.setText("작업 진행 중...")
        
        is_audio = self.chk_ext_audio.isChecked()
        audio_ext = self.cb_ext_audio.currentText()
        is_merge = self.chk_merge.isChecked()
        is_precision = self.chk_precision.isChecked()
        
        out_dir = self.entry_out_dir.text().strip()
        custom_name = self.entry_out_name.text().strip()
        
        sections_to_process = [(self.format_time(s), self.format_time(e)) for s, e in self.sections_list]
        threading.Thread(target=self._export_task, args=(self.current_file, sections_to_process, is_audio, audio_ext, is_merge, is_precision, out_dir, custom_name), daemon=True).start()

    def _export_task(self, input_file, sections, is_audio, audio_ext, is_merge, is_precision, out_dir, custom_name):
        ffmpeg = os.path.join(Config.BASE_DIR, "ffmpeg.exe")
        if not os.path.exists(ffmpeg): ffmpeg = "ffmpeg"
        
        if is_audio and audio_ext == "추천":
            codec = self.get_audio_codec(input_file)
            if codec == "opus": audio_ext = "opus"
            elif codec == "mp3": audio_ext = "mp3"
            else: audio_ext = "m4a"

        if not out_dir or not os.path.exists(out_dir):
            out_dir = os.path.dirname(input_file)

        if custom_name:
            base_name = custom_name
        else:
            base_name = f"{os.path.splitext(os.path.basename(input_file))[0]}_edited"

        ext = os.path.splitext(input_file)[1] if not is_audio else f".{audio_ext}"
        created_files = []

        try:
            if not sections:
                sections = [("00:00:00.000", self.format_time(self.player.duration()))]
                
            for idx, (start, end) in enumerate(sections):
                if len(sections) == 1 and not is_merge:
                    out_path = os.path.join(out_dir, f"{base_name}{ext}")
                else:
                    out_path = os.path.join(out_dir, f"{base_name}_cut_{idx:03}{ext}")
                
                cmd = f'"{ffmpeg}" -y -ss {start} -to {end} -i "{input_file}" '
                if is_audio:
                    cmd += f'-vn -c:a {"copy" if audio_ext in ["m4a", "opus"] else "libmp3lame"} '
                else:
                    cmd += f'-c:v libx264 -preset fast ' if is_precision else f'-c copy '
                cmd += f'"{out_path}"'
                
                subprocess.run(cmd, shell=True, creationflags=0x08000000)
                created_files.append(out_path)

            if is_merge and len(created_files) > 1:
                merge_list = os.path.join(out_dir, "merge_list.txt")
                with open(merge_list, "w", encoding="utf-8") as f:
                    for cf in created_files: f.write(f"file '{cf}'\n")
                
                merged_out = os.path.join(out_dir, f"{base_name}{ext}")
                cmd = f'"{ffmpeg}" -y -f concat -safe 0 -i "{merge_list}" -c copy "{merged_out}"'
                subprocess.run(cmd, shell=True, creationflags=0x08000000)
                
                os.remove(merge_list)
                for cf in created_files: os.remove(cf)
                    
            QMetaObject = getattr(self.main_window, "metaObject", None)
            if QMetaObject:
                self.main_window.statusBar().showMessage("미디어 내보내기가 완료되었습니다!", 5000)
                
        except Exception as e:
            print(f"Export error: {e}")
        finally:
            self.btn_export.setEnabled(True)
            self.btn_export.setText("내보내기 실행")


# ==========================================
# 5. 메인 윈도우 (탭 관리자)
# ==========================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("yt-dlp 미디어 통합 매니저")
        self.resize(900, 750)
        
        Config.init_folders() 

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.downloader_tab = DownloaderTab()
        self.editor_tab = EditorTab(self)

        self.tabs.addTab(self.downloader_tab, "⬇️ 다운로더")
        self.tabs.addTab(self.editor_tab, "✂️ 미디어 편집기")
        
        self.statusBar().showMessage("준비 완료")


# ==========================================
# 리소스 경로 자동 인식 함수 (PyInstaller 호환용)
# ==========================================
def resource_path(relative_path):
    try:
        # PyInstaller가 생성한 임시 폴더 경로 (_MEIPASS)
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    # 작업 표시줄 및 창 왼쪽 위 기본 아이콘 적용
    app.setWindowIcon(QIcon(resource_path("ytdlp_studio.ico")))
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())