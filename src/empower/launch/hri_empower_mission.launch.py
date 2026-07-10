import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    mission_params= os.path.join(
        get_package_share_directory("empower"),
        "config",
        "task_config.yaml",
    )
    empower_params = "/root/exchange/src/empower/config/empower_config.yaml"
    static_scene_graph = os.path.join(
        get_package_share_directory("empower"),
        "config",
        "scenes",
        "therapy_room_scene_graph.json",
    )

    hri_params =os.path.join(
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

    return LaunchDescription(
        [
            DeclareLaunchArgument("mission_params_file",default_value=mission_params),
            DeclareLaunchArgument("empower_params_file", default_value=empower_params),
            DeclareLaunchArgument("static_scene_graph_path",default_value=static_scene_graph),
            DeclareLaunchArgument("hri_params_file",default_value=hri_params),
            DeclareLaunchArgument("start_hri",default_value="true"),
            DeclareLaunchArgument("start_dialog_bubbles", default_value="true"),
            DeclareLaunchArgument("dialog_bubble_yaw",default_value="1.35"),
            DeclareLaunchArgument("start_models_cacher", default_value="true"),
            DeclareLaunchArgument("hri_input_mode", default_value="voice"),
            DeclareLaunchArgument("therapist_input_enabled",default_value="true"),
            DeclareLaunchArgument("benchmark_child_utterances_enabled", default_value="false"),
            DeclareLaunchArgument("benchmark_child_utterance_timeout_sec",default_value="120.0"),
            DeclareLaunchArgument("stt_device_index", default_value="8"),
            DeclareLaunchArgument("use_sim_time", default_value="true"),
            DeclareLaunchArgument("skip_to_child_activity",default_value="false"),
            DeclareLaunchArgument("test_child_name", default_value="Luca"),
            DeclareLaunchArgument("test_activity_type",default_value="storytelling"),
            DeclareLaunchArgument("test_task_description",default_value="Take the book and give it to the child"),
            Node(
                package="empower",
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
                package="llm_interaction",
                executable="voice_llm_node",
                name="voice_llm_node",
                parameters=[
                    LaunchConfiguration("hri_params_file"),
                    {
                        "input_mode": LaunchConfiguration("hri_input_mode"),
                        "stt_device_index": stt_device_index,
                        "therapist_llm_replies": False,
                        "therapist_input_enabled": ParameterValue(
                            LaunchConfiguration("therapist_input_enabled"), value_type=bool,
                        ),
                        "follow_mission_state_for_therapist_input": True,
                        "benchmark_child_utterances_enabled": ParameterValue(
                            LaunchConfiguration("benchmark_child_utterances_enabled"),value_type=bool,
                        ),
                        "benchmark_child_utterance_timeout_sec": ParameterValue(
                            LaunchConfiguration("benchmark_child_utterance_timeout_sec"), value_type=float,
                        ),
                        "dialog_bubble_sync_enabled": ParameterValue(
                            LaunchConfiguration("start_dialog_bubbles"), value_type=bool,
                        ),
                        "dialog_bubble_ready_timeout_sec": 300.0,
                        "use_sim_time": use_sim_time,
                    },
                ],
                condition=IfCondition(LaunchConfiguration("start_hri")),
                output="screen",
                emulate_tty=True,
            ),
            Node(
                package="llm_interaction",
                executable="dialog_bubble_node",
                name="dialog_bubble_node",
                parameters=[
                    {
                        "use_sim_time": use_sim_time,
                        "world_frame": "map",
                        "robot_frame": "base_footprint",
                        "robot_offset_xyz": [0.0, 0.0,2.15],
                        "robot_anchor_xyz": [0.0, 0.0, 2.15],
                        "adult_anchor_xyz": [0.0, -2.75,2.15],
                        "child_anchor_xyz": [0.0, 2.55,1.75],
                        "bubble_yaw": ParameterValue(
                            LaunchConfiguration("dialog_bubble_yaw"), value_type=float,
                        ),
                        "bubble_width_m": 1.9,
                        "bubble_height_m": 0.75,
                        "bubble_lifetime_sec": 12.0,
                        "gazebo_command": "gz",
                        "gazebo_world_name": "scene",
                    },
                ],
                condition=IfCondition(LaunchConfiguration("start_dialog_bubbles")),
                output="screen",
            ),
            Node(
                package="empower",
                executable="mission_orchestrator_node",
                name="mission_orchestrator_node",
                parameters=[
                    LaunchConfiguration("mission_params_file"),
                    {
                        "params_file": LaunchConfiguration("empower_params_file"),
                        "static_scene_graph_path": LaunchConfiguration("static_scene_graph_path"),
                        "use_sim_time": use_sim_time,
                        "skip_to_child_activity": ParameterValue(
                            LaunchConfiguration("skip_to_child_activity"), value_type=bool,
                        ),
                        "test_child_name": LaunchConfiguration("test_child_name"),
                        "test_activity_type": LaunchConfiguration("test_activity_type"),
                        "test_task_description": LaunchConfiguration("test_task_description"),
                    },
                ],
                output="screen",
            ),
        ]
    )
