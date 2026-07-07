import os
from glob import glob

from setuptools import find_packages, setup


package_name = "llm_interaction"


def existing_files(paths):
    return [path for path in paths if os.path.isfile(path)]


setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (
            f"share/{package_name}",
            existing_files(
                [
                    "package.xml",
                    "README.md",
                    "README_QWEN_ENGLISH.md",
                    "README_CHILD_MEMORY.md",
                ]
            ),
        ),
        (os.path.join("share", package_name, "config"), glob("config/*.yaml")),
        (os.path.join("share", package_name, "launch"), glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools", "rdflib", "Pillow"],
    zip_safe=True,
    maintainer="HRAI",
    maintainer_email="student@example.com",
    description="CPU-only local voice dialogue MVP for TIAGo and EMPOWER.",
    license="Apache-2.0",
    test_suite="test",
    entry_points={
        "console_scripts": [
            "dialog_bubble_node = llm_interaction.dialog_bubble_node:main",
            "voice_llm_node = llm_interaction.voice_llm_node:main",
        ],
    },
)
