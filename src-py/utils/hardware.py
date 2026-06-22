"""
utils/hardware.py

Scans the host machine and returns a HardwareProfile.
Runs once on first launch, result cached to hardware_profile.json.
The Configuration Agent (cloud LLM) reads this profile and decides
optimal model + quantization + inference engine per agent.

Supports: Windows, Linux, macOS (Apple Silicon via MLX detection).
"""

from __future__ import annotations

import json
import logging
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from core.models import GPUInfo, HardwareProfile

log = logging.getLogger(__name__)

PROFILE_PATH = Path("~/datasetter/hardware_profile.json").expanduser()


# ─── Main entry point ─────────────────────────────────────────────────────────


def scan_hardware() -> HardwareProfile:
    """
    Scan all hardware and return a HardwareProfile.
    Safe to call on any platform — gracefully handles missing tools.
    """
    log.info("Scanning hardware...")

    gpu  = _detect_gpu()
    ram  = _detect_ram()
    cpu  = _detect_cpu()
    npu_name = _detect_npu()
    os_name  = platform.system()

    # Inference engines
    has_ollama    = shutil.which("ollama") is not None
    has_llama_cpp = shutil.which("llama-server") is not None or shutil.which("llama.cpp") is not None
    has_mlx       = _has_mlx()
    has_onnx      = _has_onnx()

    tier = _compute_tier(gpu=gpu, ram_gb=ram)

    profile = HardwareProfile(
        gpu=gpu,
        ram_gb=ram,
        cpu_name=cpu,
        has_npu=npu_name is not None,
        npu_name=npu_name,
        os=os_name,
        has_ollama=has_ollama,
        has_llama_cpp=has_llama_cpp,
        has_mlx=has_mlx,
        has_onnx_runtime=has_onnx,
        tier=tier,
    )

    log.info(
        f"Hardware: {gpu.name if gpu else 'No GPU'} | "
        f"{ram:.1f}GB RAM | {cpu} | tier={tier}"
    )
    return profile


def save_profile(profile: HardwareProfile) -> None:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(profile.model_dump_json(indent=2))
    log.info(f"Hardware profile saved to {PROFILE_PATH}")


def load_profile() -> Optional[HardwareProfile]:
    if PROFILE_PATH.exists():
        try:
            data = json.loads(PROFILE_PATH.read_text())
            return HardwareProfile(**data)
        except Exception as e:
            log.warning(f"Could not load hardware profile: {e}")
    return None


def get_or_scan() -> HardwareProfile:
    """Load cached profile if available, otherwise scan."""
    cached = load_profile()
    if cached:
        log.info("Using cached hardware profile.")
        return cached
    profile = scan_hardware()
    save_profile(profile)
    return profile


# ─── GPU detection ────────────────────────────────────────────────────────────


def _detect_gpu() -> Optional[GPUInfo]:
    # Try NVIDIA first via nvidia-smi
    gpu = _nvidia_gpu()
    if gpu:
        return gpu

    # AMD via rocm-smi or wmic
    gpu = _amd_gpu()
    if gpu:
        return gpu

    # Apple Silicon — check via system_profiler
    gpu = _apple_gpu()
    if gpu:
        return gpu

    # Intel Arc / integrated — basic fallback
    gpu = _intel_gpu()
    return gpu


def _nvidia_gpu() -> Optional[GPUInfo]:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return None
        lines = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
        if not lines:
            return None
        # Take first GPU
        name, vram_mb = lines[0].rsplit(",", 1)
        vram_gb = float(vram_mb.strip()) / 1024
        return GPUInfo(name=name.strip(), vram_gb=round(vram_gb, 1), vendor="nvidia")
    except Exception:
        return None


