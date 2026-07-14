# Single image, three ways to run it - see CMD overrides below.
# Kept as one image (not split per-process) because the scraper,
# scheduler, and API all import the same `phc_scraper` package and
# share the same dependency set; splitting would just duplicate the
# pip install layer for no real isolation benefit at this scale.
FROM python:3.12-slim AS base

# tesseract-ocr: required at runtime by phc_scraper/pdf_to_markdown.py's
# OCR fallback for scanned judgments (OCR_ENABLED=true by default - see
# .env.example). Without it, OCR attempts fail gracefully and scanned
# PDFs are skipped rather than crashing the run, but you want it
# installed for real coverage.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies in their own layer so `docker build` doesn't reinstall
# ~2GB of ML packages (sentence-transformers, torch) on every code
# change - only invalidated when requirements.txt itself changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Runs as a non-root user - the image has no reason to run as root,
# and running the scraper (which talks to a public website) as root
# is an unnecessary privilege.
RUN useradd --create-home --uid 1000 scraper \
    && mkdir -p /app/data /app/downloaded_pdfs /app/pdfs /app/markdown /app/metadata \
    && chown -R scraper:scraper /app
USER scraper

# Default: serve the API. Override CMD to run the scraper once
# ("python -m phc_scraper.cli") or the scheduler
# ("python -m phc_scraper.scheduler") from the same image instead -
# e.g. `docker run <image> python -m phc_scraper.scheduler`.
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
