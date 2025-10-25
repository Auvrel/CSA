# file_utils.py - tiny helpers for file detection and stuff
import mimetypes
import os

# quick list for text recognition
TEXT_EXTS = {'.json', '.txt', '.log', '.csv', '.xml', '.sql', '.md'}
DICOM_EXTS = {'.dcm', '.dicom'}

def detect_mode(path):
    """
    returns one of: 'DICOM', 'TEXT', 'BINARY'
    simple heuristics: extension + mimetype
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in DICOM_EXTS:
        return 'DICOM'
    if ext in TEXT_EXTS:
        return 'TEXT'
    mtype, _ = mimetypes.guess_type(path)
    if mtype and mtype.startswith('text'):
        return 'TEXT'
    return 'BINARY'