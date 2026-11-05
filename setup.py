"""Small local package used by environment.yml to register Conda hooks.

Do not run setup.py directly; `conda env create -f environment.yml` will
install this package using pip's regular local-project build.
"""
from setuptools import setup, find_packages

setup(
    name="retinal-vasculature-setup",
    version="0.1.0",
    description="Reproducible checkpoint downloading for retinal vasculature analysis",
    packages=find_packages(include=["retinal_setup", "retinal_setup.*"]),
    package_data={"retinal_setup": ["model_weights_manifest.json"]},
    data_files=[("etc/conda/activate.d", [
        "activation_hooks/retinal-model-weights.bat",
        "activation_hooks/retinal-model-weights.sh",
    ])],
    python_requires=">=3.11",
)
