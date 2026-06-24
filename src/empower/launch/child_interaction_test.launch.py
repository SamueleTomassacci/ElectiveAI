import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    hri_params = os.path.join(
        get_package_share_directory("llm_interaction"),
        "config",
        "llm_config.yaml",
    )
    use_sim_time = ParameterValue(
        LaunchConfiguration("use_sim_time"),
        value_type=bool,
    )
    stt_device_index = ParameterValue(
        LaunchConfiguration("stt_device_index"),
        value_type=int,
    )
    navigate_to_child = ParameterValue(
        LaunchConfiguration("navigate_to_child"),
        value_type=bool,
    )
    start_activity_on_nav_failure = ParameterValue(
        LaunchConfiguration("start_activity_on_nav_failure"),
        value_type=bool,
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument("hri_params_file", default_value=hri_params),
            DeclareLaunchArgument("start_hri", default_value="true"),
            DeclareLaunchArgument("hri_input_mode", default_value="text"),
            DeclareLaunchArgument("stt_device_index", default_value="8"),
            DeclareLaunchArgument("use_sim_time", default_value="true"),
            DeclareLaunchArgument("navigate_to_child", default_value="true"),
            DeclareLaunchArgument(
                "start_activity_on_nav_failure",
                default_value="false",
            ),
            DeclareLaunchArgument(
                "activity_description",
                default_value="Ask the child how they feel.",
            ),
            DeclareLaunchArgument("activity_type", default_value="mood_check_teddy"),
            DeclareLaunchArgument("child_name", default_value="Luca"),
            DeclareLaunchArgument(
                "task_description",
                default_value="Child-only interaction test.",
            ),
            Node(
                package="llm_interaction",
                executable="voice_llm_node",
                name="voice_llm_node",
                parameters=[
                    LaunchConfiguration("hri_params_file"),
                    {
                        "input_mode": LaunchConfiguration("hri_input_mode"),
                        "stt_device_index": stt_device_index,
                        "therapist_llm_replies": False,
                        "follow_mission_state_for_therapist_input": True,
                        "use_sim_time": use_sim_time,
                    },
                ],
                condition=IfCondition(LaunchConfiguration("start_hri")),
                output="screen",
                emulate_tty=True,
            ),
            Node(
                package="empower",
                executable="child_interaction_test_node",
                name="child_interaction_test_node",
                parameters=[
                    {
                        "use_sim_time": use_sim_time,
                        "navigate_to_child": navigate_to_child,
                        "start_activity_on_nav_failure": start_activity_on_nav_failure,
                        "activity_description": LaunchConfiguration(
                            "activity_description"
                        ),
                        "activity_type": LaunchConfiguration("activity_type"),
                        "child_name": LaunchConfiguration("child_name"),
                        "task_description": LaunchConfiguration("task_description"),
                    }
                ],
                output="screen",
            ),
        ]
    )
