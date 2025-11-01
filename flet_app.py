# flet_app.py - RSF Compressor GUI

import flet as ft
from pathlib import Path
import os
import threading
import time 
import traceback
import tempfile
import platform

# --- CORE IMPORTS ---
# NOTE: These imports rely on your 'worker.py' file being present.
from gui.worker import CompressionWorker, ExtractWorker
# --------------------

# --- Worker Modifications for Flet Cancellation and Error Handling ---

class FletCompressionWorker(CompressionWorker):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stop_event = threading.Event() 

    def run(self):
        try:
            t0 = time.time()
            
            # --- CORRECTED IMPORTS: Must import functions for call, not self.function ---
            from core.archive import build_archive
            from core.compressor_core import compress_file_core
            # ---------------------------------------------------------------------------
            
            def progress_wrapper(pct, msg):
                if self.stop_event.is_set():
                    raise InterruptedError("Compression cancelled by user.") 
                self.progress_cb(pct, msg)

            count = build_archive( 
                self.root_dir,
                self.out_file,
                progress_cb=progress_wrapper,
                compress_callback=compress_file_core
            )
            
            dt = time.time() - t0
            self.finished_cb(True, f"✅ Done. {count} files compressed in {dt:.1f}s")
            
        except InterruptedError:
            self.finished_cb(False, "Compression cancelled by user.")
        except Exception as e:
            traceback.print_exc()
            if os.path.exists(self.out_file):
                os.remove(self.out_file)
            self.progress_cb(0, f"💥 Error: {e}")
            self.finished_cb(False, str(e))

class FletExtractWorker(ExtractWorker):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stop_event = threading.Event()

    def run(self):
        try:
            # --- CORRECTED IMPORTS: Must import functions for call ---
            from core.archive import load_archive_index, extract_single 
            # --------------------------------------------------------
            
            index = load_archive_index(self.archive_path)
            total = len(index)
            
            for i, rel in enumerate(index.keys(), 1):
                if self.stop_event.is_set():
                    raise InterruptedError("Extraction cancelled by user.") 
                    
                data = extract_single(self.archive_path, index, rel)
                out_path = Path(self.output_dir) / rel
                out_path.parent.mkdir(parents=True, exist_ok=True)
                with open(out_path, "wb") as f:
                    f.write(data)
                    
                pct = int((i / total) * 100)
                self.progress_cb(pct, f"Extracted {rel}")
                
            self.finished_cb(True, f"✅ Extracted {total} files")
            
        except InterruptedError:
            self.finished_cb(False, "Extraction cancelled by user.")
        except Exception as e:
            traceback.print_exc()
            self.progress_cb(0, f"💥 Error: {e}")
            self.finished_cb(False, str(e))


# -----------------------
# Modern minimalistic color palette 
COLOR_BG      = "#1E1E1E"  
COLOR_SURFACE = "#2D2D2D"  
COLOR_BORDER  = "#404040"  
COLOR_TEXT    = "#E8E8E8"  
COLOR_TEXT_MUTED = "#A0A0A0" 
COLOR_ACCENT  = "#6366F1"  
COLOR_SUCCESS = "#10B981"  
COLOR_ERROR   = "#EF4444"  
COLOR_WARNING = "#F59E0B"  

