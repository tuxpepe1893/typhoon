"""Carry upstream license notices with the frozen backend."""
from importlib.metadata import distribution
from pathlib import Path
import shutil
import sys

destination = Path(sys.argv[1])
python_license = Path(sys.base_prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "LICENSE.txt"
python_target = destination / f"Python-{sys.version.split()[0]}"
python_target.mkdir(parents=True, exist_ok=True)
shutil.copy2(python_license, python_target / "LICENSE.txt")
for package in ("broadlink", "cryptography", "cffi", "pycparser", "pyinstaller",
                "HAP-python", "chacha20poly1305-reuseable", "orjson", "zeroconf",
                "ifaddr", "h11", "base36", "PyQRCode"):
    dist = distribution(package)
    target = destination / f"{package}-{dist.version}"
    target.mkdir(parents=True, exist_ok=True)
    for item in dist.files or []:
        if item.name.lower().startswith(("license", "copying")):
            shutil.copy2(dist.locate_file(item), target / item.name)
    (target / "SOURCE.txt").write_text(f"Package: {package}\nVersion: {dist.version}\nhttps://pypi.org/project/{package}/{dist.version}/\n")
