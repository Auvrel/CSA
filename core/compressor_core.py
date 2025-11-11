# core/compressor_core.py

import numpy as np
import zlib
import json
import lzma
import logging
from numba import jit
from typing import Tuple
import pydicom
import io
from pathlib import Path

# --- Constants (Must match archive.py) ---
METHOD_CUSTOM_DICOM = 1
METHOD_LZMA_TEXT = 2
METHOD_ZLIB_GENERIC = 3
METHOD_STORE_ONLY = 4
METHOD_RSF = 5
METHOD_JPEG_OPTIMIZED = 6  # For JPEG files (store as-is or minimal processing)
METHOD_PNG_OPTIMIZED = 7   # For PNG files with better compression
METHOD_TIFF_COMPRESSED = 8 # For TIFF files
METHOD_BMP_COMPRESSED = 9  # For BMP files
METHOD_RAW_IMAGE = 10      # For other image formats

# Production logging: Only show warnings and errors
logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')

# PERFORMANCE: Optimized compression level (6 = 2-3x faster than 9, minimal quality loss)
COMPRESSION_LEVEL = 6

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
    Uses 2D array access for correctness.
    """
    rows, cols = image_array.shape
    
    # Initialize 2D array for residuals (using int32 for safety during subtraction)
    residual_array = np.zeros((rows, cols), dtype=np.int32) 
    
    for r in range(rows):
        for c in range(cols):
            current_value = image_array[r, c]

            # Define Neighbors (A: Left, B: Up, C: Up-Left)
            # Access neighbors from the original image array
            a = image_array[r, c - 1] if c > 0 else 0
            b = image_array[r - 1, c] if r > 0 else 0
            c_up_left = image_array[r - 1, c - 1] if r > 0 and c > 0 else 0
            
            if r == 0 and c == 0:
                prediction = 0
            else:
                prediction = paeth_predictor(a, b, c_up_left)
            
            residual = current_value - prediction
            residual_array[r, c] = residual

    # Flatten and convert to int32 (will be converted to int16 before ZLIB)
    return residual_array.flatten()


@jit(nopython=True)
def reconstruct_image_from_residuals(residual_stream: np.ndarray, rows: int, cols: int) -> np.ndarray:
    """
    Inverse of calculate_residual_stream, reconstructs the 16-bit image array using Paeth logic.
    Uses 2D array access for correctness.
    """
    if rows * cols == 0:
        return np.zeros((0, 0), dtype=np.uint16)
        
    # Initialize the image array directly in 2D
    image_array = np.zeros((rows, cols), dtype=np.int32)
    
    k = 0
    for r in range(rows):
        for c in range(cols):
            residual = residual_stream[k]
            
            # Neighbors must be accessed from the partially RECONSTRUCTED image_array
            
            # A: Left 
            a = image_array[r, c - 1] if c > 0 else 0 
            
            # B: Up 
            b = image_array[r - 1, c] if r > 0 else 0 
            
            # C: Up-Left 
            c_up_left = image_array[r - 1, c - 1] if r > 0 and c > 0 else 0 
            
            # Reconstruction: Original = Residual + Prediction
            prediction = paeth_predictor(a, b, c_up_left)
            current_value = residual + prediction
            
            image_array[r, c] = current_value 
            k += 1

    # Return the reconstructed array
    return image_array.astype(np.uint16)


# --- Compression Functions ---

def compress_dicom_image_smart(pixel_array: np.ndarray) -> Tuple[bytes, int]:
    """
    Intelligently compress DICOM pixel data using optimal method.
    PERFORMANCE: Uses compression level 6 for 2-3x speed improvement.
    """
    if pixel_array.dtype != np.uint16:
        pixel_array = pixel_array.astype(np.uint16)
    
    raw_image_bytes = pixel_array.tobytes()
    
    # --- A. Custom Predictive Compression Attempt (METHOD 1) ---
    try:
        # 1. Calculate residuals (returns np.int32 array)
        residual_stream = calculate_residual_stream(pixel_array) 
        
        # 2. Convert to np.int16 (2-byte) to match the raw pixel data stream size
        residual_stream_16bit = residual_stream.astype(np.int16) 
        
        # 3. Compress the optimized stream
        optimized_compressed_data = zlib.compress(residual_stream_16bit.tobytes(), COMPRESSION_LEVEL)
        
        # --- B. Direct Raw ZLIB Fallback Compression (METHOD 3) ---
        # Only compute fallback if residual compression succeeded
        raw_compressed_data = zlib.compress(raw_image_bytes, COMPRESSION_LEVEL)
        
        # --- C. Choose best method ---
        if len(optimized_compressed_data) < len(raw_compressed_data):
            return optimized_compressed_data, METHOD_CUSTOM_DICOM
        else:
            return raw_compressed_data, METHOD_ZLIB_GENERIC
            
    except Exception as e:
        # Fallback to raw compression if residual calculation fails
        logging.warning(f"Custom prediction failed, using generic compression: {e}")
        raw_compressed_data = zlib.compress(raw_image_bytes, COMPRESSION_LEVEL)
        return raw_compressed_data, METHOD_ZLIB_GENERIC


# --- File Type Detection ---

def detect_file_type(abs_path: str, raw_bytes: bytes) -> str:
    """
    Detect file type based on extension and magic bytes.
    Returns file type string for compression method selection.
    """
    path = Path(abs_path)
    ext = path.suffix.lower()

    # Check magic bytes for better detection
    if len(raw_bytes) >= 4:
        magic = raw_bytes[:4]
        if magic.startswith(b'\xff\xd8'):  # JPEG SOI marker
            return 'jpeg'
        elif magic.startswith(b'\x89PNG'):  # PNG signature
            return 'png'
        elif magic.startswith(b'II*\x00') or magic.startswith(b'MM\x00*'):  # TIFF
            return 'tiff'
        elif magic.startswith(b'BM'):  # BMP
            return 'bmp'
        elif raw_bytes.startswith(b'DICM'):  # DICOM (after preamble)
            return 'dicom'

    # Fallback to extension-based detection
    if ext in ['.jpg', '.jpeg']:
        return 'jpeg'
    elif ext == '.png':
        return 'png'
    elif ext in ['.tif', '.tiff']:
        return 'tiff'
    elif ext == '.bmp':
        return 'bmp'
    elif ext in ['.dcm', '.dicom']:
        return 'dicom'
    elif ext in ['.txt', '.csv', '.json', '.xml', '.html', '.css', '.js', '.py']:
        return 'text'
    else:
        return 'binary'

# --- Specialized Compression Functions ---

def compress_jpeg_file(raw_bytes: bytes) -> Tuple[bytes, int]:
    """
    Handle JPEG files. JPEG is already compressed, so we have options:
    - Store as-is (fastest)
    - Try to optimize/recompress (may not help much)
    """
    # For now, store JPEG files as-is since they're already compressed
    # Could add JPEG optimization later if needed
    return raw_bytes, METHOD_STORE_ONLY

def compress_png_file(raw_bytes: bytes) -> Tuple[bytes, int]:
    """
    Compress PNG files. PNG uses DEFLATE internally, so we can try better compression.
    """
    try:
        # Try maximum compression for PNG (since PNG format allows it)
        compressed = zlib.compress(raw_bytes, 9)  # Max compression for PNG
        if len(compressed) < len(raw_bytes):
            return compressed, METHOD_PNG_OPTIMIZED
        else:
            return raw_bytes, METHOD_STORE_ONLY
    except Exception:
        return raw_bytes, METHOD_STORE_ONLY

def compress_tiff_file(raw_bytes: bytes) -> Tuple[bytes, int]:
    """
    Compress TIFF files. Many TIFFs are uncompressed or poorly compressed.
    """
    try:
        # Try high compression for TIFF
        compressed = zlib.compress(raw_bytes, COMPRESSION_LEVEL)
        if len(compressed) < len(raw_bytes) * 0.9:  # Only compress if we save at least 10%
            return compressed, METHOD_TIFF_COMPRESSED
        else:
            return raw_bytes, METHOD_STORE_ONLY
    except Exception:
        return raw_bytes, METHOD_STORE_ONLY

def compress_bmp_file(raw_bytes: bytes) -> Tuple[bytes, int]:
    """
    Compress BMP files. BMPs are usually uncompressed, so compression helps a lot.
    """
    try:
        compressed = zlib.compress(raw_bytes, COMPRESSION_LEVEL)
        # BMP compression usually saves significant space
        return compressed, METHOD_BMP_COMPRESSED
    except Exception:
        return raw_bytes, METHOD_STORE_ONLY

def compress_text_file(raw_bytes: bytes) -> Tuple[bytes, int]:
    """
    Compress text files. Text compresses very well.
    """
    try:
        # Try LZMA for text (usually better than ZLIB for text)
        lzma_compressed = lzma.compress(raw_bytes, preset=6)
        zlib_compressed = zlib.compress(raw_bytes, COMPRESSION_LEVEL)

        # Use the better compression
        if len(lzma_compressed) < len(zlib_compressed):
            return lzma_compressed, METHOD_LZMA_TEXT
        else:
            return zlib_compressed, METHOD_ZLIB_GENERIC
    except Exception:
        return zlib.compress(raw_bytes, COMPRESSION_LEVEL), METHOD_ZLIB_GENERIC

# --- Top-Level Compression Dispatch ---

def compress_file_core(abs_path: str, raw_bytes: bytes) -> tuple[bytes, int, int, int, int, str]:
    """
    Compresses a single file with intelligent file-type-specific compression.
    PERFORMANCE: Optimized for production speed with file-type awareness.
    """
    # Defaults
    orig_size = len(raw_bytes)
    rows, cols = 0, 0
    dicom_metadata_json_string = '{}'

    # Detect file type
    file_type = detect_file_type(abs_path, raw_bytes)

    if file_type == 'dicom':
        # DICOM files - use custom compression
        try:
            ds = pydicom.dcmread(io.BytesIO(raw_bytes), force=True)
            pixel_array = ds.pixel_array
            rows, cols = pixel_array.shape

            # Extract and encode essential DICOM metadata
            metadata_buffer = io.BytesIO()
            ds.PixelData = b''  # Remove pixel data before saving header
            pydicom.dcmwrite(metadata_buffer, ds)
            metadata_bytes = metadata_buffer.getvalue()

            # Compress metadata header (use max compression for small metadata)
            compressed_metadata_blob = zlib.compress(metadata_bytes, 9)

            # Encode to hex string for storage
            meta_int = int.from_bytes(compressed_metadata_blob, byteorder='big')
            encoded_metadata_string = hex(meta_int)

            # Create metadata dictionary
            dicom_metadata_raw = {
                'WindowCenter': str(ds.get('WindowCenter', None)),
                'WindowWidth': str(ds.get('WindowWidth', None)),
                'RescaleIntercept': ds.get('RescaleIntercept', 0),
                'RescaleSlope': ds.get('RescaleSlope', 1),
                'metadata_blob': encoded_metadata_string
            }

            dicom_metadata_json_string = json.dumps(dicom_metadata_raw)

            # Compress pixel data (optimized for speed)
            compressed_blob, method = compress_dicom_image_smart(pixel_array)

        except Exception as e:
            logging.warning(f"DICOM processing failed for {abs_path}: {e}")
            # Fallback to generic compression
            compressed_blob = zlib.compress(raw_bytes, COMPRESSION_LEVEL)
            method = METHOD_ZLIB_GENERIC

    elif file_type == 'jpeg':
        compressed_blob, method = compress_jpeg_file(raw_bytes)

    elif file_type == 'png':
        compressed_blob, method = compress_png_file(raw_bytes)

    elif file_type == 'tiff':
        compressed_blob, method = compress_tiff_file(raw_bytes)

    elif file_type == 'bmp':
        compressed_blob, method = compress_bmp_file(raw_bytes)

    elif file_type == 'text':
        compressed_blob, method = compress_text_file(raw_bytes)

    else:
        # Binary or unknown files - use generic compression
        compressed_blob = zlib.compress(raw_bytes, COMPRESSION_LEVEL)
        method = METHOD_ZLIB_GENERIC

    return compressed_blob, method, orig_size, rows, cols, dicom_metadata_json_string

# --- Decompression Functions (RESTORED) ---

def decompress_dicom_image_smart(compressed_data: bytes, rows: int, cols: int) -> bytes:
    """
    Decompresses the custom DICOM residual stream and reconstructs the image array.
    """
    # 1. Decompress the residual stream bytes
    residual_bytes = zlib.decompress(compressed_data)

    # 2. Convert bytes back to a numpy array of residuals
    # NOTE: The residuals were stored as np.int16
    residual_stream = np.frombuffer(residual_bytes, dtype=np.int16)

    # 3. Reconstruct the image array using Numba
    image_array = reconstruct_image_from_residuals(residual_stream, rows, cols)
    
    # CRITICAL: Return the raw bytes of the reconstructed array
    return image_array.tobytes()

def decompress_file_core(method_code: int, compressed_data: bytes, rows: int, cols: int) -> bytes:
    """
    Top-level dispatch for decompression called by archive.py.
    Supports all compression methods including new file types.
    """

    if method_code == METHOD_CUSTOM_DICOM:
        return decompress_dicom_image_smart(compressed_data, rows, cols)

    elif method_code == METHOD_LZMA_TEXT:
        return lzma.decompress(compressed_data)

    elif method_code == METHOD_ZLIB_GENERIC:
        return zlib.decompress(compressed_data)

    elif method_code == METHOD_STORE_ONLY:
        return compressed_data

    elif method_code == METHOD_PNG_OPTIMIZED:
        return zlib.decompress(compressed_data)

    elif method_code == METHOD_TIFF_COMPRESSED:
        return zlib.decompress(compressed_data)

    elif method_code == METHOD_BMP_COMPRESSED:
        return zlib.decompress(compressed_data)

    elif method_code in [METHOD_JPEG_OPTIMIZED, METHOD_RAW_IMAGE, METHOD_RSF]:
        return compressed_data  # These methods store data as-is

    else:
        raise ValueError(f"Unknown compression method code: {method_code}")
