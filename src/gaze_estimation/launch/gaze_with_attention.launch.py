from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def _parse_int(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _launch_setup(context, *args, **kwargs):
    gaze_params = LaunchConfiguration("gaze_params_file").perform(context)
    monitor_params = LaunchConfiguration("monitor_params_file").perform(context)
    camera_index = LaunchConfiguration("camera_index").perform(context)
    device = LaunchConfiguration("device").perform(context)
    target_classes = LaunchConfiguration("target_classes").perform(context)
    show_window = LaunchConfiguration("show_window").perform(context)

    gaze_overrides = {}
    if camera_index != "":
        gaze_overrides["camera_index"] = _parse_int(camera_index, 0)
    if device != "":
        gaze_overrides["device"] = device
    if target_classes != "":
        gaze_overrides["target_classes"] = [
            item.strip() for item in target_classes.split(",") if item.strip()
        ]
    if show_window != "":
        gaze_overrides["show_window"] = show_window.lower() in ("true", "1", "yes")

    nodes = [
        Node(
            package="gaze_estimation",
            executable="gaze_webcam",
            name="gaze_estimation",
            output="screen",
            parameters=[gaze_params, gaze_overrides],
        ),
        Node(
            package="gaze_estimation",
            executable="attention_monitor",
            name="attention_monitor",
            output="screen",
            parameters=[monitor_params],
        ),
    ]
    return nodes


def generate_launch_description():
    pkg_share = FindPackageShare("gaze_estimation")
    default_gaze_params = PathJoinSubstitution([pkg_share, "config", "gaze_webcam.yaml"])
    default_monitor_params = PathJoinSubstitution([pkg_share, "config", "attention_monitor.yaml"])

    return LaunchDescription(
        [
            DeclareLaunchArgument("gaze_params_file", default_value=default_gaze_params),
            DeclareLaunchArgument("monitor_params_file", default_value=default_monitor_params),
            DeclareLaunchArgument("camera_index", default_value=""),
            DeclareLaunchArgument("device", default_value=""),
            DeclareLaunchArgument("target_classes", default_value=""),
            DeclareLaunchArgument("show_window", default_value=""),
            OpaqueFunction(function=_launch_setup),
        ]
    )
