import os
from glob import glob

from setuptools import find_packages, setup


package_name = "empower_ros2"


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
        "empower_ros2": "python/empower_ros2",
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
            "scene_capture_node = empower_ros2.scene_capture_node:main",
            "models_cacher_node = empower_ros2.models_cacher_node:main",
            "plan_generator_node = empower_ros2.plan_generator_node:main",
            "export_reports = empower_ros2.export_reports:main",
            "color_pcl_node = empower_ros2.color_pcl_node:main",
            "marker_centroids_node = empower_ros2.marker_centroids_node:main",
            "ground_truth_localizer_node = empower_ros2.ground_truth_localizer_node:main",
            "low_level_execution_node = empower_ros2.low_level_execution_node:main",
        ],
    },
)
