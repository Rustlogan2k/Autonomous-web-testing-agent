# The learning tester, the benchmark suite and the transfer experiment, in one image.
#
#   docker build -t web-testing-agent .
#
#   # the web UI (http://localhost:8000)
#   docker run --rm -p 8000:8000 -e OLLAMA_HOST=http://host.docker.internal:11434 web-testing-agent
#
#   # test an application
#   docker run --rm -v "$PWD/reports:/app/reports" web-testing-agent \
#       python scripts/test_app.py --url https://example.org/ --budget 400
#
#   # one experiment worker (resumable; results land in the mounted directory)
#   docker run --rm -v "$PWD/runs:/app/runs" web-testing-agent \
#       python scripts/run_transfer.py --out-dir runs/transfer_final --workers 8 --llm-offline
#
# Based on Microsoft's Playwright image so Chromium and its system libraries match the
# pinned Playwright release exactly. Headless only; no display server is needed.

FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # Ubuntu noble marks its Python as externally managed (PEP 668); this image is the
    # environment, so installing into it is the intent.
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    # The container talks to a model on the host by default; override for another host.
    OLLAMA_HOST=http://host.docker.internal:11434 \
    HF_HOME=/app/.cache/huggingface

WORKDIR /app

COPY docker/requirements-runtime.txt docker/requirements-runtime.txt
# torch is pinned to the CPU build in the same resolver run as everything else, so no
# later requirement can silently swap it for a CUDA build.
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu \
        "torch==2.6.0+cpu" -r docker/requirements-runtime.txt

# Everything else in the tree; `.dockerignore` keeps out virtualenvs, run outputs and
# downloaded papers. The frozen label projection, the label-vector store and the LLM
# judgment cache travel with it, so a worker needs neither the sentence model nor a
# language model to reproduce a run whose inputs were already judged; anything missing is
# computed on demand.
COPY . .
RUN pip install --no-deps -e .

EXPOSE 8000
CMD ["python", "-m", "web_testing_agent.app", "--host", "0.0.0.0", "--port", "8000"]
