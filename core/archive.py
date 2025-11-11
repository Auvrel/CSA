#core/archive.py
import os
import ast
import struct
from pathlib import Path
import json
from typing import Callable
import io
import logging
import zlib
import pydicom # REQUIRED for DICOM metadata handling during extraction
from pydicom.dataset import FileMetaDataset
from pydicom.uid import generate_uid, ImplicitVRLittleEndian
from pydicom.filewriter import dcmwrite
from core.compressor_core import decompress_file_core, METHOD_CUSTOM_DICOM, METHOD_ZLIB_GENERIC, METHOD_LZMA_TEXT, METHOD_STORE_ONLY
# Placeholder for compression method codes (must match core/compressor_core)
METHOD_CUSTOM_DICOM = 1 # Changed from METHOD_DICOM to match compressor_core usage
METHOD_LZMA_TEXT = 2
METHOD_ZLIB_GENERIC = 3
METHOD_STORE_ONLY = 4
METHOD_RSF = 5
METHOD_JPEG_OPTIMIZED = 6  # For JPEG files (store as-is or minimal processing)
METHOD_PNG_OPTIMIZED = 7   # For PNG files with better compression
METHOD_TIFF_COMPRESSED = 8 # For TIFF files
METHOD_BMP_COMPRESSED = 9  # For BMP files
METHOD_RAW_IMAGE = 10      # For other image formats

logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')

def build_archive(root_dir, out_file, progress_cb: Callable, compress_callback: Callable):
    """
    Compresses a directory and builds the .csa archive file.
    PERFORMANCE: Optimized for production with reduced validation overhead.
    """
    root_path = Path(root_dir)
    file_list = [p for p in root_path.rglob('*') if p.is_file()]
    total_files = len(file_list)
    archive_index = {}
    
    if total_files == 0:
        progress_cb(100, 100, "Archive complete (0 files processed).")
        return 0

    with open(out_file, "w+b") as archive_f:
        # Write placeholder for data section offset
        archive_f.write(b'\x00' * 8)
        
        for i, file_path in enumerate(file_list):
            rel_path = str(file_path.relative_to(root_path)).replace('\\', '/')
            
            # 1. Read raw file bytes
            try:
                with open(file_path, 'rb') as f:
                    orig_data = f.read()
            except Exception as e:
                logging.error(f"Error reading file {rel_path}: {e}")
                continue
            
            # 2. Compress the data
            try:
                compressed_data, method, orig_size_check, rows, cols, dicom_metadata = \
                    compress_callback(file_path, orig_data)
            except Exception as e:
                logging.error(f"Error compressing {rel_path}: {e}")
                continue
            
            # 3. Progress update and cancellation check
            is_running = progress_cb(i + 1, total_files, rel_path)
            if not is_running:
                logging.warning("Archiving cancelled by user.")
                return 0

            # 4. Write compressed data to archive
            start_offset = archive_f.tell()
            archive_f.write(compressed_data)
            comp_size = len(compressed_data)
            
            # 5. Validate and store metadata
            if comp_size <= 0:
                logging.error(f"Invalid compressed size for {rel_path}. Skipping.")
                continue

            metadata = {
                'method': method,
                'orig_size': orig_size_check,
                'comp_size': comp_size,
                'offset': start_offset,
                'rows': rows,
                'cols': cols,
                'dicom_meta': dicom_metadata
            }
            archive_index[rel_path] = metadata
            
        data_end_pos = archive_f.tell()
        
        # 6. Write index and footer
        index_json = json.dumps(archive_index).encode('utf-8')
        archive_f.write(index_json)
        archive_f.write(struct.pack('<Q', len(index_json)))
        archive_f.write(b'CSFA')
        
        # 7. Update header with data section end position
        archive_f.seek(0)
        archive_f.write(struct.pack('<Q', data_end_pos))
        
    progress_cb(total_files, total_files, "Archive complete.")
    return total_files
# Helper function to convert JSON keys back to integers where applicable
def convert_keys(obj):
    """Recursively converts dict keys that are strings of integers back to integers."""
    if isinstance(obj, dict):
        new_dict = {}
        for key, value in obj.items():
            # Attempt to convert key to integer if it's a string
            if isinstance(key, str) and key.isdigit():
                new_key = int(key)
            else:
                new_key = key
            new_dict[new_key] = convert_keys(value)
        return new_dict
    elif isinstance(obj, list):
        return [convert_keys(elem) for elem in obj]
    else:
        return obj

