"""
install.py -- Automated installation script for the Web Testing Agent project.

Handles the tricky ordering:
  1. Creates virtual environment (if not already in one)
  2. Installs PyTorch with CUDA support (requires special index URL)
  3. Installs all other requirements
  4. Installs Playwright browsers (Chromium)
  5. Downloads required ML models (CLIP, CodeBERT, sentence-transformers)
  6. Verifies the installation
  7. Creates the project directory structure

Usage:
    python install.py                    # Full install (auto-detects CUDA)
    python install.py --cuda 12.1        # Force specific CUDA version
    python install.py --cpu              # CPU-only install (for development)
    python install.py --skip-models      # Skip model downloads (saves time)
    python install.py --skip-docker      # Skip Docker image pulls
    python install.py --verify-only      # Only run verification checks
"""

import argparse
import json
import os
import platform
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

# ===========================================================================
# Configuration
# ===========================================================================

PROJECT_ROOT = Path(__file__).parent.resolve()

# PyTorch index URLs by CUDA version
PYTORCH_INDEX_URLS = {
    "12.1": "https://download.pytorch.org/whl/cu121",
    "12.4": "https://download.pytorch.org/whl/cu124",
    "12.6": "https://download.pytorch.org/whl/cu126",
    "cpu": "https://download.pytorch.org/whl/cpu",
}

PYTORCH_VERSION = "2.2.0"

# Models to pre-download for offline/faster startup
MODELS_TO_DOWNLOAD = {
    "clip": "openai/clip-vit-base-patch32",
    "codebert": "microsoft/codebert-base",
    "sentence-transformer": "sentence-transformers/all-MiniLM-L6-v2",
    # Phi-3 is large -- we download it separately and optionally
    # "phi3": "microsoft/Phi-3-mini-4k-instruct",
}

# Docker images for training targets
DOCKER_IMAGES = {
    "dvwa": "vulnerables/web-dvwa:latest",
    "webgoat": "webgoat/webgoat:2023.8",
    "opencart": "bitnami/opencart:4.0",
    "gitea": "gitea/gitea:1.21",
    "nextcloud": "nextcloud:28",
}

# Project directory structure
PROJECT_DIRS = [
    "src/web_testing_agent",
    "src/web_testing_agent/envs",
    "src/web_testing_agent/agents",
    "src/web_testing_agent/perception",
    "src/web_testing_agent/perception/encoders",
    "src/web_testing_agent/reward",
    "src/web_testing_agent/reporting",
    "src/web_testing_agent/payloads",
    "src/web_testing_agent/utils",
    "configs",
    "data/payloads",
    "data/training_logs",
    "data/annotations",
    "models/checkpoints",
    "models/pretrained",
    "models/reward_model",
    "tests/unit",
    "tests/integration",
    "tests/fixtures",
    "notebooks",
    "scripts",
    "docker",
    "reports",
    "logs",
]


# ===========================================================================
# Helpers
# ===========================================================================


def print_banner(text: str) -> None:
    """Print a formatted section banner."""
    width = 70
    print("\n" + "=" * width)
    print(f"  {text}")
    print("=" * width)


def print_step(step: int, total: int, text: str) -> None:
    """Print a formatted step indicator."""
    print(f"\n[{step}/{total}] {text}")


def print_success(text: str) -> None:
    print(f"  [OK] {text}")


def print_warning(text: str) -> None:
    print(f"  [WARN] {text}")


def print_error(text: str) -> None:
    print(f"  [FAIL] {text}")


