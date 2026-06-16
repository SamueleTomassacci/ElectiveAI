import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def launch_setup(context, *args, **kwargs):
    parameters = [LaunchConfiguration("params_file")]
    task_description = LaunchConfiguration("task_description").perform(context)
    if task_description:
        parameters.append({"task_description": task_description})
    return [
        Node(
            package="empower_ros2",
            executable="plan_generator_node",
            name="empower_plan_generator",
            parameters=parameters,
            output="screen",
        ),
    ]


def generate_launch_description():
    default_params = os.path.join(get_package_share_directory("empower_ros2"), "config", "empower_person_table_child.yaml")
    return LaunchDescription(
        [
            DeclareLaunchArgument("params_file", default_value=default_params),
            DeclareLaunchArgument("task_description", default_value=""),
            OpaqueFunction(function=launch_setup),
        ]
    )
