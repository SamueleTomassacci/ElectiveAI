import os
from glob import glob

from setuptools import find_packages,setup


package_name = "gaze_estimation"


def existing_files(paths):
    return [path for path in paths if os.path.isfile(path)]


setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages",[f"resource/{package_name}"]),
        (f"share/{package_name}", existing_files(["package.xml","README.md"])),
        (os.path.join("share", package_name, "config"), glob("config/*.yaml")),
        (os.path.join("share", package_name,"launch"),glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="HRAI",
    maintainer_email="student@example.com",
    description="Standalone ROS 2 webcam gaze target estimation package.",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "gaze_webcam = gaze_estimation.gaze_webcam_node:main",
            "attention_monitor = gaze_estimation.attention_monitor_node:main",
        ],
    },
)