def run_cmd(
    cmd: list[str],
    check: bool = True,
    capture: bool = False,
    env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run a command with proper error handling."""
    merged_env = {**os.environ, **(env or {})}
    print(f"  -> Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            check=check,
            capture_output=capture,
            text=True,
            env=merged_env,
            cwd=str(PROJECT_ROOT),
        )
        return result
    except subprocess.CalledProcessError as e:
        print_error(f"Command failed with exit code {e.returncode}")
        if e.stdout:
            print(f"    stdout: {e.stdout[:500]}")
        if e.stderr:
            print(f"    stderr: {e.stderr[:500]}")
        raise


def detect_cuda_version() -> str | None:
    """Detect installed CUDA version from nvidia-smi."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=True,
        )
        # nvidia-smi works -- now get CUDA version
        result2 = subprocess.run(
            ["nvidia-smi"],
            capture_output=True,
            text=True,
            check=True,
        )
        output = result2.stdout
        # Parse "CUDA Version: 12.x" from nvidia-smi output
        for line in output.split("\n"):
            if "CUDA Version" in line:
                parts = line.split("CUDA Version:")
                if len(parts) > 1:
                    version = parts[1].strip().split()[0]
                    return version
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    return None


def get_pytorch_index_url(cuda_version: str | None, force_cpu: bool) -> tuple[str, str]:
    """Determine the correct PyTorch index URL."""
    if force_cpu:
        return PYTORCH_INDEX_URLS["cpu"], "cpu"

    if cuda_version is None:
        print_warning("No CUDA detected. Installing CPU-only PyTorch.")
        print_warning("For GPU training, install CUDA toolkit and re-run with --cuda <version>")
        return PYTORCH_INDEX_URLS["cpu"], "cpu"

    major_minor = ".".join(cuda_version.split(".")[:2])
    print(f"  Detected CUDA version: {cuda_version}")

    # Map to closest supported PyTorch CUDA build
    cuda_major = int(major_minor.split(".")[0])
    cuda_minor = int(major_minor.split(".")[1])

    if cuda_major >= 13:
        # CUDA 13.x+ -- use the latest available PyTorch CUDA build
        key = "12.6"
    elif cuda_major == 12:
        if cuda_minor >= 4:
            key = "12.4"
        else:
            key = "12.1"
    elif cuda_major == 11:
        # PyTorch 2.2 supports CUDA 11.8
        print_warning(f"CUDA {major_minor} detected. PyTorch 2.2+ works best with CUDA 12.x.")
        print_warning("Consider upgrading CUDA. Falling back to cu121 build.")
        key = "12.1"
    else:
        print_warning(f"Unusual CUDA version {major_minor}. Defaulting to cu121 build.")
        key = "12.1"

    print(f"  Using PyTorch CUDA build: cu{key.replace('.', '')}")
    return PYTORCH_INDEX_URLS[key], key


def check_python_version() -> None:
    """Ensure Python >= 3.11."""
    major, minor = sys.version_info[:2]
    if major < 3 or (major == 3 and minor < 11):
        print_error(f"Python 3.11+ required, found {major}.{minor}")
        sys.exit(1)
    print_success(f"Python {major}.{minor}.{sys.version_info[2]}")


def check_architecture() -> None:
    """Check we're on 64-bit."""
    bits = struct.calcsize("P") * 8
    if bits != 64:
        print_error(f"64-bit Python required, found {bits}-bit")
        sys.exit(1)
    print_success(f"{bits}-bit Python on {platform.machine()}")


# ===========================================================================
# Installation Steps
# ===========================================================================


def create_project_structure() -> None:
    """Create the full project directory structure."""
    print_step(1, 7, "Creating project directory structure...")
    for dir_path in PROJECT_DIRS:
        full_path = PROJECT_ROOT / dir_path
        full_path.mkdir(parents=True, exist_ok=True)
        # Create __init__.py for Python packages
        if dir_path.startswith("src/") and not dir_path.endswith(
            ("configs", "data", "models", "notebooks", "scripts", "docker", "reports", "logs")
        ):
            init_file = full_path / "__init__.py"
            if not init_file.exists():
                init_file.write_text(
                    f'"""Package: {dir_path.split("/")[-1]}"""\n', encoding="utf-8"
                )
        # Create __init__.py for test directories
        if dir_path.startswith("tests/"):
            init_file = full_path / "__init__.py"
            if not init_file.exists():
                init_file.write_text("", encoding="utf-8")

    # Create .gitkeep for empty data/model dirs
    for keep_dir in [
        "data/training_logs",
        "data/annotations",
        "models/checkpoints",
        "models/pretrained",
        "models/reward_model",
        "reports",
        "logs",
    ]:
        gitkeep = PROJECT_ROOT / keep_dir / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.write_text("", encoding="utf-8")

    print_success("Project structure created")


