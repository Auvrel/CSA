# core/compressor_core.py

import numpy as np
import zlib
import os
import lzma
import logging
from numba import jit
from typing import Tuple
from pathlib import Path 
import pydicom # Needed for reading DICOM files in compress_file_core

# --- Constants (Must match archive.py) ---
METHOD_CUSTOM_DICOM = 1
METHOD_LZMA_TEXT = 2
METHOD_ZLIB_GENERIC = 3
METHOD_STORE_ONLY = 4
METHOD_RSF = 5 

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# --- Numba-Accelerated Core Logic (PAETH PREDICTOR) ---

@jit(nopython=True)
def paeth_predictor(a: int, b: int, c: int) -> int:
    """Computes the Paeth predictor value."""
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)

    if pa <= pb and pa <= pc:
        return a
    elif pb <= pc:
        return b
    else:
        return c

@jit(nopython=True)
def calculate_residual_stream(image_array: np.ndarray) -> np.ndarray:
    """
    Applies the Paeth predictor to generate a residual stream from a 16-bit image array.
    """
    rows, cols = image_array.shape
    # Residuals typically range from -1 to +1 times the range of the original data, so int32 is safer.
    residual_stream = np.zeros(rows * cols, dtype=np.int32) 
    
    k = 0
    for r in range(rows):
        for c in range(cols):
            current_value = image_array[r, c]

            # Define Neighbors (A: Left, B: Up, C: Up-Left)
            # Use 0 at boundaries as a safe default
            a = image_array[r, c - 1] if c > 0 else 0
            b = image_array[r - 1, c] if r > 0 else 0
            c_up_left = image_array[r - 1, c - 1] if r > 0 and c > 0 else 0
            
            # The first pixel and the first column (r>0) are often handled specially,
            # but Paeth handles boundaries gracefully.
            if r == 0 and c == 0:
                prediction = 0
            else:
                prediction = paeth_predictor(a, b, c_up_left)
            
            residual = current_value - prediction
            residual_stream[k] = residual
            k += 1

    # Convert to int16 after prediction if possible, but keeping int32 for zlib compression is often fine.
    return residual_stream.astype(np.int16) 


@jit(nopython=True)
def reconstruct_image_from_residuals(residual_stream: np.ndarray, rows: int, cols: int) -> np.ndarray:
    """
    Inverse of calculate_residual_stream, reconstructs the 16-bit image array using Paeth logic.
    """
    if rows * cols == 0:
        return np.zeros((0, 0), dtype=np.uint16)
        
    image_array = np.zeros(rows * cols, dtype=np.int32)
    
    k = 0
    for r in range(rows):
        for c in range(cols):
            residual = residual_stream[k]
            
            # Define Neighbors from the ALREADY RECONSTRUCTED image_array
            # A: Left, B: Up, C: Up-Left (Need to calculate indices)
            
            a = image_array[r * cols + (c - 1)] if c > 0 else 0
            b = image_array[(r - 1) * cols + c] if r > 0 else 0
            c_up_left = image_array[(r - 1) * cols + (c - 1)] if r > 0 and c > 0 else 0
            
            if r == 0 and c == 0:
                prediction = 0
            else:
                prediction = paeth_predictor(a, b, c_up_left)
            
            # Reconstruction: Original = Residual + Prediction
            current_value = residual + prediction
            image_array[r * cols + c] = current_value
            k += 1

    # Reshape and convert back to uint16
    return image_array.reshape((rows, cols)).astype(np.uint16)


# --- Compression Functions ---
# ... (compress_dicom_image_smart remains the same) ...

