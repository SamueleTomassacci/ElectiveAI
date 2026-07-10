import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument,IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    empower_share = get_package_share_directory("empower")
    nav2_share = get_package_share_directory("nav2_bringup")

    default_params =os.path.join(empower_share, "config","nav2_tiago_ground_truth.yaml")
    default_map = "/root/exchange/exchange/maps/scene.yaml"

    params_file = LaunchConfiguration("params_file")
    map_file = LaunchConfiguration("map")
    use_sim_time =LaunchConfiguration("use_sim_time")
    autostart = LaunchConfiguration("autostart")

    return LaunchDescription(
        [
            DeclareLaunchArgument("params_file", default_value=default_params),
            DeclareLaunchArgument("map", default_value=default_map),
            DeclareLaunchArgument("use_sim_time", default_value="true"),
            DeclareLaunchArgument("autostart",default_value="true"),
            Node(
                package="nav2_map_server",
                executable="map_server",
                name="map_server",
                output="screen",
                parameters=[
                    params_file,
                    {
                        "yaml_filename": map_file,
                        "use_sim_time": use_sim_time,
                    },
                ],
            ),
            Node(
                package="nav2_lifecycle_manager",
                executable="lifecycle_manager",
                name="lifecycle_manager_map",
                output="screen",
                parameters=[
                    {
                        "use_sim_time": use_sim_time,
                        "autostart": autostart,
                        "node_names": ["map_server"],
                    }
                ],
            ),
            Node(
                package="empower",
                executable="ground_truth_localizer_node",
                name="ground_truth_localizer",
                output="screen",
                parameters=[
                    {
                        "use_sim_time": use_sim_time,
                        "ground_truth_topic": "/ground_truth_odom",
                        "map_frame": "map",
                        "odom_frame": "odom",
                        "base_frame": "base_footprint",
                    }
                ],
            ),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    os.path.join(nav2_share,"launch", "navigation_launch.py")
                ),
                launch_arguments={
                    "use_sim_time": use_sim_time,
                    "autostart": autostart,
                    "params_file": params_file,
                }.items(),
            ),
        ]
    )