def _amd_gpu() -> Optional[GPUInfo]:
    # Try rocm-smi (Linux)
    try:
        result = subprocess.run(
            ["rocm-smi", "--showmeminfo", "vram", "--csv"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and "vram" in result.stdout.lower():
            # Parse best-effort
            match = re.search(r"(\d+)", result.stdout)
            vram_bytes = int(match.group(1)) if match else 0
            return GPUInfo(name="AMD GPU", vram_gb=round(vram_bytes / (1024 ** 3), 1), vendor="amd")
    except Exception:
        pass

    # Windows via wmic
    if platform.system() == "Windows":
        try:
            result = subprocess.run(
                ["wmic", "path", "win32_VideoController", "get", "name,AdapterRAM"],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.splitlines():
                if "AMD" in line or "Radeon" in line:
                    parts = line.split()
                    name = " ".join(p for p in parts if not p.isdigit())
                    ram_match = re.search(r"(\d{7,})", line)
                    vram_gb = int(ram_match.group(1)) / (1024**3) if ram_match else 0
                    return GPUInfo(name=name.strip(), vram_gb=round(vram_gb, 1), vendor="amd")
        except Exception:
            pass
    return None


def _apple_gpu() -> Optional[GPUInfo]:
    if platform.system() != "Darwin":
        return None
    try:
        result = subprocess.run(
            ["system_profiler", "SPDisplaysDataType", "-json"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        displays = data.get("SPDisplaysDataType", [])
        for d in displays:
            name = d.get("sppci_model", "")
            if "Apple" in name or "M1" in name or "M2" in name or "M3" in name or "M4" in name:
                # Unified memory — report total RAM as VRAM approximation
                ram = _detect_ram()
                return GPUInfo(name=name, vram_gb=ram, vendor="apple")
    except Exception:
        pass
    return None


def _intel_gpu() -> Optional[GPUInfo]:
    if platform.system() == "Windows":
        try:
            result = subprocess.run(
                ["wmic", "path", "win32_VideoController", "get", "name,AdapterRAM"],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.splitlines():
                if "Intel" in line or "Arc" in line:
                    name = re.sub(r"\d{7,}", "", line).strip()
                    ram_match = re.search(r"(\d{7,})", line)
                    vram_gb = int(ram_match.group(1)) / (1024**3) if ram_match else 0
                    return GPUInfo(name=name, vram_gb=round(vram_gb, 1), vendor="intel")
        except Exception:
            pass
    return None


# ─── RAM ──────────────────────────────────────────────────────────────────────


def _detect_ram() -> float:
    system = platform.system()
    try:
        if system == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        kb = int(line.split()[1])
                        return round(kb / (1024 ** 2), 1)
        elif system == "Darwin":
            result = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True)
            return round(int(result.stdout.strip()) / (1024 ** 3), 1)
        elif system == "Windows":
            result = subprocess.run(
                ["wmic", "computersystem", "get", "TotalPhysicalMemory"],
                capture_output=True, text=True
            )
            for line in result.stdout.splitlines():
                if line.strip().isdigit():
                    return round(int(line.strip()) / (1024 ** 3), 1)
    except Exception:
        pass
    return 0.0


# ─── CPU ──────────────────────────────────────────────────────────────────────


def _detect_cpu() -> str:
    system = platform.system()
    try:
        if system == "Linux":
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        elif system == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True
            )
            return result.stdout.strip()
        elif system == "Windows":
            result = subprocess.run(
                ["wmic", "cpu", "get", "name"],
                capture_output=True, text=True
            )
            lines = [l.strip() for l in result.stdout.splitlines() if l.strip() and l.strip() != "Name"]
            if lines:
                return lines[0]
    except Exception:
        pass
    return platform.processor() or "Unknown CPU"


# ─── NPU ──────────────────────────────────────────────────────────────────────


def _detect_npu() -> Optional[str]:
    """
    Detect NPUs: Qualcomm Hexagon, Intel Arc NPU, Apple ANE, MediaTek APU.
    Returns name string or None.
    """
    system = platform.system()

    # Windows — check device manager via PowerShell
    if system == "Windows":
        try:
            result = subprocess.run(
                ["powershell", "-Command",
                 "Get-PnpDevice | Where-Object {$_.FriendlyName -match 'NPU|Neural|Hexagon|Neural Processing'} | Select-Object -ExpandProperty FriendlyName"],
                capture_output=True, text=True, timeout=15
            )
            name = result.stdout.strip()
            if name:
                return name.splitlines()[0]
        except Exception:
            pass

    # Linux — check /sys or lspci
    if system == "Linux":
        try:
            result = subprocess.run(["lspci"], capture_output=True, text=True, timeout=10)
            for line in result.stdout.splitlines():
                if any(k in line.lower() for k in ["npu", "neural", "hexagon", "apu"]):
                    return line.split(":", 2)[-1].strip()
        except Exception:
            pass

    # Apple — ANE is always present on Apple Silicon
    if system == "Darwin" and platform.machine() == "arm64":
        return "Apple Neural Engine"

    return None


# ─── Inference engine detection ───────────────────────────────────────────────


def _has_mlx() -> bool:
    """MLX is Apple-only."""
    if platform.system() != "Darwin":
        return False
    try:
        import importlib
        return importlib.util.find_spec("mlx") is not None
    except Exception:
        return False


def _has_onnx() -> bool:
    try:
        import importlib
        return importlib.util.find_spec("onnxruntime") is not None
    except Exception:
        return False


# ─── Tier computation ─────────────────────────────────────────────────────────


def _compute_tier(gpu: Optional[GPUInfo], ram_gb: float) -> str:
    """
    Tier definitions:
      low   — <4GB VRAM or no GPU, <8GB RAM     → cloud-heavy, minimal local
      mid   — 4-8GB VRAM, 8-16GB RAM            → can run 4B-8B local models
      high  — 8-16GB VRAM, 16-32GB RAM          → can run 13B-30B local models
      ultra — >16GB VRAM or Apple M-series, 32GB+ → can run 70B+ local models
    """
    vram = gpu.vram_gb if gpu else 0.0
    vendor = gpu.vendor if gpu else "unknown"

    # Apple Silicon — unified memory acts as VRAM
    if vendor == "apple":
        if ram_gb >= 64:
            return "ultra"
        elif ram_gb >= 32:
            return "high"
        elif ram_gb >= 16:
            return "mid"
        return "low"

    if vram >= 24 or (gpu is None and ram_gb >= 64):
        return "ultra"
    elif vram >= 8 and ram_gb >= 16:
        return "high"
    elif vram >= 4 and ram_gb >= 8:
        return "mid"
    return "low"


# ─── Compatibility check ──────────────────────────────────────────────────────


def check_model_compatibility(profile: HardwareProfile, model_name: str, quantization: Optional[str] = None) -> tuple[bool, str]:
    """
    Check whether a local model can realistically run on this hardware.
    Returns (compatible: bool, reason: str).

    Estimates based on rough parameter-to-VRAM rules:
      4-bit quantized 7B  ≈ 4GB VRAM
      4-bit quantized 13B ≈ 8GB VRAM
      4-bit quantized 30B ≈ 16GB VRAM
      4-bit quantized 70B ≈ 40GB VRAM
    """
    vram = profile.gpu.vram_gb if profile.gpu else 0.0
    ram  = profile.ram_gb

    name_lower = model_name.lower()

    # Rough size detection from model name
    if any(x in name_lower for x in ["0.5b", "1b", "1.5b", "2b", "3b"]):
        required_vram = 2.0
    elif any(x in name_lower for x in ["4b", "e2b", "e4b"]):
        required_vram = 3.0
    elif any(x in name_lower for x in ["6b", "7b", "8b", "9b"]):
        required_vram = 5.0 if "q4" in name_lower else 7.0
    elif any(x in name_lower for x in ["10b", "12b", "13b", "14b"]):
        required_vram = 8.0 if "q4" in name_lower else 12.0
    elif any(x in name_lower for x in ["20b", "24b", "26b", "27b", "30b"]):
        required_vram = 14.0 if "q4" in name_lower else 20.0
    elif any(x in name_lower for x in ["40b", "65b", "70b"]):
        required_vram = 38.0 if "q4" in name_lower else 50.0
    else:
        # Unknown — allow but warn
        return True, "Model size unknown — proceeding with caution."

    # Apple Silicon uses unified RAM
    effective_vram = ram if (profile.gpu and profile.gpu.vendor == "apple") else vram

    # Also allow CPU-only if RAM is large enough (llama.cpp can offload to CPU)
    if effective_vram >= required_vram:
        return True, "Compatible."
    elif ram >= required_vram * 1.5 and profile.has_llama_cpp:
        return True, f"Will run CPU-offloaded via llama.cpp (slower). Recommend GPU with {required_vram:.0f}GB VRAM."
    else:
        return False, (
            f"Model requires ~{required_vram:.0f}GB VRAM. "
            f"Available: {effective_vram:.1f}GB. "
            "Choose a smaller or more quantized model."
        )