def compress_dicom_image_smart(pixel_array: np.ndarray, compression_level: int = 9) -> Tuple[bytes, int]:
    # ... (Keep your existing compress_dicom_image_smart logic here) ...
    # (Ensure it calls the new calculate_residual_stream)
    
    # [YOUR EXISTING CODE FOR compress_dicom_image_smart GOES HERE]
    # ...
    
    if pixel_array.dtype != np.uint16:
        pixel_array = pixel_array.astype(np.uint16)
    
    raw_image_bytes = pixel_array.tobytes()
    
    # --- A. Custom Predictive Compression Attempt (METHOD 1) ---
    optimized_compressed_data = b''
    try:
        # 1. Calculate residuals (Numba code)
        residual_stream = calculate_residual_stream(pixel_array) # Calls the new Paeth logic
        
        # 2. Compress the optimized stream (residuals)
        optimized_compressed_data = zlib.compress(residual_stream.tobytes(), compression_level)
    except Exception as e:
        logging.error(f"Custom prediction failed: {e}")

    # --- B. Direct Raw ZLIB Fallback Compression (METHOD 3) ---
    raw_compressed_data = zlib.compress(raw_image_bytes, compression_level)

    # --- C. Intelligent Decision ---
    if not optimized_compressed_data or (len(optimized_compressed_data) >= len(raw_compressed_data)):
        return raw_compressed_data, METHOD_ZLIB_GENERIC
    else:
        return optimized_compressed_data, METHOD_CUSTOM_DICOM
    
# --- Top-Level Compression Dispatch ---

def compress_file_core(path: Path, raw_bytes: bytes) -> Tuple[bytes, int, int, int, int]:
    # ... (Keep your existing compress_file_core logic here) ...
    
    # [YOUR EXISTING CODE FOR compress_file_core GOES HERE]
    # ...
    
    orig_size = len(raw_bytes)
    rows, cols = 0, 0
    
    if Path(path).suffix.lower() in ('.dcm', '.dicom') or raw_bytes.startswith(b'DICM'):
        try:
            ds = pydicom.dcmread(path, force=True)
            pixel_array = ds.pixel_array
            rows, cols = pixel_array.shape
            
            blob, method = compress_dicom_image_smart(pixel_array)
            return blob, method, orig_size, rows, cols
            
        except Exception:
            logging.warning(f"Failed to process DICOM at {path}. Falling back to generic compression.")
            pass

    # Fallback/Other file types
    if orig_size < 1024:
        return raw_bytes, METHOD_STORE_ONLY, orig_size, 0, 0
    
    if Path(path).suffix.lower() in ('.txt', '.log', '.csv', '.json', '.xml'):
        try:
            blob = lzma.compress(raw_bytes, preset=9)
            return blob, METHOD_LZMA_TEXT, orig_size, 0, 0
        except Exception:
            pass
            
    # Generic binary/fallback: Use standard ZLIB
    blob = zlib.compress(raw_bytes, 9)
    return blob, METHOD_ZLIB_GENERIC, orig_size, 0, 0


# --- Decompression Functions (RESTORED) ---

def decompress_dicom_image_smart(compressed_data: bytes, rows: int, cols: int) -> bytes:
    """
    Decompresses the custom DICOM residual stream and reconstructs the image array.
    Returns: raw numpy array bytes (uint16)
    """

    # 1. Decompress the residual stream bytes
    residual_bytes = zlib.decompress(compressed_data)

    # 2. Convert bytes back to a numpy array of residuals
    residual_stream = np.frombuffer(residual_bytes, dtype=np.int16)

    # 3. Reconstruct the image array using Numba (calls the inverse Paeth logic)
    image_array = reconstruct_image_from_residuals(residual_stream, rows, cols)
    
    # CRITICAL: Return the raw bytes of the reconstructed array for re-wrapping in archive.py
    return image_array.tobytes()

def decompress_file_core(method_code: int, compressed_data: bytes, rows: int, cols: int) -> bytes:
    """
    Top-level dispatch for decompression called by archive.py.
    Returns: uncompressed_bytes
    """
    
    if method_code == METHOD_CUSTOM_DICOM:
        return decompress_dicom_image_smart(compressed_data, rows, cols)
        
    elif method_code == METHOD_LZMA_TEXT:
        return lzma.decompress(compressed_data)
        
    elif method_code == METHOD_ZLIB_GENERIC:
        return zlib.decompress(compressed_data)
        
    elif method_code == METHOD_STORE_ONLY:
        return compressed_data
        
    else:
        raise ValueError(f"Unknown compression method code: {method_code}")