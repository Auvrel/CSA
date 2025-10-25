from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5 import QtCore
import os, time, threading, traceback,sys
from core.archive import load_archive_index, extract_single
import struct
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from PyQt5.QtCore import QThread, pyqtSignal, QCoreApplication # extract_single can fallback to zlib/lzma
# no duplicate imports
from core.file_utils import detect_mode
from core.compressor_core import compress_file_core, METHOD_RSF, ARCHIVE_HEADER_MAGIC
import concurrent.futures
import threading

_rsf_lock = threading.Lock()  # serialize calls if RSF isn’t thread-safe

def safe_rsf_compress(path, raw, timeout=6):
    """
    Run rsf_compress_file() with timeout + lock.
    Falls back to None if RSF hangs or errors.
    """
    from core.rsf_wrapper import rsf_compress_file

    try:
        with _rsf_lock:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(rsf_compress_file, path, raw)
                return fut.result(timeout=timeout)
    except Exception as e:
        print(f"[RSF⚠️] RSF failed or timed out on {path}: {e}")
        return None

class CompressThread(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(int)

    def __init__(self, root_dir, out_file, use_rsf_auto=True):
        super().__init__()
        self.root_dir = root_dir
        self.out_file = out_file
        self.use_rsf_auto = use_rsf_auto
        self.max_workers = 4

    def safe_log(self, msg):
        """Console print that won’t crash PyQt"""
        print(f"[RSF DEBUG] {msg}")
        sys.stdout.flush()

    def _emit_progress(self, pct, msg):
        self.progress.emit(pct, msg)
        QCoreApplication.processEvents()  # manually flush Qt events
        self.safe_log(f"{pct}% → {msg}")

    def run(self):
        try:
            start_time = time.time()
            files = []
            for root, _, fs in os.walk(self.root_dir):
                for f in fs:
                    files.append(os.path.join(root, f))
            total = len(files)
            if total == 0:
                self._emit_progress(0, "💀 No files found.")
                self.finished.emit(0)
                return

            self._emit_progress(0, f"📦 Compressing {total} files…")
            index = {}
            offset = 7
            os.makedirs(os.path.dirname(os.path.abspath(self.out_file)) or ".", exist_ok=True)
            f_out = open(self.out_file, "wb")
            f_out.write(b"CSA")
            f_out.write(struct.pack("<I", 0))

            # parallel pool
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                futures = [pool.submit(self._compress_one, path) for path in files]
                done = 0
                for fut in as_completed(futures):
                    rel, blob, method, orig, rows, cols = fut.result()
                    if blob is None:
                        continue
                    f_out.seek(offset)
                    f_out.write(blob)
                    size = len(blob)
                    index[rel] = {
                        "start": offset,
                        "comp_size": size,
                        "orig_size": orig,
                        "method": method,
                        "rows": rows,
                        "cols": cols
                    }
                    offset += size
                    done += 1
                    if done % 10 == 0 or done == total:
                        pct = int((done / total) * 100)
                        self._emit_progress(pct, f"🗜️ {done}/{total}: {os.path.basename(rel)}")

            # finalize
            self._emit_progress(99, "🧾 Finalizing archive index (writing JSON)…")

            threading.Thread(
                target=self._finalize_archive,
                args=(f_out, index, total, start_time),
                daemon=True
            ).start()

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self.safe_log(tb)
            self._emit_progress(0, f"💥 Compression crashed: {e}")
            self.finished.emit(-1)

    def _finalize_archive(self, f_out, index, total, start_time):
        try:
            self.safe_log("🧠 Writing index JSON…")
            index_bytes = json.dumps(index, separators=(",", ":")).encode("utf-8")
            f_out.write(index_bytes)
            index_size = len(index_bytes)
            f_out.seek(3)
            f_out.write(struct.pack("<I", index_size))
            f_out.flush()
            os.fsync(f_out.fileno())
            f_out.close()
            elapsed = time.time() - start_time
            self._emit_progress(100, f"✅ Done! {total} files compressed in {elapsed:.1f}s")
            self.finished.emit(total)
        except Exception as e:
            self.safe_log(f"[finalize crash] {e}")
            self._emit_progress(0, f"💥 Finalization failed: {e}")
            self.finished.emit(-1)

    def _compress_one(self, path):
     try:
        with open(path, "rb") as f:
            raw = f.read()

        # just run the normal compressor, no RSF auto-detect
        comp, method, orig, rows, cols = compress_file_core(path, raw)
        self.safe_log(f"[CORE✅] compressed {os.path.basename(path)}")
        return path, comp, method, orig, rows, cols

     except Exception as e:
        self.safe_log(f"[ERR💀] {path}: {e}")
        return path, None, 0, 0, 0, 0
class ExtractThread(QtCore.QThread):
    progress = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    def __init__(self, archive_path, output_dir):
        super().__init__()
        self.archive_path = archive_path
        self.output_dir = output_dir

    def run(self):
        try:
            with open(self.archive_path, 'rb') as f:
                magic = f.read(3)
                if magic != b'CSA':
                    self.progress.emit("Not a valid CSA archive")
                    return
                index_size = struct.unpack('<I', f.read(4))[0]
                f.seek(-index_size, os.SEEK_END)
                index_bytes = f.read(index_size)
                index = json.loads(index_bytes)

                for rel, meta in index.items():
                    start = meta['start']
                    comp_size = meta['comp_size']
                    f.seek(start)
                    blob = f.read(comp_size)
                    out_path = os.path.join(self.output_dir, rel)
                    os.makedirs(os.path.dirname(out_path), exist_ok=True)
                    with open(out_path, 'wb') as out_f:
                        out_f.write(blob)
                    self.progress.emit(f"Extracted: {rel}")

            self.finished.emit()
        except Exception as e:
            self.progress.emit(f"Error: {e}")
            self.finished.emit()

