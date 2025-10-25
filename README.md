# CSA File Compressor

A Python tool that compresses files in a folder and saves them into a single archive file (`.csa`). This project uses **multithreading** for speed and keeps track of each file's information for easy extraction later.

---

## Features

- Compresses all files in a folder (including subfolders).
- Saves everything into a single archive file.
- Keeps an **index** of all files (original size, compressed size, method).
- Uses **thread pool** to compress multiple files at the same time.
- Shows progress for UI integration.
- Easy to stop compression mid-way.

---

## How it works

```
Scan the folder -> Compress each file -> Write to archive -> Finalize archive
```

1. **Scan the folder**  
   - Walk through the folder recursively to find all files.  
   - Count the total number of files to process.

2. **Compress each file**  
   - Open the file in binary mode (`rb`).  
   - Compress using a simple core compression function (`compress_file_core`).  
   - Save the compressed blob in memory to write later.

3. **Write to archive**  
   - Start the archive with a 7-byte header:  
     - 3 bytes for a "magic number" (`CSA`)  
     - 4 bytes as a placeholder for the index size  
   - Write compressed file blobs sequentially.  
   - Store file information (offset, compressed size, original size) in an **index dictionary**.

4. **Finalize archive**  
   - Convert the index dictionary to JSON and write it at the end.  
   - Update the header with the correct index size.  
   - Emit signals for progress and completion.

---

## File Structure

```
main.py             # entry point for running the compressor
compressor.py       # contains ThreadedCompressor class
compress_file_core.py # handles the actual compression logic
```

---

## How to Use

```python
from compressor import ThreadedCompressor

root_dir = "path/to/folder"
output_file = "output.csa"

compressor = ThreadedCompressor(root_dir, output_file, max_workers=4)

# connect signals if using Qt GUI
compressor.progress.connect(lambda pct, msg: print(f"{pct}% - {msg}"))
compressor.finished.connect(lambda count: print(f"Done! {count} files compressed."))

compressor.start()
```

---

## Parameters

- `root_dir` — folder to compress  
- `out_file` — path of the `.csa` archive  
- `max_workers` — number of threads to use (default: number of CPU cores or 4)  

---

## Notes

- The archive keeps **relative paths** for each file.  
- Can stop compression mid-way using `request_stop()`.  
- Index makes it easy to extract individual files later.  
- Designed to be **RSF-free**, using only the core compression function.

---

## Example Output

```
Found 10 files. Launching 4 workers...
[1/10] file1.txt -> 1,024 bytes (method 1)
[2/10] file2.png -> 12,340 bytes (method 1)
...
Archive complete: 10 files written to output.csa
```

---

## ASCII Diagram

```
+--------------------------+
|         ARCHIVE          |
+--------------------------+
| Header (CSA + index size)|
+--------------------------+
| file1.blob               |
| file2.blob               |
| ...                      |
+--------------------------+
| Index (JSON)             |
+--------------------------+
```
- Each file is stored sequentially.  
- Index tells us where each file starts and its size.

---

## Why This Project is Useful

- Packs many files into one archive for easy sharing.  
- Keeps file info safe for later extraction.  
- Fast because it compresses multiple files at once.  
- Easy to integrate into a GUI using Qt signals.

---

## Future Ideas

- Add optional **encryption**.  
- Allow **extraction** of individual files from `.csa`.  
- Add **compression method options** (different algorithms).