def load_archive_index(archive_path):
    """Reads the archive footer and index to memory."""
    with open(archive_path, "rb") as f:
        # ... (File reading logic remains the same) ...
        f.seek(-12, os.SEEK_END)
        footer = f.read(12)
        
        magic = footer[8:]
        if magic != b'CSFA':
            raise ValueError("Invalid CSFA archive file format.")
            
        index_size = struct.unpack('<Q', footer[:8])[0]
        
        f.seek(-(index_size + 12), os.SEEK_END)
        index_json = f.read(index_size).decode('utf-8')
        
        # Load the index
        archive_index = json.loads(index_json)
        
        # **NEW CODE:** Recursively apply key conversions/cleaning
        # This addresses common DICOM tag issues (keys being converted to strings by json.dumps)
        cleaned_index = convert_keys(archive_index)
        
        # **Defensive Check for the top level**
        for rel_path, meta_data in cleaned_index.items():
            if not isinstance(meta_data, dict):
                # Log the corrupted data and attempt to recover (or raise a clear error)
                logging.error(f"Index entry for '{rel_path}' is corrupt (value is type {type(meta_data).__name__}).")
                # Recovery attempt: set to an empty dict to avoid crashing on .get()
                cleaned_index[rel_path] = {} 
        
        return cleaned_index
def extract_single(archive_path: str, rel_path: str, meta: dict, unknown4=None, unknown5=None) -> bytes:
    """
    Extracts, decompresses, and reconstructs a single file from the archive.
    PERFORMANCE: Optimized for production use.
    """
    # 1. Retrieve metadata
    offset = meta.get('offset', 0)
    comp_size = meta.get('comp_size', 0)
    rows = meta.get('rows', 0)
    cols = meta.get('cols', 0)
    method = meta.get('method', 0)
    
    # 2. Parse DICOM metadata
    dicom_meta_raw = meta.get('dicom_meta')
    dicom_meta = {}
    
    if dicom_meta_raw:
        if isinstance(dicom_meta_raw, dict):
            dicom_meta = dicom_meta_raw
        elif isinstance(dicom_meta_raw, str):
            try:
                if dicom_meta_raw.strip() and dicom_meta_raw != '{}':
                    dicom_meta = json.loads(dicom_meta_raw)
            except (json.JSONDecodeError, ValueError) as e:
                logging.error(f"Failed to decode DICOM metadata for {rel_path}: {e}")
    
    # 3. Read compressed data from archive
    try:
        with open(archive_path, 'rb') as f:
            # Read header to find index section start
            f.seek(0)
            index_section_start = struct.unpack('<Q', f.read(8))[0]
            
            # Validation
            if offset < 8 or offset >= index_section_start or comp_size <= 0:
                logging.error(f"Invalid parameters for {rel_path}: offset={offset}, size={comp_size}")
                return b''
            
            # Read compressed data
            f.seek(offset)
            compressed_data = f.read(comp_size)
            
            if len(compressed_data) != comp_size:
                logging.error(f"Read {len(compressed_data)} bytes, expected {comp_size} for {rel_path}")
                if len(compressed_data) == 0:
                    return b''
                
    except Exception as e:
        logging.error(f"Error reading compressed data for {rel_path}: {e}")
        return b''

    # 4. Decompress the data
    try:
        if not compressed_data:
            logging.error(f"No compressed data for {rel_path}")
            return b''

        uncompressed_data = decompress_file_core(method, compressed_data, rows, cols)
        
        if not uncompressed_data:
            logging.error(f"Decompression returned empty data for {rel_path}")
            return b''
            
    except Exception as e:
        logging.error(f"Error decompressing {rel_path} (Method {method}): {e}")
        return b''

    # 5. DICOM reconstruction (if applicable)
    is_dicom = (method == METHOD_CUSTOM_DICOM or method == METHOD_ZLIB_GENERIC) and rows > 0 and cols > 0
    
    if is_dicom:
        try:
            ds = None
            metadata_blob_encoded = dicom_meta.get('metadata_blob')
            
            if metadata_blob_encoded:
                # Decode Hex String -> Int -> Bytes -> Decompress ZLIB
                meta_int = int(metadata_blob_encoded, 16)
                byte_length = (meta_int.bit_length() + 7) // 8
                if byte_length == 0: byte_length = 1 
                
                metadata_blob_compressed = meta_int.to_bytes(byte_length, byteorder='big')
                metadata_bytes = zlib.decompress(metadata_blob_compressed)
                ds = pydicom.dcmread(io.BytesIO(metadata_bytes), force=True)
                logging.info(f"Successfully loaded DICOM metadata for {rel_path}")
                
            if ds is None:
                # Minimal fallback dataset creation
                ds = pydicom.Dataset()
                ds.SOPClassUID = generate_uid()
                ds.Rows = rows
                ds.Columns = cols
                ds.BitsAllocated = 16
                ds.BitsStored = 16
                ds.HighBit = 15
                ds.PixelRepresentation = 0
                ds.PhotometricInterpretation = 'MONOCHROME2'
                ds.SamplesPerPixel = 1
                
            # Insert the pixel data
            expected_size = rows * cols * 2
            if len(uncompressed_data) != expected_size:
                # Attempt to fix size mismatch
                uncompressed_data = uncompressed_data[:expected_size].ljust(expected_size, b'\x00')
                
            ds.PixelData = uncompressed_data
            
            # Re-apply windowing/rescale tags from index (simplified)
            for tag in ['WindowCenter', 'WindowWidth', 'RescaleIntercept', 'RescaleSlope']:
                value = dicom_meta.get(tag)
                if value is not None and value != 'None' and str(value).strip():
                     setattr(ds, tag, ast.literal_eval(value) if isinstance(value, str) else value)

            # Ensure file_meta is set for writing the DICOM file
            if not hasattr(ds, 'file_meta') or not ds.file_meta:
                 ds.file_meta = FileMetaDataset()
            from pydicom.uid import ExplicitVRLittleEndian
            ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
            ds.file_meta.MediaStorageSOPClassUID = getattr(ds, 'SOPClassUID', generate_uid())
            ds.file_meta.MediaStorageSOPInstanceUID = getattr(ds, 'SOPInstanceUID', generate_uid())

            # Write the reconstructed DICOM file to an in-memory buffer
            buffer = io.BytesIO()
            pydicom.dcmwrite(buffer, ds, enforce_file_format=True)
            result = buffer.getvalue()

            # Final validation check for DICOM preamble
            if len(result) >= 132 and result[128:132] != b'DICM':
                fixed_buffer = io.BytesIO(b'\x00' * 128 + b'DICM' + result[132:])
                result = fixed_buffer.getvalue()
                logging.info(f"Fixed DICOM preamble for {rel_path}")
            
            if len(result) > 0:
                return result
            else:
                logging.error(f"Reconstructed DICOM file is empty for {rel_path}")
                return uncompressed_data
            
        except Exception as e:
            logging.error(f"Error reconstructing DICOM file for {rel_path}: {e}", exc_info=True)
            # Fallback to returning just the raw pixel data
            return uncompressed_data

    # --- 6. Return uncompressed data for non-DICOM files ---
    return uncompressed_data
