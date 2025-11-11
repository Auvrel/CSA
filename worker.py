# worker.py (CORRECTED VERSION)

import threading
from pathlib import Path
from typing import Callable
import os

# Placeholder imports for core logic - adjust if your file paths are different
from core.archive import build_archive, extract_archive # load_archive_index is used only for info
from core.compressor_core import compress_file_core

# Type Aliases for clarity
# The worker's progress_cb is (percent, message) which updates the UI
ProgressCallback = Callable[[int, str], None] 
FinishedCallback = Callable[[bool, str], None] 


class CompressionWorker:
    # NOTE: Does NOT inherit from threading.Thread
    def __init__(self, root_dir: str, out_file: str, progress_cb: ProgressCallback, finished_cb: FinishedCallback):
        self.root_dir = root_dir
        self.out_file = out_file
        self.progress_cb = progress_cb
        self.finished_cb = finished_cb
        self.stop_event = threading.Event() 

    def run_process(self):
        try:
            root_path = Path(self.root_dir)
            
            # --- File Listing (Simplified for the worker) ---
            file_list=[]
            for dirpath, _, filenames in os.walk(root_path):
                for filename in filenames:
                    file_list.append(Path(dirpath) / filename)

            if not file_list:
                self.finished_cb(False, "Error: No files found to compress.")
                return

            self.progress_cb(0, f"Starting compression of {len(file_list)} files...")

            # --- CORRECTED Progress Hook (3-Argument Signature) ---
            def progress_hook(current_count, total_count, current_path):
                if self.stop_event.is_set():
                    return False # Signal cancellation to build_archive
                
                # Calculate UI percentage and format message
                pct = int((current_count / total_count) * 100)
                
                # Check if current_path is a Path object or the final message string
                if isinstance(current_path, Path):
                    msg = f"Archiving file {current_count} of {total_count}: {current_path.name}"
                elif isinstance(current_path, str):
                    msg = current_path
                else:
                    msg = f"Archiving: {pct}% complete."

                self.progress_cb(pct, msg) # This calls the UI update function
                return True # Signal continuation to build_archive

            # --- Build Archive Call (Corrected root_dir) ---
            build_archive(
                root_dir=str(root_path), # Pass as string as per function definition
                out_file=self.out_file,
                compress_callback=compress_file_core,
                progress_cb=progress_hook # Pass the 3-arg hook
            )
            
            if self.stop_event.is_set():
                self.finished_cb(False, "Compression Cancelled.")
            else:
                self.finished_cb(True, "Compression Succeeded!")

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.finished_cb(False, f"Fatal Compression Error: {e}")


class ExtractWorker:
    # NOTE: Does NOT inherit from threading.Thread
    def __init__(self, archive_path: str, output_dir: str, progress_cb: ProgressCallback, finished_cb: FinishedCallback):
        self.archive_path = archive_path
        self.output_dir = output_dir
        self.progress_cb = progress_cb
        self.finished_cb = finished_cb
        self.stop_event = threading.Event() 

    def run_process(self):
        try:
            self.progress_cb(0, "Starting extraction...")
            
            # --- CORRECTED Progress Hook (3-Argument Signature) ---
            def progress_hook(current_count, total_count, current_path):
                if self.stop_event.is_set():
                    # Raising an error is the correct way to stop the external function
                    raise InterruptedError("Extraction cancelled by user.")
                
                # Calculate UI percentage
                pct = int((current_count / total_count) * 100)
                
                if isinstance(current_path, Path):
                    msg = f"Extracting file {current_count} of {total_count}: {current_path.name}"
                elif isinstance(current_path, str):
                    msg = current_path
                else:
                    msg = f"Extracting: {pct}% complete."

                self.progress_cb(pct, msg) # This calls the UI update function
                return True # Not strictly necessary if raising InterruptedError

            # --- Extract Archive Call (Corrected Signature) ---
            # NOTE: The extract_archive function no longer takes the index as a separate argument.
            extract_archive(
                archive_path=self.archive_path,
                output_dir=self.output_dir,
                progress_hook=progress_hook # Pass the 3-arg hook
            )
            
            if self.stop_event.is_set():
                self.finished_cb(False, "Extraction Cancelled.")
            else:
                self.finished_cb(True, "Extraction Succeeded!")

        except InterruptedError:
             self.finished_cb(False, "Extraction Cancelled.")
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.finished_cb(False, f"Fatal Extraction Error: {e}")