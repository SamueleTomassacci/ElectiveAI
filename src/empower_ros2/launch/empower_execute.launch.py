import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    default_params = os.path.join(get_package_share_directory("empower_ros2"), "config", "empower_tiago.yaml")
    use_sim_time = LaunchConfiguration("use_sim_time")
    return LaunchDescription(
        [
            DeclareLaunchArgument("params_file", default_value=default_params),
            DeclareLaunchArgument("use_sim_time", default_value="true"),
            Node(
                package="empower_ros2",
                executable="color_pcl_node",
                name="empower_color_pcl",
                parameters=[LaunchConfiguration("params_file"), {"use_sim_time": use_sim_time}],
                output="screen",
            ),
            Node(
                package="empower_ros2",
                executable="marker_centroids_node",
                name="empower_marker_centroids",
                parameters=[LaunchConfiguration("params_file"), {"use_sim_time": use_sim_time}],
                output="screen",
            ),
            Node(
                package="empower_ros2",
                executable="low_level_execution_node",
                name="empower_low_level_execution",
                parameters=[LaunchConfiguration("params_file"), {"use_sim_time": use_sim_time}],
                output="screen",
            ),
        ]
    )
