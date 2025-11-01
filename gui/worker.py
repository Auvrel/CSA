# worker.py - Background workers for compression and extraction
import threading
import time
import traceback
from pathlib import Path
from core.archive import build_archive, load_archive_index, extract_single
from core.compressor_core import compress_file_core

def run_task(fn, callback=None):
    """
    Runs a blocking function `fn` in a background thread.
    Optionally calls `callback(result or exception)` after completion.
    """

    def _task():
        try:
            result = fn()
            if callback:
                callback(result)
        except Exception as e:
            traceback.print_exc()
            if callback:
                callback(e)

    t = threading.Thread(target=_task, daemon=True)
    t.start()
    return t

class CompressionWorker(threading.Thread):
    def __init__(self, root_dir, out_file, progress_cb, finished_cb):
        super().__init__()
        self.root_dir = root_dir
        self.out_file = out_file
        self.progress_cb = progress_cb
        self.finished_cb = finished_cb

    def run(self):
        try:
            t0 = time.time()
            def progress(pct, msg):
                self.progress_cb(pct, msg)

            count = build_archive(
                self.root_dir,
                self.out_file,
                progress_cb=progress,
                compress_callback=compress_file_core
            )
            dt = time.time() - t0
            self.finished_cb(True, f"✅ Done. {count} files compressed in {dt:.1f}s")
        except Exception as e:
            traceback.print_exc()
            self.progress_cb(0, f"💥 Error: {e}")
            self.finished_cb(False, str(e))


class ExtractWorker(threading.Thread):
    def __init__(self, archive_path, output_dir, progress_cb, finished_cb):
        super().__init__()
        self.archive_path = archive_path
        self.output_dir = output_dir
        self.progress_cb = progress_cb
        self.finished_cb = finished_cb

    def run(self):
        try:
            index = load_archive_index(self.archive_path)
            total = len(index)
            for i, rel in enumerate(index.keys(), 1):
                data = extract_single(self.archive_path, index, rel)
                out_path = Path(self.output_dir) / rel
                out_path.parent.mkdir(parents=True, exist_ok=True)
                with open(out_path, "wb") as f:
                    f.write(data)
                pct = int((i / total) * 100)
                self.progress_cb(pct, f"Extracted {rel}")
            self.finished_cb(True, f"✅ Extracted {total} files")
        except Exception as e:
            traceback.print_exc()
            self.progress_cb(0, f"💥 Error: {e}")
            self.finished_cb(False, str(e))
