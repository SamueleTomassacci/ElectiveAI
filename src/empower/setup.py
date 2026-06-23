import os
from glob import glob

from setuptools import find_packages, setup


package_name = "empower"


def package_files(directory):
    paths = []
    for path in glob(os.path.join(directory, "**"), recursive=True):
        if os.path.isfile(path):
            paths.append((os.path.join("share", package_name, os.path.dirname(path)), [path]))
    return paths


def existing_files(paths):
    return [path for path in paths if os.path.isfile(path)]


setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages("python"),
    package_dir={
        "empower": "python/empower",
        "efficientvit": "python/efficientvit",
        "yolo_world": "python/yolo_world",
    },
    py_modules=["agents", "detection", "loader", "models", "paths"],
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", existing_files(["package.xml", "README.md", "EMPOWER_LICENSE"])),
        (f"share/{package_name}/launch", glob("launch/*.py")),
    ]
    + package_files("config"),
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="HRAI",
    maintainer_email="student@example.com",
    description="ROS2 adapters around the upstream EMPOWER perception, grounding and planning pipeline.",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "multi_view_capture_node = empower.multi_view_capture_node:main",
            "multi_view_aggregator = empower.multi_view_aggregator:main",
            "models_cacher_node = empower.models_cacher_node:main",
            "plan_generator_node = empower.plan_generator_node:main",
            "ground_truth_localizer_node = empower.ground_truth_localizer_node:main",
            "low_level_execution_node = empower.low_level_execution_node:main",
            "mission_orchestrator_node = empower.mission_orchestrator_node:main",
        ],
    },
)
