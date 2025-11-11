# flet_app.py - RSF Compressor GUI

import flet as ft
from pathlib import Path
import os
import threading
import time 
import traceback
import tempfile
import platform
import logging
MY_PAGE = None
# --- CORE IMPORTS ---
# NOTE: These imports rely on your 'worker.py' file being present.
from worker import CompressionWorker, ExtractWorker
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
            # --- CORRECTED IMPORTS ---
            from core.archive import load_archive_index, extract_archive 
            # -------------------------
            
            # 1. Load Index (this can be slow, but only done once)
            index = load_archive_index(self.archive_path)
            
            # Define a progress wrapper for extraction that includes the cancellation check
            def progress_wrapper(pct, msg):
                if self.stop_event.is_set():
                    raise InterruptedError("Extraction cancelled by user.") 
                # self.progress_cb(pct, msg)

            # 2. Perform Batch Extraction
            count = extract_archive(
                self.archive_path,
                self.output_dir,
                index,
                progress_cb=progress_wrapper
            )
            
            self.finished_cb(True, f"✅ Extracted {count} files")
            
        except InterruptedError:
            self.finished_cb(False, "Extraction cancelled by user.")
        except Exception as e:
            traceback.print_exc()
            # No need to delete extracted files, user can decide
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
COLOR_DEFAULT = "#565450"  