def install_pytorch(index_url: str, cuda_key: str) -> None:
    """Install PyTorch with the correct CUDA support."""
    print_step(2, 7, f"Installing PyTorch {PYTORCH_VERSION} (CUDA: {cuda_key})...")

    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        f"torch>={PYTORCH_VERSION}",
        f"torchvision",
        f"torchaudio",
        "--index-url",
        index_url,
    ]
    run_cmd(cmd)
    print_success("PyTorch installed")


def install_requirements() -> None:
    """Install all other requirements."""
    print_step(3, 7, "Installing project requirements...")

    requirements_file = PROJECT_ROOT / "requirements.txt"
    if not requirements_file.exists():
        print_error("requirements.txt not found!")
        sys.exit(1)

    # Install with constraints to avoid conflicts
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "-r",
        str(requirements_file),
    ]
    run_cmd(cmd)

    # Install the project itself in editable mode
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "-e",
        str(PROJECT_ROOT),
    ]
    run_cmd(cmd, check=False)  # May fail if setup.py not ready yet, that's OK

    print_success("Requirements installed")


def install_playwright_browsers() -> None:
    """Install Playwright and download Chromium."""
    print_step(4, 7, "Installing Playwright browsers (Chromium)...")

    cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    run_cmd(cmd)

    # Also install system dependencies on Linux
    if platform.system() == "Linux":
        print("  Installing Playwright system dependencies (may require sudo)...")
        cmd = [sys.executable, "-m", "playwright", "install-deps", "chromium"]
        run_cmd(cmd, check=False)

    print_success("Playwright Chromium installed")


def download_models(skip: bool = False) -> None:
    """Pre-download ML models for faster startup."""
    print_step(5, 7, "Downloading ML models...")

    if skip:
        print_warning("Skipping model downloads (--skip-models flag)")
        print_warning("Models will be downloaded on first use (slower initial run)")
        return

    models_dir = PROJECT_ROOT / "models" / "pretrained"
    models_dir.mkdir(parents=True, exist_ok=True)

    for model_key, model_name in MODELS_TO_DOWNLOAD.items():
        print(f"\n  Downloading {model_key}: {model_name}...")
        try:
            if model_key == "clip":
                # Use transformers to download CLIP
                from transformers import CLIPModel, CLIPProcessor

                CLIPProcessor.from_pretrained(model_name)
                CLIPModel.from_pretrained(model_name)
                print_success(f"{model_key} downloaded and cached")

            elif model_key == "codebert":
                from transformers import AutoModel, AutoTokenizer

                AutoTokenizer.from_pretrained(model_name)
                AutoModel.from_pretrained(model_name)
                print_success(f"{model_key} downloaded and cached")

            elif model_key == "sentence-transformer":
                # pyrefly: ignore [missing-import]
                from sentence_transformers import SentenceTransformer

                SentenceTransformer(model_name)
                print_success(f"{model_key} downloaded and cached")

        except Exception as e:
            print_warning(f"Failed to download {model_key}: {e}")
            print_warning("Model will be downloaded on first use")


def pull_docker_images(skip: bool = False) -> None:
    """Pull Docker images for training targets."""
    print_step(6, 7, "Pulling Docker images for training targets...")

    if skip:
        print_warning("Skipping Docker image pulls (--skip-docker flag)")
        return

    # Check if Docker is available
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, check=True
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        print_warning("Docker not found or not running. Skipping image pulls.")
        print_warning("Install Docker Desktop and re-run to pull training target images.")
        return

    for name, image in DOCKER_IMAGES.items():
        print(f"\n  Pulling {name}: {image}...")
        try:
            run_cmd(["docker", "pull", image])
            print_success(f"{name} image pulled")
        except subprocess.CalledProcessError:
            print_warning(f"Failed to pull {name}. You can pull it manually later:")
            print_warning(f"  docker pull {image}")


