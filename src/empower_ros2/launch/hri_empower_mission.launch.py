import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    mission_params = os.path.join(
        get_package_share_directory("empower_ros2"),
        "config",
        "hri_empower_mission.yaml",
    )
    empower_params = "/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml"
    hri_params = os.path.join(
        get_package_share_directory("empower_hri"),
        "config",
        "hri_cpu_english.yaml",
    )
    use_sim_time = ParameterValue(
        LaunchConfiguration("use_sim_time"),
        value_type=bool,
    )
    stt_device_index = ParameterValue(
        LaunchConfiguration("stt_device_index"),
        value_type=int,
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument("mission_params_file", default_value=mission_params),
            DeclareLaunchArgument("empower_params_file", default_value=empower_params),
            DeclareLaunchArgument("hri_params_file", default_value=hri_params),
            DeclareLaunchArgument("start_hri", default_value="true"),
            DeclareLaunchArgument("start_models_cacher", default_value="true"),
            DeclareLaunchArgument("hri_input_mode", default_value="voice"),
            DeclareLaunchArgument("stt_device_index", default_value="8"),
            DeclareLaunchArgument("use_sim_time", default_value="true"),
            Node(
                package="empower_ros2",
                executable="models_cacher_node",
                name="empower_models_cacher",
                parameters=[
                    LaunchConfiguration("empower_params_file"),
                    {"use_sim_time": use_sim_time},
                ],
                condition=IfCondition(LaunchConfiguration("start_models_cacher")),
                output="screen",
            ),
            Node(
                package="empower_hri",
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
                package="empower_ros2",
                executable="mission_orchestrator_node",
                name="mission_orchestrator_node",
                parameters=[
                    LaunchConfiguration("mission_params_file"),
                    {
                        "params_file": LaunchConfiguration("empower_params_file"),
                        "use_sim_time": use_sim_time,
                    },
                ],
                output="screen",
            ),
        ]
    )
