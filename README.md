# 📂 File & Archive Explorer

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![Flet](https://img.shields.io/badge/Flet-1.7.0+-green.svg)](https://flet.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A **modern, efficient, and user-friendly file and archive explorer** built with **Python & Flet**. Navigate directories asynchronously, browse archive indexes without extraction, and access detailed file metadata—all in a responsive GUI.

---

## 📝 Table of Contents

* [Features](#features)
* [Installation](#installation)
* [Usage](#usage)
* [Configuration](#configuration)
* [Developer Notes](#developer-notes)
* [Contributing](#contributing)
* [License](#license)
* [Screenshots](#screenshots)

---

## 🌟 Features

### File Explorer

* Navigate local directories with intuitive UI.
* Parent directory navigation (`..`) and **breadcrumb/back buttons**.
* Async folder loading with **progress indicators**.
* Distinguishes **folders, files, and parent directories** via icons and colors.
* Handles permission errors and empty directories gracefully.

### Archive Explorer

* Browse archive indexes **without extraction**.
* Supports multiple compression methods:

  * **DICOM**, **LZMA**, **ZLIB**, **STORE**, **RSF**
* Displays **original & compressed file sizes** and **compression ratios**.
* Clickable files for selection logging.

### Logging & Error Handling

* Logs navigation events, file selections, and errors.
* Graceful handling of:

  * Permission issues
  * Missing directories
  * Archive load failures

### Technical Highlights

* Asynchronous folder and archive loading using Python threads.
* Safe GUI updates via `safe_update` to prevent threading issues.
* Fully color-coded UI for folders, files, errors, and progress indicators.
* Easily extendable for custom archive formats and compression algorithms.

---

## ⚙️ Installation

1. Clone the repository:

```bash
git clone https://github.com/yourusername/file-archive-explorer.git
cd file-archive-explorer
```

2. Activate your virtual environment:

```bash
# Linux / macOS
source venv/bin/activate

# Windows
venv\Scripts\activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

---

## 🚀 Usage

Run the app:

```bash
python main.py
```

* Click folders to navigate inside.
* Click files to log selection.
* Load archives to browse contents without extraction.
* Use the **Back button** or **breadcrumb** to navigate history.

---

## 🛠 Configuration

* **Colors & Themes:** Adjust constants for text, background, accent, errors, and surfaces.
* **Archive Methods Mapping:** Easily extendable in `load_explorer_archive`.
* **Navigation History:** Controlled via `explorer_path_stack` for back button functionality.

---

## 🧑‍💻 Developer Notes

This app demonstrates several advanced techniques in Python GUI development:

1. **Asynchronous Loading:**

   * Both folder and archive loading run in **daemon threads** to keep the UI responsive.
   * Progress indicators are shown during loading.

2. **Safe UI Updates:**

   * GUI updates are wrapped in `safe_update` to avoid **threading conflicts**.

3. **Navigation Stack:**

   * `explorer_path_stack` stores navigation history for back button functionality.
   * Supports **parent directory (`..`) logic** to navigate up the file tree.

4. **Error Handling:**

   * Permission denied, missing folders, and archive read errors are **gracefully captured** and displayed in the UI.
   * All exceptions are logged for debugging.

5. **Archive Index Support:**

   * Load indexes from compressed archives without extraction.
   * Displays **method type, original/compressed size, and compression ratio**.

---

## 🤝 Contributing

Contributions are welcome!

1. Fork the repository.
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Commit your changes: `git commit -am "Add feature"`
4. Push: `git push origin feature/my-feature`
5. Open a Pull Request.

---

## 📄 License

This project is licensed under the **MIT License** – see the [LICENSE](LICENSE) file for details.

---

## 🖼 Screenshots

*Add screenshots of your app UI here for a professional touch.*
