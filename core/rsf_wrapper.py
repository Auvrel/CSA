# rsf_wrapper.py
# Shallow RSF wrapper that folds bytes before giving to core compressors.
# We keep it light so it doesn't wreck speed.

import struct
import zlib
import numpy as np
from .file_utils import detect_mode
from .compressor_core import compress_dicom_image, compress_file_core

MAGIC = b'RSF0'  # marker for RSF blobs inside the .csa

def _fold_bytes(raw: bytes, block=256):
    # break into blocks, compute block mean, store delta-of-means and normalized residuals
    arr = np.frombuffer(raw, dtype=np.uint8).astype(np.int32)
    n = arr.size
    by = (n + block - 1) // block
    means = []
    blocks = []
    for i in range(by):
        start = i*block
        chunk = arr[start:start+block]
        if chunk.size == 0:
            means.append(0)
            blocks.append(np.array([], dtype=np.int32))
            continue
        m = int(round(float(chunk.mean())))
        means.append(m)
        blocks.append(chunk - m)
    # coarse = delta of means
    coarse = []
    prev = 0
    for m in means:
        coarse.append(int(m - prev))
        prev = m
    coarse_bytes = np.array(coarse, dtype=np.int32).tobytes()
    # flatten main blocks
    if blocks:
        main_flat = np.concatenate([b.ravel() for b in blocks]).astype(np.int32)
        main_bytes = main_flat.tobytes()
    else:
        main_bytes = b''
    return coarse_bytes, main_bytes

def _unfold_bytes(coarse_bytes: bytes, main_bytes: bytes, orig_len: int, block=256):
    coarse = np.frombuffer(coarse_bytes, dtype=np.int32)
    # reconstruct means
    means = []
    prev = 0
    for d in coarse:
        prev += int(d)
        means.append(prev)
    if len(main_bytes) > 0:
        main_flat = np.frombuffer(main_bytes, dtype=np.int32)
    else:
        main_flat = np.array([], dtype=np.int32)
    out = bytearray()
    idx = 0
    for i, m in enumerate(means):
        blk = main_flat[idx: idx+block]
        idx += block
        if blk.size == 0:
            break
        restored = (blk + m).astype(np.int32)
        restored = np.clip(restored, 0, 255).astype(np.uint8)
        out.extend(restored.tobytes())
    return bytes(out[:orig_len])


    if not blob.startswith(MAGIC):
        raise ValueError("Not an RSF blob")
    offset = len(MAGIC)
    # detect DICOM header vs folded header by peeking next 4 bytes
    peek = blob[offset:offset+4]
    if peek == b'DCM0':
        offset += 4
        rows, cols = struct.unpack_from('<HH', blob, offset); offset += 4
        comp_payload = blob[offset:]
        # comp_payload is the zlib'ed mapped residuals; decompress and invert mapping
        mapped = zlib.decompress(comp_payload)
        arr = np.frombuffer(mapped, dtype=np.uint32)
        # map back to signed residuals (same mapping used in compressor_core)
        signed = np.empty(arr.shape, dtype=np.int32)
        even = (arr & 1) == 0
        signed[even] = (arr[even] >> 1).astype(np.int32)
        signed[~even] = -(((arr[~even] + 1) >> 1).astype(np.int32))
        # now inverse-predict (we will use the simpler python inverse to avoid heavy code here)
        # reconstruct pixels row-major
        rows_i = int(rows); cols_i = int(cols)
        recon = np.zeros((rows_i, cols_i), dtype=np.uint16)
        idx = 0
        for y in range(rows_i):
            for x in range(cols_i):
                A = recon[y, x-1] if x > 0 else 0
                B = recon[y-1, x] if y > 0 else 0
                C = recon[y-1, x-1] if (x > 0 and y > 0) else 0
                if C >= max(A, B):
                    pred = min(A, B)
                elif C <= min(A, B):
                    pred = max(A, B)
                else:
                    pred = int(A) + int(B) - int(C)
                rv = int(signed[idx]); idx += 1
                val = pred + rv
                if val < 0: val = 0
                if val > 65535: val = 65535
                recon[y, x] = val
        return recon.tobytes()

    else:
        # folded case
        orig_len, lc, lm = struct.unpack_from('<QII', blob, offset)
        offset += struct.calcsize('<QII')
        c1 = blob[offset: offset+lc]; offset += lc
        c2 = blob[offset: offset+lm]; offset += lm
        coarse = zlib.decompress(c1)
        main = zlib.decompress(c2)
        return _unfold_bytes(coarse, main, orig_len)