def verify_installation() -> None:
    """Verify that all critical components are working."""
    print_step(7, 7, "Verifying installation...")

    results = {}
    total = 0
    passed = 0

    checks = [
        ("Python version", "import sys; assert sys.version_info >= (3, 11)"),
        ("PyTorch", "import torch; print(f'  PyTorch {torch.__version__}')"),
        (
            "CUDA available",
            "import torch; print(f'  CUDA: {torch.cuda.is_available()}, Devices: {torch.cuda.device_count() if torch.cuda.is_available() else 0}')",
        ),
        ("Gymnasium", "import gymnasium; print(f'  Gymnasium {gymnasium.__version__}')"),
        (
            "Stable Baselines 3",
            "import stable_baselines3; print(f'  SB3 {stable_baselines3.__version__}')",
        ),
        ("Playwright", "import playwright; print(f'  Playwright available')"),
        (
            "Transformers",
            "import transformers; print(f'  Transformers {transformers.__version__}')",
        ),
        (
            "Sentence Transformers",
            "import sentence_transformers; print(f'  Sentence Transformers {sentence_transformers.__version__}')",
        ),
        ("PEFT (LoRA)", "import peft; print(f'  PEFT {peft.__version__}')"),
        ("Weights & Biases", "import wandb; print(f'  W&B {wandb.__version__}')"),
        ("Rich", "import rich; print(f'  Rich {rich.__version__}')"),
        ("Loguru", "import loguru; print(f'  Loguru available')"),
        ("BeautifulSoup", "import bs4; print(f'  BS4 {bs4.__version__}')"),
        ("scikit-image", "import skimage; print(f'  scikit-image {skimage.__version__}')"),
        ("Docker SDK", "import docker; print(f'  Docker SDK {docker.__version__}')"),
        ("Pydantic", "import pydantic; print(f'  Pydantic {pydantic.__version__}')"),
    ]

    for name, check_code in checks:
        total += 1
        try:
            exec(check_code)
            results[name] = True
            passed += 1
        except Exception as e:
            results[name] = False
            print_error(f"{name}: {e}")

    # Summary
    print(f"\n  Verification: {passed}/{total} checks passed")

    if passed == total:
        print_success("All checks passed! Installation complete.")
    else:
        failed = [name for name, ok in results.items() if not ok]
        print_warning(f"Failed checks: {', '.join(failed)}")
        print_warning("Some packages may need manual installation.")

    return results


# ===========================================================================
# .gitignore and .env template
# ===========================================================================


def create_gitignore() -> None:
    """Create a comprehensive .gitignore."""
    gitignore_path = PROJECT_ROOT / ".gitignore"
    if gitignore_path.exists():
        return

    gitignore_path.write_text(
        """\
# Python
__pycache__/
*.py[cod]
*$py.class
*.egg-info/
dist/
build/
*.egg
.eggs/

# Virtual environments
venv/
.venv/
env/

# IDE
.vscode/
.idea/
*.swp
*.swo
*~

# OS
.DS_Store
Thumbs.db

# ML Models (large files -- use Git LFS or download via script)
models/pretrained/
models/checkpoints/*.pt
models/checkpoints/*.pth
models/reward_model/*.bin
*.safetensors
*.gguf

# Data
data/training_logs/
data/annotations/*.json
!data/annotations/.gitkeep

# Logs
logs/
*.log
wandb/

# Reports (generated)
reports/*.html
reports/*.pdf

# Environment variables
.env
.env.local

# Jupyter
.ipynb_checkpoints/

# Docker
docker-compose.override.yml

# Playwright
playwright-report/
test-results/
""",
        encoding="utf-8",
    )
    print_success(".gitignore created")


