# UTAR Past-Year Paper Auto-Scraper 🎓

![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg)
![Contributions welcome](https://img.shields.io/badge/Contributions-welcome-orange.svg) 
![License](https://img.shields.io/badge/License-MIT-blue.svg)

A high-performance, multithreaded web scraper built specifically for Universiti Tunku Abdul Rahman (UTAR) students to instantly batch-download Past Year Examination Papers securely from the UTAR portal. 

The scraper features an intelligent **Adaptive Search Engine** designed to unearth hidden course codes and a **Local OCR Engine** to automatically convert scanned exams into searchable text documents.

---

## ⚡ Run Instantly in Google Colab
Don't want to install anything on your machine? Run the scraper directly in your browser using Google Colab!

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/drive/1hyT_5WTT1M1G1lZkxJ8BEMwC9Bd41nV6?usp=sharing)

---

## ✨ Core Features

* **Adaptive Dual-Engine Search**: Effortlessly switch between "Directory Crawl" to dump massive loads of degree-wide subject PDFs, or utilize the internal "Quick Search" algorithm to explicitly uncover *hidden/decoupled* subjects that don't appear in the main directory tree.
* **Concurrent Async Networking**: Deploys a multithreaded network pool architecture spanning out up to 100 threads to mass-download hundreds of PDF files synchronously, slashing download times.
* **Smart OCR Preprocessing**: Integrates with [OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF). Seamlessly convert legacy scanned PDF images into fully text-searchable PDFs locally—so you are never hindered from `Ctrl+F`'ing through dense exam papers again. 
* **Self-Healing Session Control**: Bypasses typical networking latency with an iterative retry strategy, effectively bypassing Portal `429`, `500` and `504` errors so you never wake up to an interrupted download batch. 

---

## 🚀 Local Installation & Usage

### 1. Install Dependencies
Ensure you have Python 3.8+ installed. You will need `requests`, `beautifulsoup4`, and [ocrmypdf](file:///C:/Users/kingy/.gemini/antigravity/brain/f8cfad12-fd87-45f3-a7fd-3bbe3778d143/utar_scraper_fixed.py#49-56) (if you plan to use the OCR features).

```bash
pip install requests beautifulsoup4
```

*(Optional) For OCR Features:*
You must install Tesseract and Ghostscript on your system before installing the python wrapper.
* **Ubuntu/Colab:** `sudo apt-get install -y tesseract-ocr ghostscript qpdf`
* **Windows/Mac:** Follow the [OCRmyPDF Installation Guide](https://ocrmypdf.readthedocs.io/en/latest/installation.html).
```bash
pip install ocrmypdf
```

### 2. Authenticate with UTAR Portal
Log into the official UTAR student portal using your web browser. 
1. Press `F12` to open Developer Tools.
2. Go to the **Network** tab.
3. Refresh the page and click on the main request.
4. Scroll down to "Request Headers" and copy your `JSESSIONID` cookie string.

### 3. Execute the Engine
Run the interface script and simply follow the on-screen prompts! 

```bash
python utar_scraper_fixed.py
```

**Mode Examples**: 
- **Full Dump**: Leave the "Target Subject" blank to deep-crawl every single foundation/bachelor subject. 
- **Targeted Dump**: Type in your course code (e.g., `EHEL1024`) to run a targeted extraction pipeline globally.
- **Searchable Formats**: Choose `OCR` at startup if you'd like your PDFs locally converted into readable text arrays seamlessly.

---

## 📝 Disclaimer & Legal Note
This scraper respects the UTAR secure portal endpoints and is specifically designed strictly for educational backing capabilities. Active authentication (User's own student cookie `JSESSIONID`) is required explicitly per session to authorize access. It does not exploit unauthenticated access or bypass security protocols. Always uphold data security standards and use responsibly. Use at your own risk.
