from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare
from launch.substitutions import PathJoinSubstitution


def _parse_int(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _launch_setup(context, *args, **kwargs):
    params_file = LaunchConfiguration("params_file").perform(context)
    camera_index = LaunchConfiguration("camera_index").perform(context)
    device = LaunchConfiguration("device").perform(context)
    target_classes = LaunchConfiguration("target_classes").perform(context)

    overrides = {}
    if camera_index != "":
        overrides["camera_index"] = _parse_int(camera_index, 0)
    if device != "":
        overrides["device"] = device
    if target_classes != "":
        overrides["target_classes"] = [
            item.strip() for item in target_classes.split(",") if item.strip()
        ]

    return [
        Node(
            package="gaze_estimation",
            executable="gaze_webcam",
            name="gaze_estimation",
            output="screen",
            parameters=[params_file, overrides],
        )
    ]


def generate_launch_description():
    default_params = PathJoinSubstitution(
        [FindPackageShare("gaze_estimation"), "config", "gaze_webcam.yaml"]
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument("params_file", default_value=default_params),
            DeclareLaunchArgument("camera_index", default_value=""),
            DeclareLaunchArgument("device", default_value=""),
            DeclareLaunchArgument("target_classes", default_value=""),
            OpaqueFunction(function=_launch_setup),
        ]
    )