def create_env_template() -> None:
    """Create .env.example template."""
    env_path = PROJECT_ROOT / ".env.example"
    if env_path.exists():
        return

    env_path.write_text(
        """\
# ============================================================================
# Web Testing Agent -- Environment Configuration
# ============================================================================
# Copy this to .env and fill in your values:
#   cp .env.example .env
# ============================================================================

# --- Weights & Biases ---
WANDB_API_KEY=your_wandb_api_key_here
WANDB_PROJECT=web-testing-agent
WANDB_ENTITY=your_wandb_username

# --- Target Application URLs (when running locally) ---
DVWA_URL=http://localhost:4280
WEBGOAT_URL=http://localhost:8080/WebGoat
OPENCART_URL=http://localhost:8090
GITEA_URL=http://localhost:3000
NEXTCLOUD_URL=http://localhost:8080

# --- Model Paths (override defaults) ---
# CLIP_MODEL_NAME=openai/clip-vit-base-patch32
# CODEBERT_MODEL_NAME=microsoft/codebert-base
# SENTENCE_TRANSFORMER_NAME=sentence-transformers/all-MiniLM-L6-v2
# PHI3_MODEL_NAME=microsoft/Phi-3-mini-4k-instruct

# --- vLLM Reward Server ---
VLLM_HOST=localhost
VLLM_PORT=8000

# --- GPU Configuration ---
# CUDA_VISIBLE_DEVICES=0

# --- Logging ---
LOG_LEVEL=INFO
""",
        encoding="utf-8",
    )
    print_success(".env.example created")


def create_docker_compose() -> None:
    """Create docker-compose for training target applications."""
    compose_path = PROJECT_ROOT / "docker" / "docker-compose.yml"
    if compose_path.exists():
        return

    compose_path.parent.mkdir(parents=True, exist_ok=True)
    compose_path.write_text(
        """\
# ============================================================================
# Docker Compose -- Training Target Applications
# ============================================================================
# Usage:
#   cd docker
#   docker compose up -d dvwa          # Start DVWA only (Agent A training)
#   docker compose up -d opencart      # Start OpenCart (Agent B training)
#   docker compose up -d               # Start everything
#   docker compose down                # Stop all
# ============================================================================

services:
  # === Agent A Training Targets ===

  dvwa:
    image: vulnerables/web-dvwa:latest
    container_name: wta-dvwa
    ports:
      - "4280:80"
    environment:
      - MYSQL_HOSTNAME=dvwa-db
      - MYSQL_DATABASE=dvwa
      - MYSQL_USERNAME=dvwa
      - MYSQL_PASSWORD=p@ssw0rd
    depends_on:
      - dvwa-db
    restart: unless-stopped

  dvwa-db:
    image: mysql:8.0
    container_name: wta-dvwa-db
    environment:
      - MYSQL_ROOT_PASSWORD=rootpassword
      - MYSQL_DATABASE=dvwa
      - MYSQL_USER=dvwa
      - MYSQL_PASSWORD=p@ssw0rd
    volumes:
      - dvwa-db-data:/var/lib/mysql
    restart: unless-stopped

  webgoat:
    image: webgoat/webgoat:2023.8
    container_name: wta-webgoat
    ports:
      - "8080:8080"
      - "9090:9090"
    restart: unless-stopped

  # === Agent B Training Targets ===

  opencart:
    image: bitnami/opencart:4.0
    container_name: wta-opencart
    ports:
      - "8090:8080"
      - "8443:8443"
    environment:
      - OPENCART_HOST=localhost:8090
      - OPENCART_DATABASE_HOST=opencart-db
      - OPENCART_DATABASE_PORT_NUMBER=3306
      - OPENCART_DATABASE_USER=opencart
      - OPENCART_DATABASE_PASSWORD=opencart_pass
      - OPENCART_DATABASE_NAME=opencart
    depends_on:
      - opencart-db
    restart: unless-stopped

  opencart-db:
    image: mysql:8.0
    container_name: wta-opencart-db
    environment:
      - MYSQL_ROOT_PASSWORD=rootpassword
      - MYSQL_DATABASE=opencart
      - MYSQL_USER=opencart
      - MYSQL_PASSWORD=opencart_pass
    volumes:
      - opencart-db-data:/var/lib/mysql
    restart: unless-stopped

  gitea:
    image: gitea/gitea:1.21
    container_name: wta-gitea
    ports:
      - "3000:3000"
      - "2222:22"
    environment:
      - GITEA__database__DB_TYPE=sqlite3
    volumes:
      - gitea-data:/data
    restart: unless-stopped

  nextcloud:
    image: nextcloud:28
    container_name: wta-nextcloud
    ports:
      - "8081:80"
    environment:
      - SQLITE_DATABASE=nextcloud
      - NEXTCLOUD_ADMIN_USER=admin
      - NEXTCLOUD_ADMIN_PASSWORD=admin
    volumes:
      - nextcloud-data:/var/www/html
    restart: unless-stopped

volumes:
  dvwa-db-data:
  opencart-db-data:
  gitea-data:
  nextcloud-data:
""",
        encoding="utf-8",
    )
    print_success("docker-compose.yml created")


