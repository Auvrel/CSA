# compressor_core.py
# This contains your original three-stage compressor:
# - DICOM context-adaptive predictor -> residuals -> mapping -> zlib
# - LZMA for text
# - store-only for pre-compressed files
#
# I added an optional Numba accelerated predictor if numba is installed.
# Comments are short and not cringe.

import os
import zlib
import lzma
import warnings

# optional libs
try:
    import numpy as np
except Exception:
    raise RuntimeError("numpy required")

try:
    from numba import njit, int64, int32, uint16
    NUMBA = True
except Exception:
    NUMBA = False

try:
    import pydicom
    PYDICOM = True
except Exception:
    PYDICOM = False

# method codes
METHOD_DICOM = 1
METHOD_LZMA_TEXT = 2
METHOD_ZLIB_GENERIC = 3
METHOD_STORE_ONLY = 4
METHOD_RSF = 5  # used when RSF wrapper used

ARCHIVE_HEADER_MAGIC = b'CSA'  # 3 bytes

# ----------------------
# DICOM predictor (Paeth-like / context adaptive)
# ----------------------
def context_predict(A, B, C):
    # simple Paeth-like: clamp behavior included
    if C >= max(A, B):
        return min(A, B)
    elif C <= min(A, B):
        return max(A, B)
    else:
        return A + B - C

if NUMBA:
    # numba-accelerated loop (faster)
    @njit
    def gen_residuals_numba(image):
        rows, cols = image.shape
        out = np.empty((rows, cols), dtype=np.int32)
        for y in range(rows):
            for x in range(cols):
                A = image[y, x-1] if x > 0 else 0
                B = image[y-1, x] if y > 0 else 0
                C = image[y-1, x-1] if (x > 0 and y > 0) else 0
                # paeth-ish without function call to keep njit friendly
                if C >= max(A, B):
                    pred = min(A, B)
                elif C <= min(A, B):
                    pred = max(A, B)
                else:
                    pred = A + B - C
                out[y, x] = int(image[y, x]) - int(pred)
        return out
else:
    def gen_residuals_py(image):
        rows, cols = image.shape
        out = np.empty((rows, cols), dtype=np.int32)
        for y in range(rows):
            for x in range(cols):
                A = image[y, x-1] if x > 0 else 0
                B = image[y-1, x] if y > 0 else 0
                C = image[y-1, x-1] if (x > 0 and y > 0) else 0
                pred = context_predict(A, B, C)
                out[y, x] = int(image[y, x]) - int(pred)
        return out

# signed <-> unsigned zigzag mapping (int32 -> uint32)
def signed_to_unsigned(arr):
    # arr: numpy int32
    pos = arr >= 0
    mapped = arr.astype('int64')  # prevent overflow
    res = np.empty(arr.shape, dtype=np.uint32)
    res[pos] = (mapped[pos].astype(np.uint64) << 1)
    res[~pos] = ((-mapped[~pos].astype(np.uint64) << 1) - 1)
    return res

def unsigned_to_signed(mapped):
    mapped = mapped.astype(np.uint32)
    even = (mapped & 1) == 0
    res = np.empty(mapped.shape, dtype=np.int32)
    res[even] = (mapped[even] >> 1).astype(np.int32)
    res[~even] = -(((mapped[~even] + 1) >> 1).astype(np.int32))
    return res

# ----------------------
# compress functions
# ----------------------
def compress_text_lzma(raw_bytes):
    # big dictionary for best ratio on text
    try:
        filters = [{"id": lzma.FILTER_LZMA2, "dict_size": 32 * 1024 * 1024}]
        return lzma.compress(raw_bytes, format=lzma.FORMAT_XZ, filters=filters)
    except Exception:
        return lzma.compress(raw_bytes)

def compress_file_core(path, raw_bytes):
    """
    decides which baseline method to use (without RSF).
    returns (compressed_bytes, method_code, orig_size, rows, cols)
    rows/cols only for DICOM
    """
    _, ext = os.path.splitext(path)
    ext = ext.lower()
    orig = len(raw_bytes)
    try:
        # text
        if ext in ('.txt', '.log', '.csv', '.sql', '.py', '.json', '.xml', '.md'):
            comp = compress_text_lzma(raw_bytes)
            return comp, METHOD_LZMA_TEXT, orig, 0, 0
        # images that are already compressed - store
        if ext in ('.jpg', '.jpeg', '.png', '.mp4', '.zip', '.gz'):
            return raw_bytes, METHOD_STORE_ONLY, orig, 0, 0
        # default zlib
        comp = zlib.compress(raw_bytes, level=9)
        if len(comp) < orig * 0.95:
            return comp, METHOD_ZLIB_GENERIC, orig, 0, 0
        return raw_bytes, METHOD_STORE_ONLY, orig, 0, 0
    except Exception as e:
        warnings.warn(f"compress core fail {e}")
        return raw_bytes, METHOD_STORE_ONLY, orig, 0, 0

# DICOM-special compressor (keeps original behavior)
def compress_dicom_image(path):
    """
    read DICOM, compute residuals, map, then zlib them.
    returns bytes blob ready to store in archive.
    """
    if not PYDICOM:
        raise RuntimeError("pydicom not installed, can't compress DICOM here")
    ds = pydicom.dcmread(path, force=True)
    if 'PixelData' not in ds:
        raise ValueError("No PixelData")
    image = ds.pixel_array
    if image.dtype != np.uint16:
        image = image.astype(np.uint16)
    if NUMBA:
        residuals = gen_residuals_numba(image)
    else:
        residuals = gen_residuals_py(image)
    mapped = signed_to_unsigned(residuals).ravel()
    return zlib.compress(mapped.tobytes(), level=9), METHOD_DICOM, image.shape[0]*image.shape[1], image.shape[0], image.shape[1]