def main(page: ft.Page):
    page.title = "RSF Compressor"
    page.window_width = 1600
    page.window_height = 900
    page.bgcolor = COLOR_BG
    page.padding = 15
    page.theme_mode = ft.ThemeMode.DARK

    # State
    current_source = None
    is_archive = False
    archive_index = None
    is_processing = False
    active_worker = None
    
    # Temporary directory for single-file extraction and opening
    TEMP_DIR = Path(tempfile.gettempdir()) / "rsf_compressor_temp"
    TEMP_DIR.mkdir(exist_ok=True)
    
    # File pickers (must be created and added to overlay)
    file_picker_src = ft.FilePicker(on_result=lambda e: None)
    file_picker_src_folder = ft.FilePicker(on_result=lambda e: None)
    file_picker_out = ft.FilePicker(on_result=lambda e: None)
    file_picker_extract = ft.FilePicker(on_result=lambda e: None)
    page.overlay.extend([file_picker_src, file_picker_src_folder, file_picker_out, file_picker_extract])

    # Thread-safe UI update helper
    def safe_update(update_fn):
        """Safely update UI from any thread"""
        async def async_wrapper():
            update_fn()
        
        if threading.current_thread() == threading.main_thread():
            update_fn()
        else:
            page.run_task(async_wrapper)

    def open_file_in_os(file_path):
        """Opens a file using the operating system's default application."""
        if not os.path.exists(file_path):
            log(f"Cannot open: file not found at {file_path}", "error")
            return
            
        try:
            if platform.system() == "Windows":
                os.startfile(file_path)
            elif platform.system() == "Darwin":
                os.system(f'open "{file_path}"')
            else:
                os.system(f'xdg-open "{file_path}"')
            
            log(f"Successfully launched file: {Path(file_path).name}", "success")
            
        except Exception as e:
            log(f"Failed to launch file {Path(file_path).name}: {e}", "error")

    # -----------------------
    # Log panel 
    log_entries = []
    log_update_pending = False

    def log(msg, level="info"):
        """Add log message (thread-safe, batched updates)"""
        nonlocal log_update_pending
        level_colors = {
            "info": COLOR_TEXT_MUTED, "success": COLOR_SUCCESS, 
            "error": COLOR_ERROR, "warning": COLOR_WARNING
        }
        timestamp = ft.Text(f"[{level.upper()}] ", size=11, color=level_colors.get(level, COLOR_TEXT_MUTED), weight=ft.FontWeight.BOLD)
        msg_text = ft.Text(msg, size=12, color=COLOR_TEXT, selectable=True)
        
        log_entries.append(
            ft.Container(
                content=ft.Row([timestamp, msg_text], spacing=5, tight=True),
                padding=ft.padding.only(bottom=4, left=5, right=5)
            )
        )
        
        if len(log_entries) > 1000:
            log_entries[:] = log_entries[-1000:]
        
        if not log_update_pending:
            log_update_pending = True
            def update_log():
                nonlocal log_update_pending
                log_scroll.controls = log_entries[-500:] 
                log_scroll.update()
                log_update_pending = False
            
            safe_update(update_log)

    log_scroll = ft.ListView(
        controls=[],
        expand=True,
        spacing=2,
        padding=5,
        auto_scroll=True
    )
    
    log_container = ft.Container(
        content=log_scroll,
        bgcolor=COLOR_SURFACE,
        border_radius=12,
        padding=8,
        expand=True
    )

    # -----------------------
    # Progress indicators
    compression_progress = ft.ProgressBar(value=0, width=400, color=COLOR_ACCENT, bgcolor=COLOR_BORDER)
    extraction_progress = ft.ProgressBar(value=0, width=400, color=COLOR_SUCCESS, bgcolor=COLOR_BORDER)
    status_text = ft.Text("Ready", size=14, color=COLOR_TEXT, weight=ft.FontWeight.BOLD)

    # -----------------------
    # Explorer panel (left side)
    explorer_list = ft.ListView(expand=True, spacing=2, padding=5)
    current_explorer_path = None
    
    def reset_explorer_navigation():
        """Reset navigation state when switching sources"""
        nonlocal current_explorer_path, archive_index
        current_explorer_path = None
        archive_index = None
        log("Explorer navigation state reset.", "info")
    
    def load_explorer_folder(folder_path):
        """Load folder contents into explorer, allowing navigation via directory clicks."""
        nonlocal current_explorer_path
        
        if not os.path.isdir(folder_path):
            log(f"Path is not a valid directory: {folder_path}", "error")
            return
            
        current_explorer_path = folder_path
        
        explorer_list.controls.clear()
        explorer_list.controls.append(
            ft.Container(
                content=ft.Row([
                    ft.ProgressRing(width=20, height=20, color=COLOR_ACCENT),
                    ft.Text("Loading directory contents...", color=COLOR_TEXT_MUTED)
                ]),
                padding=10
            )
        )
        safe_update(explorer_list.update)
        
        def load_async():
            try:
                items = []

                # --- Parent Directory (..) Logic ---
                current_path_obj = Path(folder_path).resolve()
                parent_path_obj = current_path_obj.parent
                parent_path = str(parent_path_obj)
                
                if parent_path != folder_path and os.path.isdir(parent_path):
                    items.append((parent_path, "..", True, True))

                # --- Current Directory Contents ---
                dir_contents = os.listdir(folder_path)
                for item in sorted(dir_contents):
                    full_path = os.path.join(folder_path, item)
                    if os.path.isdir(full_path):
                        items.append((full_path, item, True, False))
                    elif os.path.isfile(full_path):
                        items.append((full_path, item, False, False))
            
            except Exception as e:
                log(f"Error reading directory {folder_path}: {e}", "warning")
                items = []

            def update_explorer():
                explorer_list.controls.clear()
                
                path_name = Path(folder_path).name or folder_path
                path_display = path_name if len(path_name) < 50 else "..." + path_name[-47:]
                
                explorer_list.controls.append(
                    ft.Container(
                        content=ft.Text(
                            f"📂 {path_display}",
                            color=COLOR_TEXT_MUTED,
                            size=11,
                            italic=True,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            tooltip=folder_path
                        ),
                        padding=ft.padding.only(left=10, bottom=5),
                        margin=ft.margin.only(bottom=5)
                    )
                )
                
                if not items:
                    explorer_list.controls.append(
                        ft.Container(
                            content=ft.Text("Empty folder or access denied.", color=COLOR_TEXT_MUTED, size=12, italic=True),
                            padding=10
                        )
                    )
                    safe_update(explorer_list.update)
                    return
                
                parents = [(fp, it, is_dir, is_parent) for fp, it, is_dir, is_parent in items if is_parent]
                folders = [(fp, it, is_dir, is_parent) for fp, it, is_dir, is_parent in items if is_dir and not is_parent]
                files = [(fp, it, is_dir, is_parent) for fp, it, is_dir, is_parent in items if not is_dir and not is_parent]
                
                for full_path, item, is_dir, is_parent in parents + folders + files:
                    
                    if is_parent:
                        icon = ft. Icons.ARROW_UPWARD 
                        label = "Go to Parent (..)"
                        icon_color = COLOR_SUCCESS 
                    elif is_dir:
                        icon = ft. Icons.FOLDER
                        label = f"{item}/"
                        icon_color = COLOR_ACCENT
                    else:
                        icon = ft. Icons.INSERT_DRIVE_FILE
                        label = item
                        icon_color = COLOR_TEXT_MUTED

                    def make_click_handler(path, is_directory):
                        def on_click(e):
                            if is_directory:
                                log(f"Navigating to: {path}", "info")
                                load_explorer_folder(path)
                            else:
                                log(f"Selected file: {path}", "info")
                        return on_click
                    
                    tile_container = ft.Container(
                        content=ft.ListTile(
                            title=ft.Text(label, color=COLOR_TEXT, size=13, weight=ft.FontWeight.BOLD if is_parent else ft.FontWeight.NORMAL),
                            leading=ft.Icon(icon, color=icon_color, size=20),
                            on_click=make_click_handler(full_path, is_dir),
                        ),
                        bgcolor=COLOR_SURFACE,
                        border_radius=12,
                        padding=2
                    )
                    explorer_list.controls.append(tile_container)
                
                safe_update(explorer_list.update)
            
            safe_update(update_explorer)
        
        threading.Thread(target=load_async, daemon=True).start()

    def load_explorer_archive(archive_path, virtual_path=""):
        """
        Load archive index and display contents based on the virtual_path, 
        simulating a directory tree.
        """
        nonlocal archive_index
        
        # 1. Load Index if it hasn't been loaded yet
        if archive_index is None:
            explorer_list.controls.clear()
            explorer_list.controls.append(ft.Container(content=ft.Row([ft.ProgressRing(width=20, height=20, color=COLOR_ACCENT), ft.Text("Loading archive index...", color=COLOR_TEXT_MUTED)]), padding=10))
            safe_update(explorer_list.update)
            
            def load_index_async():
                nonlocal archive_index
                try:
                    from core.archive import load_archive_index 
                    archive_index = load_archive_index(archive_path)
                    log(f"Successfully loaded full archive index: {len(archive_index)} files.", "success")
                    safe_update(lambda: load_explorer_archive(archive_path, virtual_path)) # Reload with data
                except Exception as e:
                    error_msg = str(e)
                    log(f"Fatal error loading archive index: {error_msg}", "error")
                    safe_update(lambda: (explorer_list.controls.clear(), explorer_list.controls.append(ft.Text(f"Failed to load archive index. Error: {error_msg}", color=COLOR_ERROR, size=12)), explorer_list.update()))
            
            threading.Thread(target=load_index_async, daemon=True).start()
            return

        # 2. Virtual Directory Parsing Logic (in a separate thread)
        def parse_archive_async():
            contents = {} # {item_name: {'type': 'dir'/'file', 'meta': {...}}
            
            # 2a. Add Parent Directory
            if virtual_path:
                # Strip last segment, handle root case
                parent_dir = str(Path(virtual_path).parent).replace('\\', '/').strip('/')
                if parent_dir == virtual_path.strip('/'): parent_dir = "" # Handle case where parent is root
                contents['..'] = {'type': 'parent', 'path': parent_dir}

            # 2b. Iterate through the entire flat index
            for rel_path, meta in archive_index.items():
                
                # Check if the file/folder belongs to the current virtual_path
                current_prefix = virtual_path.strip('/')
                if not current_prefix:
                    # At root
                    if '/' not in rel_path:
                        # End file at root
                        segment = rel_path
                        contents[segment] = {'type': 'file', 'meta': meta, 'path': rel_path}
                    else:
                        # Folder at root
                        segment = rel_path.split('/')[0]
                        contents[segment] = {'type': 'dir', 'path': segment + '/'}
                elif rel_path.startswith(current_prefix + '/'):
                    
                    remaining_path = rel_path[len(current_prefix):].strip('/')
                    if not remaining_path: continue

                    segment = remaining_path.split('/')[0]
                    full_segment_path = current_prefix + '/' + segment
                    
                    if segment not in contents:
                        if '/' in remaining_path:
                            # Virtual folder
                            contents[segment] = {'type': 'dir', 'path': full_segment_path + '/'}
                        else:
                            # End file
                            contents[segment] = {'type': 'file', 'meta': meta, 'path': full_segment_path}

            # 3. UI Update (Back on the main thread)
            def update_explorer_ui():
                explorer_list.controls.clear()
                method_names = {1: 'DICOM', 2: 'LZMA', 3: 'ZLIB', 4: 'STORE', 5: 'RSF'}

                # Header Display
                path_name = Path(archive_path).name or archive_path
                path_display = path_name if len(path_name) < 50 else "..." + path_name[-47:]
                
                explorer_list.controls.append(
                    ft.Container(
                        content=ft.Text(
                            f"📦 {path_display} / {virtual_path}",
                            color=COLOR_TEXT_MUTED,
                            size=11,
                            italic=True,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            tooltip=f"{archive_path} (Viewing: /{virtual_path})"
                        ),
                        padding=ft.padding.only(left=10, bottom=5),
                        margin=ft.margin.only(bottom=5)
                    )
                )

                sorted_keys = sorted(
                    contents.keys(), 
                    key=lambda k: (0 if contents[k]['type'] == 'parent' else 1 if contents[k]['type'] == 'dir' else 2, k)
                )
                
                for item_name in sorted_keys:
                    item_data = contents[item_name]
                    item_type = item_data['type']
                    
                    is_dir = (item_type == 'dir' or item_type == 'parent')
                    next_path = item_data['path']
                    subtitle = None
                    
                    # --- Determine Icon, Label, and Click Action ---
                    if item_type == 'parent':
                        icon, icon_color, label = ft. Icons.ARROW_UPWARD, COLOR_SUCCESS, "Go to Parent (..)"
                    elif item_type == 'dir':
                        icon, icon_color, label = ft. Icons.FOLDER, COLOR_ACCENT, item_name + '/'
                    else: # file
                        meta = item_data['meta']
                        method_name = method_names.get(meta.get('method', 4), 'GEN')
                        size_str = f"{meta.get('comp_size', 0)/1024:.2f} KB / {meta.get('orig_size', 0)/1024:.2f} KB"

                        icon, icon_color, label = ft. Icons.ARCHIVE, COLOR_TEXT_MUTED, item_name
                        subtitle = ft.Text(f"{method_name} | {size_str}", color=COLOR_TEXT_MUTED, size=10)

                    def make_click_handler(rel_path_in_archive, is_directory):
                        def on_click(e):
                            if is_directory:
                                log(f"Navigating archive to: /{rel_path_in_archive}", "info")
                                load_explorer_archive(archive_path, rel_path_in_archive)
                            else:
                                # --- Extract and Open Logic ---
                                log(f"Attempting to extract and open: {rel_path_in_archive}", "info")
                                
                                def extract_and_open_async():
                                    try:
                                        from core.archive import extract_single
                                        index = archive_index # Index is already loaded

                                        data = extract_single(archive_path, index, rel_path_in_archive)
                                        
                                        # Use only the filename for the temp path to avoid deep nesting in temp dir
                                        temp_out_path = TEMP_DIR / Path(rel_path_in_archive).name
                                        
                                        if temp_out_path.exists(): temp_out_path.unlink()
                                            
                                        with open(temp_out_path, "wb") as f:
                                            f.write(data)
                                            
                                        safe_update(lambda: open_file_in_os(temp_out_path))

                                    except Exception as ex:
                                        log(f"Failed to extract or open file {rel_path_in_archive}: {ex}", "error")

                                threading.Thread(target=extract_and_open_async, daemon=True).start()
                                # -----------------------------------
                        return on_click

                    # --- Create Tile ---
                    tile_container = ft.Container(
                        content=ft.ListTile(
                            title=ft.Text(label, color=COLOR_TEXT, size=13, weight=ft.FontWeight.BOLD if item_type == 'parent' else ft.FontWeight.NORMAL),
                            subtitle=subtitle,
                            leading=ft.Icon(icon, color=icon_color, size=20),
                            on_click=make_click_handler(next_path, is_dir),
                        ),
                        bgcolor=COLOR_SURFACE,
                        border_radius=12,
                        padding=2
                    )
                    explorer_list.controls.append(tile_container)
                
                safe_update(explorer_list.update)

            threading.Thread(target=update_explorer_ui, daemon=True).start()

        threading.Thread(target=parse_archive_async, daemon=True).start()


    # -----------------------
    # Controls panel (middle)
    def update_source_from_field():
        """Update explorer when source field changes or refresh is clicked"""
        nonlocal current_source, is_archive
        path = src_field.value.strip()
        if not path:
            log("Source field is empty. Cannot load explorer.", "warning")
            explorer_list.controls.clear()
            explorer_list.update()
            return
        
        log(f"Attempting to load path: {path}", "info")
        
        if path.lower().endswith('.csa') and os.path.isfile(path):
            is_archive = True
            current_source = path
            reset_explorer_navigation()
            load_explorer_archive(path)
            log(f"Source set to Archive: {path}", "info")
        elif os.path.isdir(path):
            is_archive = False
            current_source = path
            reset_explorer_navigation()
            load_explorer_folder(path)
            log(f"Source set to Folder: {path}", "info")
        elif os.path.isfile(path):
            is_archive = False
            current_source = path
            reset_explorer_navigation()
            load_explorer_folder(str(Path(path).parent))
            log(f"Source set to Single File: {path}", "info")
        else:
            is_archive = False
            current_source = None
            log(f"Invalid path: {path}", "error")
            explorer_list.controls.clear()
            explorer_list.controls.append(
                ft.Text("Invalid source path or file.", color=COLOR_ERROR, size=12, italic=True)
            )
            explorer_list.update()
    
    src_field = ft.TextField(
        label="Source Folder / File / Archive",
        hint_text="Enter path or click Browse",
        expand=True,
        bgcolor=COLOR_SURFACE,
        color=COLOR_TEXT,
        border_color=COLOR_BORDER,
        focused_border_color=COLOR_ACCENT,
        border_radius=12,
        on_submit=lambda e: update_source_from_field(),
        on_blur=lambda e: update_source_from_field(),
        text_size=12,
        content_padding=12
    )
    
    def pick_src_folder(e):
        if is_processing: return
        def picked_folder(e):
            nonlocal current_source, is_archive
            if e.path:
                path = e.path
                safe_update(lambda: (setattr(src_field, 'value', path), src_field.update()))
                is_archive = False
                current_source = path
                reset_explorer_navigation()
                load_explorer_folder(path)
                log(f"Successfully picked source folder: {path}", "success")
            else:
                log("Folder selection cancelled.", "warning")
        
        file_picker_src_folder.on_result = picked_folder
        file_picker_src_folder.get_directory_path(dialog_title="Select Source Folder for Compression")
    
    def pick_src_archive(e):
        if is_processing: return
        def picked_files(e):
            nonlocal current_source, is_archive
            if e.files and len(e.files) > 0:
                path = e.files[0].path
                if path.lower().endswith('.csa') and os.path.isfile(path):
                    safe_update(lambda: (setattr(src_field, 'value', path), src_field.update()))
                    is_archive = True
                    current_source = path
                    reset_explorer_navigation()
                    load_explorer_archive(path)
                    log(f"Successfully picked source archive: {path}", "success")
                else:
                    log(f"Selected file is not a valid .csa archive: {path}", "error")
            else:
                log("Archive selection cancelled.", "warning")
        
        file_picker_src.on_result = picked_files
        file_picker_src.pick_files(
            allow_multiple=False,
            dialog_title="Select Source Archive (.csa) for Extraction",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["csa"]
        )

    browse_folder_btn = ft.IconButton(
        icon=ft. Icons.FOLDER,
        tooltip="Browse Folder (Compress)",
        on_click=pick_src_folder,
        icon_color=COLOR_ACCENT
    )

    browse_archive_btn = ft.IconButton(
        icon=ft. Icons.ARCHIVE,
        tooltip="Browse Archive (Extract)",
        on_click=pick_src_archive,
        icon_color=COLOR_SUCCESS
    )

    refresh_btn = ft.IconButton(
        icon=ft. Icons.REFRESH,
        tooltip="Refresh Explorer",
        on_click=lambda e: update_source_from_field(),
        icon_color=COLOR_TEXT_MUTED
    )

    # Output Controls
    out_field = ft.TextField(
        label="Output Destination",
        hint_text="Click Save As or Select Folder",
        expand=True,
        bgcolor=COLOR_SURFACE,
        color=COLOR_TEXT,
        border_color=COLOR_BORDER,
        focused_border_color=COLOR_ACCENT,
        border_radius=12,
        text_size=12,
        content_padding=12
    )

    def pick_out_file(e):
        if is_processing: return
        def picked_file(e):
            if e.path:
                out_field.value = e.path
                log(f"Output file selected: {e.path}", "info")
            else:
                log("Save As cancelled.", "warning")
            safe_update(out_field.update)
        
        suggested_name = "archive.csa"
        if current_source and not is_archive:
            suggested_name = Path(current_source).name + ".csa"
            
        file_picker_out.on_result = picked_file
        file_picker_out.save_file(
            dialog_title="Save Compressed Archive As",
            file_name=suggested_name,
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["csa"]
        )

    def pick_out_folder(e):
        if is_processing: return
        def picked_folder(e):
            if e.path:
                out_field.value = e.path
                log(f"Output folder selected: {e.path}", "info")
            else:
                log("Folder selection cancelled.", "warning")
            safe_update(out_field.update)
            
        file_picker_extract.on_result = picked_folder
        file_picker_extract.get_directory_path(dialog_title="Select Output Folder for Extraction")

    browse_out_file_btn = ft.IconButton(
        icon=ft. Icons.SAVE_ALT,
        tooltip="Save As (Compress)",
        on_click=pick_out_file,
        icon_color=COLOR_ACCENT
    )

    browse_out_folder_btn = ft.IconButton(
        icon=ft. Icons.FOLDER_OPEN,
        tooltip="Select Folder (Extract)",
        on_click=pick_out_folder,
        icon_color=COLOR_SUCCESS
    )

    # -----------------------
    # Operation Logic

    def update_progress_ui(pct, msg):
        """Callback from worker thread to update UI (progress bar and status)"""
        def update():
            nonlocal is_processing
            
            if is_processing == "compressing":
                msg_prefix = "[COMPRESS] "
                task_progress.color = COLOR_ACCENT
            elif is_processing == "extracting":
                msg_prefix = "[EXTRACT] "
                task_progress.color = COLOR_SUCCESS
            else:
                return
                
            task_progress.value = pct / 100.0
            status_text.value = f"{msg_prefix}{pct}% - {msg}"
            
            page.update()
        
        safe_update(update)
    def on_task_finished(success, final_msg):
        """Callback from worker thread when task is complete or failed"""
        nonlocal is_processing, active_worker
        
        def reset_ui():
            nonlocal is_processing, active_worker
            
            is_processing_type = is_processing
            is_processing = False
            active_worker = None
            
            if is_processing_type == "compressing":
                compression_progress.value = 0.0
                action_btn.text = "Compress"
                action_btn.icon = ft. Icons.ARCHIVE
                action_btn.bgcolor = COLOR_ACCENT
                browse_folder_btn.disabled = False
                browse_archive_btn.disabled = False
                browse_out_file_btn.disabled = False
            elif is_processing_type == "extracting":
                extraction_progress.value = 0.0
                action_btn.text = "Extract"
                action_btn.icon = ft. Icons.FOLDER_OPEN
                action_btn.bgcolor = COLOR_SUCCESS
                browse_folder_btn.disabled = False
                browse_archive_btn.disabled = False
                browse_out_folder_btn.disabled = False
            
            status_text.value = final_msg
            if success:
                log(final_msg, "success")
            else:
                log(final_msg, "error")

            page.update()
            
        safe_update(reset_ui)

    def start_process(e):
        nonlocal is_processing, active_worker, current_source
        
        if is_processing:
            if active_worker:
                active_worker.stop_event.set()
                log("Cancellation requested...", "warning")
            return

        src_path = current_source
        out_path = out_field.value.strip()
        
        if not src_path or not os.path.exists(src_path):
            log("Invalid or non-existent source path.", "error")
            status_text.value = "Error: Invalid source."
            page.update()
            return
            
        if not out_path:
            log("Output destination cannot be empty.", "error")
            status_text.value = "Error: Output needed."
            page.update()
            return
            
        is_processing = "compressing" if not is_archive else "extracting"
        
        action_btn.text = "Cancel"
        action_btn.icon = ft. Icons.CLOSE
        action_btn.bgcolor = COLOR_ERROR
        browse_folder_btn.disabled = True
        browse_archive_btn.disabled = True
        
        if is_processing == "compressing":
            out_file = out_path
            root_dir = src_path
            
            if os.path.isdir(out_file):
                log("Output for compression must be a file (.csa), not a directory.", "error")
                on_task_finished(False, "Error: Output must be a file.")
                return

            active_worker = FletCompressionWorker(root_dir, out_file, update_progress_ui, on_task_finished)
            active_worker.start()
            log(f"Compression started: {root_dir} -> {out_file}", "info")
            browse_out_file_btn.disabled = True
            
        elif is_processing == "extracting":
            archive_path = src_path
            output_dir = out_path

            if not os.path.isdir(output_dir):
                log("Output for extraction must be a folder.", "error")
                on_task_finished(False, "Error: Output must be a folder.")
                return

            active_worker = FletExtractWorker(archive_path, output_dir, update_progress_ui, on_task_finished)
            active_worker.start()
            log(f"Extraction started: {archive_path} -> {output_dir}", "info")
            browse_out_folder_btn.disabled = True
        
        page.update()
        
    action_btn = ft.ElevatedButton(
        text="Compress / Extract",
        icon=ft. Icons.PLAY_ARROW,
        on_click=start_process,
        bgcolor=COLOR_ACCENT,
        color=COLOR_TEXT,
        height=40,
        expand=True
    )
    
    # -----------------------
    # Layout definition

    explorer_card = ft.Card(
        content=ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(ft. Icons.FOLDER_OPEN, color=COLOR_ACCENT, size=20),
                    ft.Text("Explorer", size=18, color=COLOR_TEXT, weight=ft.FontWeight.BOLD)
                ], spacing=8),
                ft.Divider(height=1, color=COLOR_BORDER),
                explorer_list
            ], spacing=8, expand=True),
            padding=12
        ),
        color=COLOR_SURFACE,
        width=380,
        elevation=0
    )

    src_row = ft.Row([src_field, browse_folder_btn, browse_archive_btn, refresh_btn], height=60)
    out_row = ft.Row([out_field, browse_out_file_btn, browse_out_folder_btn], height=60)

    task_progress = ft.ProgressBar(value=0, width=400, color=COLOR_ACCENT, bgcolor=COLOR_BORDER)
    status_text = ft.Text("Ready", size=14, color=COLOR_TEXT, weight=ft.FontWeight.BOLD, width=200) # Give status text a defined width

