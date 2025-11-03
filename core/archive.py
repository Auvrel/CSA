import os
import struct
from pathlib import Path
import json
from typing import Callable
import logging
import pydicom # REQUIRED for DICOM metadata handling during extraction
import shutil # Used for creating template file
from pydicom.dataset import FileMetaDataset
from pydicom.uid import generate_uid, ImplicitVRLittleEndian
from pydicom.filewriter import dcmwrite
import numpy as np
# Placeholder for compression method codes (must match core/compressor_core)
METHOD_CUSTOM_DICOM = 1 # Changed from METHOD_DICOM to match compressor_core usage
METHOD_LZMA_TEXT = 2
METHOD_ZLIB_GENERIC = 3
METHOD_STORE_ONLY = 4
METHOD_RSF = 5 

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def build_archive(root_dir, out_file, progress_cb: Callable, compress_callback: Callable):
    """
    Compresses a directory and builds the .csa archive file.
    ...
    """
    root_path = Path(root_dir)
    file_list = [p for p in root_path.rglob('*') if p.is_file()]
    total_files = len(file_list)
    archive_index = {}
    
    if total_files == 0:
        # NOTE: Using the 3-arg signature for the progress_hook
        progress_cb(100, 100, "Archive complete (0 files processed).")
        return 0

    with open(out_file, "wb") as archive_f:
        archive_f.write(b'\x00' * 4) # Placeholder for data section offset 
        
        for i, file_path in enumerate(file_list):
            
            # --- PROGRESS UPDATE AND CANCELLATION CHECK ---
            # NOTE: progress_cb must now accept (count, total, path_or_msg)
            is_running = progress_cb(i + 1, total_files, file_path)
            if not is_running:
                # If cancellation is signaled
                logging.warning("Archiving cancelled by user.")
                return 0 
            # ---------------------------------------------

            rel_path = str(file_path.relative_to(root_path)).replace('\\', '/')
            
            with open(file_path, "rb") as f:
                orig_data = f.read()

            # The compressor returns (blob, method, size, rows, cols)
            compressed_data, method, orig_size_check, rows, cols = compress_callback(file_path, orig_data)
            
            start_offset = archive_f.tell()
            archive_f.write(compressed_data)
            comp_size = archive_f.tell() - start_offset

            # Store metadata for the index
            metadata = {
                'method': method,
                'orig_size': orig_size_check,
                'comp_size': comp_size,
                'offset': start_offset,
                'rows': rows,
                'cols': cols
            }
            logging.info(f"BUILD: Storing metadata for {rel_path}: Method={method}, Size={comp_size}")
            archive_index[rel_path] = metadata
            
        data_end_pos = archive_f.tell()
        
        # 2. Serialize and write the index
        index_json = json.dumps(archive_index).encode('utf-8')
        archive_f.write(index_json)
        
        # 3. Write the final footer metadata
        archive_f.write(struct.pack('<I', len(index_json))) 
        archive_f.write(b'CSFA') # Magic header for verification
        
        # 4. Fill the initial placeholder (data offset)
        archive_f.seek(0)
        archive_f.write(struct.pack('<I', data_end_pos)) 
        
    # Final progress message (using the 3-arg signature)
    progress_cb(total_files, total_files, "Archive complete.")

    return total_files

def load_archive_index(archive_path):
    """Reads the archive footer and index to memory."""
    with open(archive_path, "rb") as f:
        f.seek(-8, os.SEEK_END)
        footer = f.read()
        
        magic = footer[4:]
        if magic != b'CSFA':
            raise ValueError("Invalid CSFA archive file format.")
            
        index_size = struct.unpack('<I', footer[:4])[0]
        
        f.seek(-(index_size + 8), os.SEEK_END)
        index_json = f.read(index_size).decode('utf-8')
        
        return json.loads(index_json)