# ===========================================================================
# Main
# ===========================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Install Web Testing Agent dependencies",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--cuda",
        type=str,
        default=None,
        help="Force specific CUDA version (e.g., 12.1, 12.4)",
    )
    parser.add_argument(
        "--cpu",
        action="store_true",
        help="Install CPU-only PyTorch (for development without GPU)",
    )
    parser.add_argument(
        "--skip-models",
        action="store_true",
        help="Skip downloading ML models (they'll download on first use)",
    )
    parser.add_argument(
        "--skip-docker",
        action="store_true",
        help="Skip pulling Docker images",
    )
    parser.add_argument(
        "--skip-pytorch",
        action="store_true",
        help="Skip PyTorch installation (if already installed)",
    )
    parser.add_argument(
        "--skip-requirements",
        action="store_true",
        help="Skip project requirements installation (pip install -r requirements.txt)",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only run verification checks",
    )
    args = parser.parse_args()

    print_banner("Web Testing Agent -- Installation")
    print(f"  Platform: {platform.system()} {platform.release()}")
    print(f"  Python:   {sys.version}")
    print(f"  Project:  {PROJECT_ROOT}")

    if args.verify_only:
        verify_installation()
        return

    # Pre-flight checks
    print_banner("Pre-flight Checks")
    check_python_version()
    check_architecture()

    # Detect or set CUDA
    if args.cuda:
        cuda_version = args.cuda
        print(f"  Using forced CUDA version: {cuda_version}")
    else:
        cuda_version = detect_cuda_version()

    index_url, cuda_key = get_pytorch_index_url(cuda_version, args.cpu)

    # Run installation steps
    start_time = time.time()

    print_banner("Installation")

    create_project_structure()

    if not args.skip_pytorch:
        install_pytorch(index_url, cuda_key)

    if not args.skip_requirements:
        install_requirements()
        install_playwright_browsers()
    else:
        print_warning("Skipping requirements and Playwright installation (--skip-requirements flag)")
    download_models(skip=args.skip_models)
    pull_docker_images(skip=args.skip_docker)

    # Create project files
    create_gitignore()
    create_env_template()
    create_docker_compose()

    # Verify
    print_banner("Verification")
    verify_installation()

    elapsed = time.time() - start_time
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)

    print_banner("Installation Complete!")
    print(f"  Total time: {minutes}m {seconds}s")
    print()
    print("  Next steps:")
    print("  1. Copy .env.example to .env and fill in your W&B API key:")
    print("       cp .env.example .env")
    print("  2. Start training targets:")
    print("       cd docker && docker compose up -d dvwa")
    print("  3. Begin Phase 1 implementation:")
    print("       python -m web_testing_agent.envs.base_env  # test browser env")
    print()


if __name__ == "__main__":
    main()
