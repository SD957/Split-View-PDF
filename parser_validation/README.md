# Parser Validation

This runner reproduces parser-side observations on an arbitrary PDF. It covers
the seven open-source render/extract engines in Table 2 and the six open-source
text-extraction pipelines. Render-capable engines emit both TXT and PNG files;
text-only pipelines emit TXT files.

## Dependencies

Ubuntu packages (Node.js 20.16 or newer is required for PDF.js):

```bash
sudo apt-get install python3 python3-venv npm default-jre-headless \
  poppler-utils ghostscript curl
```

Install the seven dual-capability engines:

```bash
./setup.sh --core
```

Install those engines plus PyPDF, PDFMiner, LlamaParse/LiteParse,
OpenDataLoader, Docling (no OCR), and Unstructured (fast mode):

```bash
./setup.sh --all
```

## Run

```bash
./run.sh ../corpus_a/pdfs/EG_001_canary.pdf
```

To run all Corpus A PDFs:

```bash
./run.sh ../corpus_a/pdfs
```

Results are written under `outputs/<pdf-stem>/`. Each engine produces a TXT
file and, when rendering is supported, a `page-001.png`. `manifest.json`
records availability, warnings, errors, and output filenames.

Use `--engines` to run a subset and `--page` to render a different page:

```bash
./run.sh input.pdf --engines mupdf poppler pdfium pdfjs --page 1 --dpi 300
```

The runner records unavailable optional engines instead of aborting the batch.
No PDF generation code is included.