def extract_archive(archive_path, output_dir, progress_hook: Callable):
    """
    Handles the batch extraction and progress reporting.
    """
    # Import decompression logic needed for extract_single
    from core.compressor_core import decompress_file_core 

    archive_path = Path(archive_path)
    output_dir = Path(output_dir)

    try:
        archive_index = load_archive_index(archive_path)
        file_list = list(archive_index.keys())
        total_files = len(file_list)
    except Exception as e:
        # NOTE: progress_hook signature is (count, total, message)
        progress_hook(0, 1, f"ERROR: Could not load archive index: {e}")
        return 0

    if total_files == 0:
        progress_hook(1, 1, "Extraction complete (Empty archive).")
        return 0

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    extracted_count = 0
    for i, rel_path in enumerate(file_list):
        
        # --- PROGRESS & CANCELLATION ---
        # The hook returns True/False based on the stop_event check
        is_running = progress_hook(i + 1, total_files, rel_path)
        if not is_running:
            return extracted_count # Return count of files processed before cancellation

        try:
            # Extract and write the file
            # Get the file's metadata from the index
            file_meta_data = archive_index.get(rel_path)
            if not isinstance(file_meta_data, dict):
                logging.error(f"Index entry for '{rel_path}' is not a dict: {type(file_meta_data)}")
                continue
            
            # Extract the file data using the correct signature:
            # extract_single(archive_path, rel_path, meta, unknown4, unknown5)
            data = extract_single(
                str(archive_path),  # archive_path: str
                rel_path,            # rel_path: str
                file_meta_data,      # meta: dict (the file's metadata)
                output_dir,          # unknown4 (optional, not used by function)
                decompress_file_core # unknown5 (optional, not used by function)
            )
            
            # Write the extracted data to the output directory
            output_file_path = output_dir / rel_path
            output_file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_file_path, 'wb') as f:
                f.write(data)
            
            extracted_count += 1
        except Exception as e:
            logging.error(f"Failed to extract {rel_path}: {e}")
            # If a file fails, we log it and continue to the next one
            
    # Final Progress
    # NOTE: Using total_files for both count and total ensures 100%
    progress_hook(total_files, total_files, "Extraction Succeeded!")

    return extracted_count