def main(page: ft.Page):
    global MY_PAGE
    MY_PAGE = page
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
    file_picker_add_files = ft.FilePicker(on_result=lambda e: None)  # For adding files to archive
    page.overlay.extend([file_picker_src, file_picker_src_folder, file_picker_out, file_picker_extract, file_picker_add_files])
   
    # Thread-safe UI update helper
    def safe_update(update_fn):
     """Safely update UI using the low-level asyncio thread-safe mechanism."""
     
     # Check if we are ALREADY on the main thread
     if threading.current_thread() == threading.main_thread():
         update_fn()
     else:
         # CRITICAL FIX: Use the standard asyncio method via page.loop.
         # This function schedules the update_fn to run on the main thread's event loop.
         page.loop.call_soon_threadsafe(update_fn)
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
   # Log panel 
    log_entries = []
    log_update_pending = False
    def make_click_handler(full_path: str, is_dir: bool):
       """
       Creates a click event handler function tailored for a specific path.
       
       If the item is a directory (is_dir=True), it loads the explorer for that path.
       If the item is a file (is_dir=False), it triggers the extraction/opening process.
       """
       
       # The actual function that will be executed when the item is clicked
       def click_handler(e):
           global current_source # Assuming you use a global variable to track the current path
           
           # We need to distinguish between opening a new folder (path is a directory)
           # and opening a file (path is a file).
           
           if is_dir:
               # 1. Directory Click: Change the source path and reload the explorer
               current_source = full_path
               # Call the main folder loader function (or a combined async loader)
               safe_update(lambda: setattr(src_field, 'value', full_path)) # Update the source bar
               load_explorer_folder(full_path) 
               
           else:
               # 2. File Click: Trigger the extraction/opening process
               
               # NOTE: Assuming load_async or a similar function is defined 
               # to handle file opening in a background thread.
               # We pass the path as 'e.control.data' or directly to the async function.
               log(f"Attempting to open file: {full_path}", "info")
               
               # This is where your extract_and_open_async logic (or load_async) is triggered.
               # You must modify this call to match how your file opener expects its arguments.
               threading.Thread(target=load_async, args=(e, full_path, True), daemon=True).start()
               
       return click_handler
    def log(msg, level="info"):
        """Add log message (thread-safe, batched updates). PRODUCTION: Reduced verbosity."""
        nonlocal log_update_pending

        # PRODUCTION: Only log errors and warnings to reduce UI noise
        if level == "info":
            return

        level_colors = {
            "success": COLOR_SUCCESS, "error": COLOR_ERROR, "warning": COLOR_WARNING
        }
        timestamp = ft.Text(f"[{level.upper()}] ", size=11, color=level_colors.get(level, COLOR_TEXT_MUTED), weight=ft.FontWeight.BOLD)
        msg_text = ft.Text(msg, size=12, color=COLOR_TEXT, selectable=True)

        log_entries.append(
            ft.Container(
                content=ft.Row([timestamp, msg_text], spacing=5, tight=True),
                padding=ft.padding.only(bottom=4, left=5, right=5)
            )
        )

        if len(log_entries) > 500:  # Reduced from 1000
            log_entries[:] = log_entries[-500:]

        if not log_update_pending:
            log_update_pending = True
            def update_log():
                nonlocal log_update_pending
                log_scroll.controls = log_entries[-200:]  # Show fewer entries
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
    add_files_button = ft.ElevatedButton(
    "Add Files ",
    icon=ft.Icons.ADD,
    on_click=lambda e: pick_files_to_add(),
    bgcolor=COLOR_SUCCESS,
    color=COLOR_TEXT,
    height=40,
    visible=False  # Hidden by default
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
    EXPLORER_COLUMN_SPACING = 4
    EXPLORER_HEADER_PADDING = ft.padding.only(left=10, bottom=6)
    
    # Search field (tight height to reduce gap)
    search_field = ft.TextField(
        hint_text="Search entire index / folder (type to search)...",
        prefix_icon=ft.Icons.SEARCH,
        bgcolor=COLOR_SURFACE,
        color=COLOR_TEXT,
        border_color=COLOR_BORDER,
        focused_border_color=COLOR_ACCENT,
        border_radius=8,
        dense=True,
        height=40,
        text_size=12,
        content_padding=8,
        on_change=lambda e: threading.Thread(target=perform_search, args=(e.control.value,), daemon=True).start()
    )
    
    # A small cache to hold "current explorer view" — not used for searching but for quick reloads
    explorer_cache = []

    def reset_explorer_navigation():
        """Reset navigation state when switching sources"""
        nonlocal current_explorer_path, archive_index
        current_explorer_path = None
        archive_index = None
        log("Explorer navigation state reset.", "info")
    def pick_files_to_add():
        """Pick files to add to the current archive"""
        if not is_archive or not current_source:
            log("No archive loaded to add files to", "warning")
            return

        def picked_files(e):
            if e.files and len(e.files) > 0:
                file_paths = [f.path for f in e.files]
                log(f"Selected {len(file_paths)} files to add", "success")
                add_files_to_current_archive(file_paths)
            else:
                log("File selection cancelled", "warning")

        file_picker_add_files.on_result = picked_files
        file_picker_add_files.pick_files(
            allow_multiple=True,
            dialog_title="Select Files to Add to Archive",
            file_type=ft.FilePickerFileType.ANY
        )

    def add_files_to_current_archive(file_paths):
        """Add selected files to the current archive"""
        if not is_archive or not current_source:
            log("No archive loaded", "error")
            return

        def progress_callback(current, total, message):
            safe_update(lambda: (setattr(status_text, 'value', f"Adding files: {message}"), status_text.update()))

        def add_files_async():
            try:
                from core.archive import add_files_to_archive
                from core.compressor_core import compress_file_core
                added_count = add_files_to_archive(current_source, file_paths, progress_callback, compress_file_core)

                if added_count > 0:
                    # Reload the archive index and explorer
                    nonlocal archive_index
                    from core.archive import load_archive_index
                    archive_index = load_archive_index(current_source)
                    safe_update(lambda: load_explorer_archive(current_source))
                    log(f"Successfully added {added_count} files to archive", "success")
                else:
                    log("No files were added to archive", "warning")

            except Exception as ex:
                log(f"Failed to add files to archive: {ex}", "error")
            finally:
                safe_update(lambda: (setattr(status_text, 'value', "Ready"), status_text.update()))

        threading.Thread(target=add_files_async, daemon=True).start()
    def load_explorer_folder(path):
       """
       Loads folder contents into the explorer.
       PRODUCTION: Simplified error handling.
       """
       path_obj = Path(path)
       if not path_obj.is_dir():
           log(f"Path is not a valid directory: {path}", "error")
           explorer_list.controls.clear()
           explorer_list.update()
           return
   
       # --- STEP 1: CLEAR EXISTING DATA ---
       log(f"Loading folder contents from: {path}", "info")
       explorer_list.controls.clear()
       
       # --- Directory Navigation Logic (Up Folder) ---
       if path_obj.parent != path_obj:
           # Define the '..' item (Up Folder)
           up_path = str(path_obj.parent)
           up_icon = ft.Icons.ARROW_UPWARD
           
           # NOTE: Assumes 'make_click_handler' is defined elsewhere to handle navigation
           up_row_content = ft.Row(
                controls=[
                    ft.Icon(up_icon, color=COLOR_TEXT_MUTED, size=16),
                    ft.Text(".. (Go Up)", color=COLOR_TEXT_MUTED, size=12, expand=True),
                ],
                spacing=5,
                height=25,
                # NOTE: Row does not have on_click
            )
# Wrap the Row content in a Container to make it clickable
           up_container = ft.Container(
               content=up_row_content,
               padding=ft.padding.only(left=5, right=5), # Optional: Add padding for click target
               on_click=make_click_handler(up_path, is_dir=True), # Attach handler to the Container
           )

       # --- Loop through contents ---
       try:
           items = os.listdir(path)
           
           # Sort folders before files, then alphabetically
           sorted_items = sorted(items, key=lambda x: (not Path(path, x).is_dir(), x.lower()))
           
           for item_name in sorted_items:
               item_path = path_obj / item_name
               
               # Skip hidden files or files/folders starting with '.'
               if item_name.startswith('.'):
                   continue
   
               is_dir = item_path.is_dir()
               
               if is_dir:
                   icon = ft.Icons.FOLDER
                   color = COLOR_ACCENT
               else:
                   icon = ft.Icons.INSERT_DRIVE_FILE
                   color = COLOR_TEXT
   
               # Define the item's visual row
               item_row_content = ft.Row(
                      controls=[
                          ft.Icon(icon, color=color, size=16),
                          ft.Text(item_name, color=COLOR_TEXT, size=12, expand=True),
                      ],
                      spacing=5,
                      height=25,
                      # NOTE: on_click is omitted here!
                  )

# Wrap the Row content in a Container to make it clickable
               item_container = ft.Container(
                   content=item_row_content,
                   padding=ft.padding.only(left=5, right=5), # Optional padding
                   on_click=make_click_handler(str(item_path), is_dir=is_dir), # Attach handler to the Container
               )
               

       except Exception as e:
           log(f"Failed to read directory {path}: {e}", "error")
           explorer_list.controls.append(
               ft.Text(f"Error reading directory: {e}", color=COLOR_ERROR, size=12)
           )
   
       # --- STEP 3: APPLY FILTERING AND UPDATE UI ---
       
       # Reset the search field value
       safe_update(lambda: setattr(search_field, 'value', ""))
       
       # 🎯 Apply the filter (passing "" shows all items now stored in current_explorer_items)
   
       # NOTE: The update of explorer_list is now handled by filter_explorer_list
    def build_match_tile(label, subtitle, icon, icon_color, click_handler):
      return ft.Container(
          content=ft.ListTile(
              title=ft.Text(label, color=COLOR_TEXT, size=13),
              subtitle=ft.Text(subtitle, color=COLOR_TEXT_MUTED, size=10) if subtitle else None,
              leading=ft.Icon(icon, color=icon_color, size=18),
              on_click=click_handler
          ),
          bgcolor=COLOR_SURFACE,
          border_radius=10,
          padding=6,
          margin=ft.margin.only(bottom=4)
      )
    def perform_search(query: str):
     """
     Search whole archive index (if archive loaded) or walk filesystem (if folder loaded).
     This runs in a background thread and updates the explorer on the main thread.
     """
     query = (query or "").strip().lower()
     # If query is empty -> restore current view
     if not query:
         # restore the current view by calling existing loaders (run on main thread)
         safe_update(lambda: (search_field.update(), update_source_from_field()))
         return
 
     # Show inline searching indicator immediately
     def show_searching_ui():
        explorer_list.controls.clear()
        explorer_list.controls.append(
            ft.Container(
                content=ft.Row([
                    ft.ProgressRing(width=18, height=18, color=COLOR_ACCENT),
                    ft.Text(f"Searching for '{query}'...", color=COLOR_TEXT_MUTED, size=12)
                ], spacing=8),
                padding=8
            )
        )
        explorer_list.update()
     safe_update(show_searching_ui)

     results = []
     MAX_RESULTS = 1000  # adjust if you like
 
     try:
         if is_archive and archive_index is not None:
             # Walk the archive index (flat dict mapping rel_path -> meta)
             # We match against the whole path and filename
             for rel_path, meta in archive_index.items():
                 if len(results) >= MAX_RESULTS:
                     break
                 low = rel_path.lower()
                 if query in low:
                     # match found
                     method_names = {1: 'DICOM', 2: 'LZMA', 3: 'ZLIB', 4: 'STORE', 5: 'RSF'}
                     method_name = method_names.get(meta.get('method', 4), 'GEN')
                     size_str = f"{meta.get('comp_size',0)/1024:.1f} KB / {meta.get('orig_size',0)/1024:.1f} KB"
                     results.append({
                         "label": Path(rel_path).name,
                         "subtitle": f"/{rel_path}  •  {method_name} • {size_str}",
                         "rel_path": rel_path,
                         "is_dir": rel_path.endswith("/"),  # in archive index files are paths; dirs handled by virtualization
                         "icon": ft.Icons.ARCHIVE,
                         "icon_color": COLOR_TEXT_MUTED
                     })
 
         elif current_source and os.path.exists(current_source):
             # Folder mode: walk the entire tree beneath current_source
             # We will search filenames and relative paths
             root = current_source if os.path.isdir(current_source) else str(Path(current_source).parent)
             count = 0
             for dirpath, dirnames, filenames in os.walk(root):
                 # check directories
                 for d in dirnames:
                     full = os.path.join(dirpath, d)
                     rel = os.path.relpath(full, root).replace("\\", "/")
                     if query in rel.lower() or query in d.lower():
                         results.append({
                             "label": d + "/",
                             "subtitle": f"/{rel}/",
                             "full_path": full,
                             "is_dir": True,
                             "icon": ft.Icons.FOLDER,
                             "icon_color": COLOR_ACCENT
                         })
                         count += 1
                         if count >= MAX_RESULTS:
                             break
                 if count >= MAX_RESULTS:
                     break
                 # check files
                 for f in filenames:
                     full = os.path.join(dirpath, f)
                     rel = os.path.relpath(full, root).replace("\\", "/")
                     if query in rel.lower() or query in f.lower():
                         results.append({
                             "label": f,
                             "subtitle": f"/{rel}",
                             "full_path": full,
                             "is_dir": False,
                             "icon": ft.Icons.INSERT_DRIVE_FILE,
                             "icon_color": COLOR_TEXT_MUTED
                         })
                         count += 1
                         if count >= MAX_RESULTS:
                             break
                 if count >= MAX_RESULTS:
                     break
 
         else:
             # nothing to search
             pass
 
     except Exception as e:
         # If something goes wrong, show error in UI
         safe_update(lambda: (explorer_list.controls.clear(), explorer_list.controls.append(ft.Text(f"Search failed: {e}", color=COLOR_ERROR)), explorer_list.update()))
         return

    # Build UI tiles from results and publish on main thread
     def show_results():
         explorer_list.controls.clear()
         if not results:
             explorer_list.controls.append(ft.Text(f"No matches for '{query}'.", color=COLOR_TEXT_MUTED, size=12, italic=True))
             explorer_list.update()
             return
 
         # Header showing count and a "Clear" small button
         header_row = ft.Row([
             ft.Text(f"Search results ({len(results)}): '{query}'", color=COLOR_TEXT_MUTED, size=11, italic=True),
             ft.ElevatedButton("Clear", width=70, height=32, on_click=lambda e: safe_update(lambda: (setattr(search_field, 'value', ''), search_field.update(), update_source_from_field())))
         ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
         explorer_list.controls.append(ft.Container(content=header_row, padding=EXPLORER_HEADER_PADDING))
 
         # Create tiles
         for res in results:
             if is_archive and archive_index is not None:
                 # Archive search results
                 rel = res["rel_path"]
                 is_dir = res.get("is_dir", False)
                 def make_click_handler_archive(rel_path):
                     def on_click(e):
                         if rel_path.endswith("/"):
                             # Navigate to virtual directory
                             load_explorer_archive(current_source, rel_path.strip("/"))
                         else:
                             # Extract and open file
                             def extract_and_open_async():
                                 try:
                                     from core.archive import extract_single
                                     from core.compressor_core import decompress_file_core

                                     file_meta_data = archive_index.get(rel_path)
                                     if not isinstance(file_meta_data, dict):
                                         raise TypeError(f"Index for '{rel_path}' is corrupted")

                                     data = extract_single(
                                         current_source, rel_path, file_meta_data,
                                         TEMP_DIR, decompress_file_core
                                     )

                                     if not data:
                                         raise RuntimeError(f"Extraction returned empty data for {rel_path}")

                                     temp_out_path = TEMP_DIR / rel_path
                                     temp_out_path.parent.mkdir(parents=True, exist_ok=True)

                                     with open(temp_out_path, "wb") as f:
                                         f.write(data)

                                     if not temp_out_path.exists():
                                         raise RuntimeError(f"File was not created at {temp_out_path}")

                                     log(f"Successfully extracted {rel_path} from search", "success")
                                     safe_update(lambda: open_file_in_os(temp_out_path))

                                 except Exception as ex:
                                     log(f"Failed to extract file {rel_path}: {ex}", "error")

                             threading.Thread(target=extract_and_open_async, daemon=True).start()
                     return on_click

                 tile = build_match_tile(res["label"], res["subtitle"], res["icon"], res["icon_color"], make_click_handler_archive(rel))

             else:
                 # Folder search results
                 full = res.get("full_path")
                 is_dir = res.get("is_dir", False)
                 def make_click_handler_fs(path, is_dir_flag):
                     def on_click(e):
                         if is_dir_flag:
                             load_explorer_folder(path)
                         else:
                             # Open file directly
                             safe_update(lambda: open_file_in_os(path))
                     return on_click
                 tile = build_match_tile(res["label"], res["subtitle"], res["icon"], res["icon_color"], make_click_handler_fs(full, is_dir))
 
             explorer_list.controls.append(tile)
 
         explorer_list.update()
 
     safe_update(show_results)
    def load_async():
            folder_path = None
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
                if folder_path is None:
                  log("Cannot update explorer: Source path is not set.", "warning")
                  # Optionally clear the explorer or display a message
                  explorer_list.controls.clear()
                  explorer_list.update()
                  return
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
                                       # Import extraction function
                                       from core.archive import extract_single
                                       from core.compressor_core import decompress_file_core
                                       
                                       index = archive_index # Index is already loaded
                                       
                                       # Get the file's metadata from the index
                                       file_meta_data = index.get(rel_path_in_archive)
                                        
                                       if not isinstance(file_meta_data, dict):
                                            raise TypeError(f"Index for '{rel_path_in_archive}' is corrupted. Expected dict, got: {type(file_meta_data).__name__}")
                                        
                                       # Extract the file data (extract_single returns bytes)
                                       data = extract_single(
                                           archive_path,
                                           rel_path_in_archive,
                                           file_meta_data,
                                           TEMP_DIR,
                                           decompress_file_core
                                       )
                                       
                                       if not data:
                                           raise RuntimeError(f"Extraction returned empty data for {rel_path_in_archive}")
                                       
                                       # Write the extracted file to temp directory
                                       temp_out_path = TEMP_DIR / rel_path_in_archive
                                       temp_out_path.parent.mkdir(parents=True, exist_ok=True)
                                       
                                       with open(temp_out_path, "wb") as f:
                                           f.write(data)
                                       
                                       # Verify the file was written correctly
                                       if not temp_out_path.exists():
                                           raise RuntimeError(f"File was not created at {temp_out_path}")
                                       
                                       written_size = temp_out_path.stat().st_size
                                       if written_size != len(data):
                                           log(f"Warning: File size mismatch for {rel_path_in_archive}: expected {len(data)} bytes, got {written_size} bytes", "warning")
                                       
                                       log(f"Successfully extracted {rel_path_in_archive} ({written_size} bytes)", "success")
                                       
                                       # Open the file in the OS default application
                                       safe_update(lambda: open_file_in_os(temp_out_path))
                                          
                                   except Exception as ex:
                                       import traceback
                                       full_error = f"{ex}\n{traceback.format_exc()}"
                                       log(f"Failed to extract or open file {rel_path_in_archive}: {full_error}", "error")

                                threading.Thread(target=extract_and_open_async, daemon=True).start()
# ...
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
        
        if path.lower().endswith('.csa') and os.path.isfile(path):
            is_archive = True
            current_source = path
            reset_explorer_navigation()
            load_explorer_archive(path)
            log(f"Source set to Archive: {path}", "success")
        elif os.path.isdir(path):
            is_archive = False
            current_source = path
            reset_explorer_navigation()
            load_explorer_folder(path)
            log(f"Source set to Folder: {path}", "success")
        elif os.path.isfile(path):
            is_archive = False
            current_source = path
            reset_explorer_navigation()
            load_explorer_folder(str(Path(path).parent))
            log(f"Source set to Single File: {path}", "success")
        else:
            is_archive = False
            current_source = None
            log(f"Invalid path: {path}", "error")
            explorer_list.controls.clear()
            explorer_list.controls.append(
                ft.Text("Invalid source path or file.", color=COLOR_ERROR, size=12, italic=True)
            )
            explorer_list.update()
        # --- NEW: UI Update based on Source Type ---
        if path.lower().endswith('.csa') and os.path.isfile(path):
            is_archive = True
            # ... (load_explorer_archive logic) ...
            
            # Set UI for Extraction
            action_btn.text = "Extract"
            action_btn.icon = ft.Icons.FOLDER_OPEN
            action_btn.bgcolor = COLOR_SUCCESS
            browse_out_file_btn.disabled = True
            browse_out_folder_btn.disabled = False
            out_field.label = "Extraction Output Folder"

        elif os.path.isdir(path):
            is_archive = False
            # ... (load_explorer_folder logic) ...
            
            # Set UI for Compression
            action_btn.text = "Compress"
            action_btn.icon = ft.Icons.ARCHIVE
            action_btn.bgcolor = COLOR_ACCENT
            browse_out_file_btn.disabled = False
            browse_out_folder_btn.disabled = True
            out_field.label = "Output Archive Path (.csa)"
            
        else:
            # ... (Invalid path logic) ...
            is_archive = False
            current_source = None
            action_btn.text = "Compress / Extract"
            action_btn.icon = ft.Icons.PLAY_ARROW
            action_btn.bgcolor = COLOR_ACCENT
            browse_out_file_btn.disabled = True
            browse_out_folder_btn.disabled = True
            out_field.label = "Output Destination"
            
        action_btn.update()
        out_field.update()
        browse_out_file_btn.update()
        browse_out_folder_btn.update()
        add_files_button.visible = is_archive
        add_files_button.update()
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
                update_source_from_field()
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
                    update_source_from_field()
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
     # This is guaranteed to run on the main thread by page.run_thread
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
     
     # Explicit updates are essential and now safe
     task_progress.update() 
     status_text.update()
     # These are now executed directly on the main thread
     task_progress.update() 
     status_text.update()
    def on_task_finished(success, final_msg):
     """Callback from worker thread when task is complete or failed"""
     nonlocal is_processing, active_worker
     
     # This entire cleanup block must be run thread-safely for the final update
     def reset_ui():
         nonlocal is_processing, active_worker
         
         is_processing_type = is_processing
         is_processing = False
         active_worker = None
         
         # UI resets based on task type
         if is_processing_type == "compressing":
             # Assuming 'task_progress' is used, otherwise use 'compression_progress'
             task_progress.value = 0.0 
             action_btn.text = "Compress"
             action_btn.icon = ft. Icons.ARCHIVE
             action_btn.bgcolor = COLOR_ACCENT
             browse_folder_btn.disabled = False
             browse_archive_btn.disabled = False
             browse_out_file_btn.disabled = False
         elif is_processing_type == "extracting":
             task_progress.value = 0.0
             action_btn.text = "Extract"
             action_btn.icon = ft. Icons.FOLDER_OPEN
             action_btn.bgcolor = COLOR_SUCCESS
             browse_folder_btn.disabled = False
             browse_archive_btn.disabled = False
             browse_out_folder_btn.disabled = False
             
         status_text.value = final_msg
         
         if success:
             status_text.color = COLOR_DEFAULT
             log(final_msg, "success")
         else:
             status_text.color = COLOR_ERROR
             log(final_msg, "error")
 
         # Update the page once for the final state change
         page.update() 
         
     # CRITICAL: Launch the final UI reset onto the main thread
     # Since run_thread is now working, we can trust it.
     page.run_thread(reset_ui)
    def start_process(e):
        nonlocal is_processing, active_worker, current_source
        
        if is_processing:
         if active_worker and hasattr(active_worker, 'stop_event'):
            active_worker.stop_event.set() # Set the stop event
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
        page.update()
        if is_processing == "compressing":
            out_file = out_path
            root_dir = src_path
            
            if Path(out_file).is_dir():
                log("Output for compression must be a file (.csa), not a directory.", "error")
                on_task_finished(False, "Error: Output must be a file.")
                return

            active_worker = FletCompressionWorker(src_path, out_path, update_progress_ui, on_task_finished)
            # active_worker.start()
            log(f"Compression started: {root_dir} -> {out_file}", "success")
            browse_out_file_btn.disabled = True
            page.run_thread(active_worker.run_process)
        elif is_processing == "extracting":
            archive_path = src_path
            output_dir = out_path

            if not os.path.isdir(output_dir):
                log("Output for extraction must be a folder.", "error")
                on_task_finished(False, "Error: Output must be a folder.")
                return

            active_worker = FletExtractWorker(src_path, out_path, update_progress_ui, on_task_finished)
            log(f"Extraction started: {archive_path} -> {output_dir}", "success")
            browse_out_folder_btn.disabled = True
            page.run_thread(active_worker.run_process)
        
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
            # header row
            ft.Row([
                ft.Icon(ft.Icons.FOLDER_OPEN, color=COLOR_ACCENT, size=20),
                ft.Text("Explorer", size=18, color=COLOR_TEXT, weight=ft.FontWeight.BOLD)
            ], spacing=8),
            ft.Divider(height=1, color=COLOR_BORDER),
            # INSERT SEARCH FIELD HERE (compact, no giant gap)
            search_field,
            # explorer content (list)
            explorer_list,
            ft.Divider(height=1, color=COLOR_BORDER),
            add_files_button
        ], spacing=6, expand=True),  # tightened spacing to reduce large gaps
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
    update_source_from_field() 
if __name__ == "__main__":
    ft.app(target=main)