# Redefine progress_bar
    progress_bar = ft.Card(
     content=ft.Container(
        content=ft.Column([
            ft.Row([
                ft.Icon(ft.Icons.SPEED, color=COLOR_ACCENT),
                ft.Text("Operation Status", size=16, color=COLOR_TEXT, weight=ft.FontWeight.BOLD),
            ], spacing=8),
            ft.Divider(height=1, color=COLOR_BORDER),
            ft.Row([
                status_text,
                task_progress # <--- Only one progress control now
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, expand=True)
        ], spacing=10),
        padding=12
    ),
    color=COLOR_SURFACE,
    elevation=0
)
    controls_card = ft.Card(
        content=ft.Container(
            content=ft.Column([
                ft.Text("Source / Destination", size=18, color=COLOR_TEXT, weight=ft.FontWeight.BOLD),
                ft.Divider(height=1, color=COLOR_BORDER),
                src_row, # Height 60
                out_row, # Height 60
                # --- FINAL CORRECTED BUTTON LAYOUT ---
                ft.Container(
                    content=ft.Row([action_btn], spacing=10),
                    # Add margin to ensure the bottom edge of the button is visible
                    margin=ft.margin.only(top=5, bottom=5) 
                )
                # -------------------------------------
            # Reduced spacing slightly for better fit
            ], spacing=8), 
            padding=12
        ),
        color=COLOR_SURFACE,
        expand=True,
        elevation=0
    )
    
    main_content = ft.Column(
        controls=[
            controls_card,
            progress_bar,
            ft.Text("Log Output", size=18, color=COLOR_TEXT, weight=ft.FontWeight.BOLD),
            log_container
        ],
        expand=True,
        spacing=15
    )

    page.add(
        ft.Row([
            explorer_card,
            main_content
        ], expand=True, spacing=15)
    )

if __name__ == "__main__":
    ft.app(target=main)