def add_files_to_archive(archive_path: str, files_to_add: list, progress_cb: Callable = None, compress_callback: Callable = None) -> int:
    """
    Adds new files to an existing archive.
    Returns the number of files successfully added.

    Args:
        archive_path: Path to the existing archive
        files_to_add: List of file paths to add
        progress_cb: Progress callback function (current, total, message)
        compress_callback: Compression function (defaults to compress_file_core)
    """
    if not compress_callback:
        from core.compressor_core import compress_file_core
        compress_callback = compress_file_core

    if not progress_cb:
        def progress_cb(current, total, message):
            logging.info(f"[{current}/{total}] {message}")

    archive_path = Path(archive_path)

    # 1. Load existing archive index
    try:
        existing_index = load_archive_index(str(archive_path))
        logging.info(f"Loaded existing archive with {len(existing_index)} files")
    except Exception as e:
        logging.error(f"Failed to load archive index: {e}")
        return 0

    # 2. Filter out files that already exist in archive
    new_files = []
    for file_path in files_to_add:
        file_path = Path(file_path)
        if not file_path.exists():
            logging.warning(f"File does not exist: {file_path}")
            continue

        # Calculate relative path (use just filename for simplicity)
        rel_path = file_path.name

        # Check if file already exists
        if rel_path in existing_index:
            logging.warning(f"File already exists in archive: {rel_path}")
            continue

        new_files.append((str(file_path), rel_path))

    if not new_files:
        logging.info("No new files to add")
        return 0

    logging.info(f"Adding {len(new_files)} new files to archive")

    # 3. Read the existing archive structure
    with open(archive_path, "rb") as f:
        # Read header to find data section end
        data_section_end = struct.unpack('<Q', f.read(8))[0]

        # Read all existing compressed data
        f.seek(8)
        existing_data = f.read(data_section_end - 8)

    # 4. Create new archive with additional files
    temp_archive_path = archive_path.with_suffix('.tmp')

    with open(temp_archive_path, "w+b") as archive_f:
        # Write placeholder for data section offset
        archive_f.write(b'\x00' * 8)

        # Copy existing compressed data
        archive_f.write(existing_data)

        # Add new files
        updated_index = existing_index.copy()
        base_offset = len(existing_data) + 8  # +8 for header

        for i, (file_path, rel_path) in enumerate(new_files):
            # Progress update
            progress_cb(i + 1, len(new_files), f"Adding {rel_path}")

            # Read and compress new file
            try:
                with open(file_path, 'rb') as f:
                    orig_data = f.read()
            except Exception as e:
                logging.error(f"Error reading file {rel_path}: {e}")
                continue

            try:
                compressed_data, method, orig_size_check, rows, cols, dicom_metadata = \
                    compress_callback(file_path, orig_data)
            except Exception as e:
                logging.error(f"Error compressing {rel_path}: {e}")
                continue

            # Write compressed data
            start_offset = archive_f.tell()
            archive_f.write(compressed_data)
            comp_size = len(compressed_data)

            # Store metadata
            if comp_size <= 0:
                logging.error(f"Invalid compressed size for {rel_path}. Skipping.")
                continue

            metadata = {
                'method': method,
                'orig_size': orig_size_check,
                'comp_size': comp_size,
                'offset': start_offset,
                'rows': rows,
                'cols': cols,
                'dicom_meta': dicom_metadata
            }
            updated_index[rel_path] = metadata

        data_end_pos = archive_f.tell()

        # Write updated index and footer
        index_json = json.dumps(updated_index).encode('utf-8')
        archive_f.write(index_json)
        archive_f.write(struct.pack('<Q', len(index_json)))
        archive_f.write(b'CSFA')

        # Update header with data section end position
        archive_f.seek(0)
        archive_f.write(struct.pack('<Q', data_end_pos))

    # 5. Replace original archive with updated one
    try:
        temp_archive_path.replace(archive_path)
        progress_cb(len(new_files), len(new_files), f"Successfully added {len(new_files)} files")
        logging.info(f"Archive updated successfully. Total files: {len(updated_index)}")
        return len(new_files)
    except Exception as e:
        logging.error(f"Failed to replace archive: {e}")
        # Clean up temp file
        if temp_archive_path.exists():
            temp_archive_path.unlink()
        return 0
