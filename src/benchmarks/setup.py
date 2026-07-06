import os
from glob import glob

from setuptools import find_packages, setup


package_name = "benchmarks"


def existing_files(paths):
    return [path for path in paths if os.path.isfile(path)]


setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", existing_files(["package.xml"])),
        (os.path.join("share", package_name, "config"), glob("config/*.yaml")),
    ],
    install_requires=["setuptools", "PyYAML"],
    zip_safe=True,
    maintainer="HRAI",
    maintainer_email="student@example.com",
    description="Benchmark harness for the TIAGo therapy-room simulation.",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "therapy_benchmark = benchmarks.benchmarking:main",
        ],
    },
)