def extract_single(archive_path, index, rel_path, out_dir, decompress_callback):
    """
    Extracts, decompresses, and writes a single file from the archive.
    Returns: The raw uncompressed file content bytes (needed for the explorer preview).
    """
    meta = index.get(rel_path)
    if not meta:
        raise KeyError(f"File '{rel_path}' not found in index.")
        
    method = meta['method']
    offset = meta['offset']
    comp_size = meta['comp_size']
    rows = meta.get('rows', 0)
    cols = meta.get('cols', 0)
    
    with open(archive_path, "rb") as f:
        f.seek(offset)
        compressed_data = f.read(comp_size)
        
    logging.info(f"EXTRACT: Reading {comp_size} bytes from offset {offset} for file {rel_path}")
    
    # Decompress using the core function. This returns the raw data blob.
    uncompressed_data = decompress_callback(method, compressed_data, rows, cols)
    
    # Determine output path and ensure directories exist
    output_file_path = Path(out_dir) / rel_path.replace('/', os.sep)
    output_file_path.parent.mkdir(parents=True, exist_ok=True)
    
    # --- DICOM RECONSTRUCTION FIX (The Core Change) ---
    # This block executes if it's a DICOM-processed file
    if method == METHOD_CUSTOM_DICOM or (method == METHOD_ZLIB_GENERIC and rows > 0):
        try:
            # 1. Reconstruct the numpy array
            pixel_array = np.frombuffer(uncompressed_data, dtype=np.uint16).reshape((rows, cols))

            # 2. Create a minimal DICOM dataset (The data elements)
            ds = pydicom.Dataset()
            ds.Rows = rows
            ds.Columns = cols
            ds.BitsAllocated = 16
            ds.BitsStored = 16
            ds.HighBit = 15
            ds.PixelRepresentation = 0 # Unsigned integer
            ds.PhotometricInterpretation = "MONOCHROME2"
            ds.SOPClassUID = '1.2.840.10008.5.1.4.1.1.7' # Secondary Capture (Fallback)
            ds.SOPInstanceUID = generate_uid() # New UID for the new file
            ds.is_implicit_VR = True 
            ds.is_little_endian = True
            
            # Add the decompressed pixel data
            ds.PixelData = pixel_array.tobytes()

            # 3. CRITICAL: Manually create the required File Meta Information (FMI)
            # This satisfies the DICOM writer and prevents the FMI error.
            file_meta = FileMetaDataset()
            file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
            file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
            file_meta.TransferSyntaxUID = ImplicitVRLittleEndian # Explicit VR Little Endian is common
            file_meta.FileMetaInformationGroupLength = 0 # Will be recalculated by dcmwrite
            ds.file_meta = file_meta

            # 4. Write the rebuilt DICOM file using dcmwrite (preferred over save_as)
            dcmwrite(output_file_path, ds)
            
        except Exception as e:
            logging.error(f"DICOM Reconstruction Failed for {rel_path}: {e}")
            # If reconstruction fails, write the raw pixel data and raise the error
            with open(output_file_path, "wb") as out_f:
                out_f.write(uncompressed_data)
            # Raising the error informs the GUI the attempt failed
            raise RuntimeError(f"Extraction failed due to DICOM reconstruction error: {e}") 
            
    else:
        # 5. Write the raw uncompressed data for non-DICOM files
        with open(output_file_path, "wb") as out_f:
            out_f.write(uncompressed_data)

    # Return the raw data for the explorer preview logic (even if it's DICOM, the raw bytes are usually used)
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
            # This call uses the corrected 5-argument signature:
            extract_single(
    archive_path, 
    archive_index, 
    rel_path, 
    output_dir,             # MUST BE PRESENT
    decompress_file_core    # MUST BE PRESENT
)
            extracted_count += 1
        except Exception as e:
            logging.error(f"Failed to extract {rel_path}: {e}")
            # If a file fails, we log it and continue to the next one
            
    # Final Progress
    # NOTE: Using total_files for both count and total ensures 100%
    progress_hook(total_files, total_files, "Extraction Succeeded!")
    
    return extracted_count