import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


STRING_PARAMETERS = {
    "input_mode": "text",
    "llm_provider": "ollama",
    "llm_base_url": "http://localhost:11434",
    "llm_model": "qwen2.5:1.5b",
    "llm_fallback_model": "llama3.2:1b",
    "stt_provider": "faster_whisper",
    "stt_model": "tiny",
    "stt_device": "cpu",
    "stt_compute_type": "int8",
    "language": "it",
    "tts_provider": "pyttsx3",
    "piper_model_path": "",
    "piper_config_path": "",
}

TYPED_PARAMETERS = {
    "llm_timeout": ("60.0", float),
    "temperature": ("0.4", float),
    "max_response_tokens": ("120", int),
    "max_history_turns": ("4", int),
    "record_seconds": ("4.0", float),
    "sample_rate": ("16000", int),
    "tts_timeout": ("30.0", float),
}


def generate_launch_description():
    default_params = os.path.join(
        get_package_share_directory("empower_hri"),
        "config",
        "hri_cpu_local.yaml",
    )
    arguments = [
        DeclareLaunchArgument("params_file", default_value=default_params),
    ]
    arguments.extend(
        DeclareLaunchArgument(name, default_value=default)
        for name, default in STRING_PARAMETERS.items()
    )
    arguments.extend(
        DeclareLaunchArgument(name, default_value=default)
        for name, (default, _) in TYPED_PARAMETERS.items()
    )

    overrides = {
        name: LaunchConfiguration(name)
        for name in STRING_PARAMETERS
    }
    overrides.update(
        {
            name: ParameterValue(LaunchConfiguration(name), value_type=value_type)
            for name, (_, value_type) in TYPED_PARAMETERS.items()
        }
    )

    return LaunchDescription(
        arguments
        + [
            Node(
                package="empower_hri",
                executable="voice_llm_node",
                name="voice_llm_node",
                parameters=[LaunchConfiguration("params_file"), overrides],
                output="screen",
                emulate_tty=True,
            )
        ]
    )
