# archive.py - build and read .csa archives (binary header + JSON index at end)
import os
import struct
import json
from .compressor_core import ARCHIVE_HEADER_MAGIC
from typing import Callable

def build_archive(root_dir: str, out_file: str, progress_cb=None, compress_callback: Callable=None):
    """
    Walk root_dir, compress files via compress_callback(path, raw_bytes),
    write blobs sequentially, and append JSON index at the end.
    compress_callback returns tuple: (blob_bytes, method_code, orig_size, rows, cols)
    """
    archive_index = {}
    current_offset = 7  # 3-byte magic + 4-byte placeholder
    all_paths = []
    for root, _, files in os.walk(root_dir):
        for fn in files:
            all_paths.append(os.path.join(root, fn))
    total = len(all_paths)
    processed = 0
    with open(out_file, 'wb') as f_out:
        f_out.write(ARCHIVE_HEADER_MAGIC)
        f_out.write(struct.pack('<I', 0))  # placeholder for index size
        for path in all_paths:
            rel = os.path.relpath(path, root_dir).replace(os.path.sep, '/')
            with open(path, 'rb') as f:
                raw = f.read()
            if compress_callback:
                blob, method, orig, rows, cols = compress_callback(path, raw)
            else:
                # default behaviour: store raw
                blob, method, orig, rows, cols = raw, 4, len(raw), 0, 0
            f_out.write(blob)
            archive_index[rel] = {
                'start': current_offset,
                'comp_size': len(blob),
                'orig_size': orig,
                'method': method,
                'rows': rows,
                'cols': cols
            }
            current_offset += len(blob)
            processed += 1
            if progress_cb:
                progress_cb(int((processed/total)*100), f"{processed}/{total} {rel}")
        index_bytes = json.dumps(archive_index).encode('utf-8')
        f_out.write(index_bytes)
        # write index size in header
        f_out.seek(3)
        f_out.write(struct.pack('<I', len(index_bytes)))
    return len(archive_index)

def load_archive_index(archive_file: str):
    try:
        with open(archive_file, 'rb') as f:
            magic = f.read(3)
            if magic != ARCHIVE_HEADER_MAGIC:
                raise ValueError("bad magic")
            index_size = struct.unpack('<I', f.read(4))[0]
            archive_size = os.path.getsize(archive_file)
            index_start = archive_size - index_size
            if index_start < 7 or index_start > archive_size:
                raise ValueError("invalid index size")
            f.seek(index_start)
            idxb = f.read(index_size)
            return json.loads(idxb.decode('utf-8'))
    except Exception as e:
        raise

def extract_single(archive_file: str, index: dict, rel_path: str, rsf_decompress_func=None):
    meta = index.get(rel_path)
    if not meta:
        raise ValueError("not found")
    with open(archive_file, 'rb') as f:
        f.seek(meta['start'])
        blob = f.read(meta['comp_size'])
    method = meta.get('method')
    # METHOD_RSF assumed equal to 5 here
    if method == 5 and rsf_decompress_func:
        return rsf_decompress_func(blob)
    # method code 1 (DICOM) may be raw DICOM-compressed blob from core; treat as raw for simplicity
    # if zipped/lzma etc, user must store metadata and reverse accordingly. For prototype, we return blob.
    # If blob is zlib compressed, try to decompress
    try:
        import zlib
        return zlib.decompress(blob)
    except Exception:
        try:
            import lzma
            return lzma.decompress(blob)
        except Exception:
            return blob