from PyQt5 import QtWidgets, QtCore
from PyQt5.QtWidgets import QFileDialog, QMessageBox, QTreeWidgetItem
import os
import struct
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

# imports from your core modules
from core.file_utils import detect_mode
from core.compressor_core import compress_file_core, METHOD_RSF, ARCHIVE_HEADER_MAGIC
from core.archive import load_archive_index, extract_single
from gui.worker import CompressThread, ExtractThread

class ThreadedCompressor(QtCore.QThread):
    progress = QtCore.pyqtSignal(int, str)
    finished = QtCore.pyqtSignal(int)

    def __init__(self, root_dir, out_file, max_workers=None):
        super().__init__()
        self.root_dir = root_dir
        self.out_file = out_file
        self.max_workers = max_workers or min(4, (os.cpu_count() or 4))
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        # gather files
        all_paths = [os.path.join(r, f) for r, _, fs in os.walk(self.root_dir) for f in fs]
        total = len(all_paths)
        if total == 0:
            self.progress.emit(0, "No files found, nothing to do.")
            self.finished.emit(0)
            return
        self.progress.emit(1, f"Found {total} files. Launching {self.max_workers} workers...")

        index = {}
        current_offset = 7  # header: 3 magic bytes + 4 index size

        with open(self.out_file, 'wb') as f_out:
            f_out.write(ARCHIVE_HEADER_MAGIC)
            f_out.write(struct.pack('<I', 0))

            def compress_one(path):
                rel = os.path.relpath(path, self.root_dir).replace(os.path.sep, '/')
                try:
                    with open(path, 'rb') as fr:
                        raw = fr.read()
                    comp, method, orig, rows, cols = compress_file_core(path, raw)
                    return (rel, comp, method, orig, rows, cols)
                except Exception as e:
                    return (rel, None, 'ERR', 0, 0, 0)

            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                future_to_path = {pool.submit(compress_one, p): p for p in all_paths}
                completed = 0
                for future in as_completed(future_to_path):
                    if self._stop_requested:
                        break
                    p = future_to_path[future]
                    try:
                        rel, blob, method, orig, rows, cols = future.result()
                    except Exception as exc:
                        self.progress.emit(int((completed/total)*100), f"ERR compressing {p}: {exc}")
                        completed += 1
                        continue

                    if blob is None:
                        self.progress.emit(int((completed/total)*100), f"Failed: {rel} (skipped)")
                        completed += 1
                        continue

                    f_out.seek(current_offset)
                    f_out.write(blob)
                    comp_size = len(blob)
                    index[rel] = {
                        'start': current_offset,
                        'comp_size': comp_size,
                        'orig_size': orig,
                        'method': method,
                        'rows': rows,
                        'cols': cols
                    }
                    current_offset += comp_size
                    completed += 1
                    pct = int((completed/total) * 100)
                    self.progress.emit(pct, f"[{completed}/{total}] {rel} -> {comp_size:,} bytes (method {method})")

            # write index
            index_bytes = json.dumps(index).encode('utf-8')
            f_out.write(index_bytes)
            f_out.seek(3)
            f_out.write(struct.pack('<I', len(index_bytes)))

        self.progress.emit(100, f"Archive complete: {len(index)} files written to {self.out_file}")
        self.finished.emit(len(index))

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CSA Archiver")
        self.resize(1000, 700)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        tabs = QtWidgets.QTabWidget()
        layout.addWidget(tabs)

        # --- Compressor tab ---
        comp_tab = QtWidgets.QWidget()
        tabs.addTab(comp_tab, "Compress")
        c_layout = QtWidgets.QVBoxLayout(comp_tab)

        h1 = QtWidgets.QHBoxLayout()
        self.src_edit = QtWidgets.QLineEdit(os.getcwd())
        btn_src = QtWidgets.QPushButton("Browse Folder")
        btn_src.clicked.connect(self.pick_folder)
        h1.addWidget(self.src_edit); h1.addWidget(btn_src)
        c_layout.addLayout(h1)

        h2 = QtWidgets.QHBoxLayout()
        self.out_edit = QtWidgets.QLineEdit(os.path.join(os.getcwd(), "archive.csa"))
        btn_out = QtWidgets.QPushButton("Save As")
        btn_out.clicked.connect(self.pick_save)
        h2.addWidget(self.out_edit); h2.addWidget(btn_out)
        c_layout.addLayout(h2)

        self.start_btn = QtWidgets.QPushButton("Start Archiving")
        self.start_btn.clicked.connect(self.start_archive)
        c_layout.addWidget(self.start_btn)

        self.progress = QtWidgets.QProgressBar(); c_layout.addWidget(self.progress)
        self.log = QtWidgets.QTextEdit(); self.log.setReadOnly(True); c_layout.addWidget(self.log)

        # --- Explorer tab ---
        expl_tab = QtWidgets.QWidget()
        tabs.addTab(expl_tab, "Explorer")
        e_layout = QtWidgets.QVBoxLayout(expl_tab)

        h3 = QtWidgets.QHBoxLayout()
        self.open_edit = QtWidgets.QLineEdit()
        btn_open = QtWidgets.QPushButton("Open .csa")
        btn_open.clicked.connect(self.open_archive)
        h3.addWidget(self.open_edit); h3.addWidget(btn_open)
        e_layout.addLayout(h3)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Name","Method","Orig Size","Comp Size","Ratio"])
        e_layout.addWidget(self.tree)

        h4 = QtWidgets.QHBoxLayout()
        self.dest_edit = QtWidgets.QLineEdit(os.path.join(os.getcwd(), "extracted"))
        btn_dest = QtWidgets.QPushButton("Browse Dest")
        btn_dest.clicked.connect(self.pick_dest)
        btn_extract_all = QtWidgets.QPushButton("Extract All")
        btn_extract_all.clicked.connect(self.extract_all)
        h4.addWidget(self.dest_edit); h4.addWidget(btn_dest); h4.addWidget(btn_extract_all)
        e_layout.addLayout(h4)

        # keep state
        self.current_index = None
        self.current_archive = None
        self.compress_thread = None
        self.extract_thread = None

    # --- UI helpers ---
    def log_msg(self, s): self.log.append(s)
    def pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Select source folder")
        if d: self.src_edit.setText(d)
    def pick_save(self):
        f, _ = QFileDialog.getSaveFileName(self, "Save Archive", filter="Custom Archive (*.csa)")
        if f: self.out_edit.setText(f)
    def pick_dest(self):
        d = QFileDialog.getExistingDirectory(self, "Select destination")
        if d: self.dest_edit.setText(d)

    # --- compression ---
    def start_archive(self):
        src = self.src_edit.text()
        out = self.out_edit.text()
        if not os.path.isdir(src):
            QMessageBox.critical(self, "Error", "Choose valid source folder")
            return
        self.start_btn.setEnabled(False)
        self.log_msg("Preparing archive...")
        self.compress_thread = ThreadedCompressor(src, out)
        self.compress_thread.progress.connect(self.on_progress)
        self.compress_thread.finished.connect(self.on_finished_archive)
        self.compress_thread.start()

    def on_progress(self, pct, msg):
        try: self.progress.setValue(pct)
        except Exception: pass
        self.log_msg(msg)

    def on_finished_archive(self, count):
        self.start_btn.setEnabled(True)
        self.log_msg(f"Archive finished. {count} files.")

    # --- Explorer ---
    def open_archive(self):
        f, _ = QFileDialog.getOpenFileName(self, "Open Archive", filter="Custom Archive (*.csa)")
        if not f: return
        self.open_edit.setText(f)
        try:
            idx = load_archive_index(f)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load archive: {e}")
            return
        self.current_index = idx
        self.current_archive = f
        self.tree.clear()

        nodes = {}
        for rel, meta in idx.items():
            parts = rel.split('/')
            for i in range(1, len(parts)+1):
                path = '/'.join(parts[:i])
                if path not in nodes:
                    item = QTreeWidgetItem()
                    item.setText(0, parts[i-1])
                    if i == len(parts):
                        method_name = {1:'DICOM',2:'LZMA',3:'ZLIB',4:'STORE',5:'RSF'}.get(meta.get('method'), 'GEN')
                        orig = meta.get('orig_size',0)
                        cs = meta.get('comp_size',0)
                        ratio = f"{(orig/cs):.2f}:1" if cs>0 else "N/A"
                        item.setText(1, method_name)
                        item.setText(2, str(orig))
                        item.setText(3, str(cs))
                        item.setText(4, ratio)
                    if i == 1:
                        self.tree.addTopLevelItem(item)
                    else:
                        parent_path = '/'.join(parts[:i-1])
                        nodes[parent_path].addChild(item)
                    nodes[path] = item

        self.tree.expandAll()
        self.log_msg("Archive loaded into explorer.")

    def extract_all(self):
        if not self.current_index or not self.current_archive:
            QMessageBox.critical(self, "Error", "No archive loaded")
            return
        dest = self.dest_edit.text()
        os.makedirs(dest, exist_ok=True)
        self.extract_thread = ExtractThread(self.current_archive, dest)
        self.extract_thread.progress.connect(lambda s: self.log_msg(s))
        self.extract_thread.finished.connect(lambda: QMessageBox.information(self, "Done", "Extraction complete"))
        self.extract_thread.start()