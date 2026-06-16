import os

import rclpy
from rclpy.node import Node

from empower_ros2.report_writer import write_readable_reports


class ExportReportsNode(Node):
    def __init__(self):
        super().__init__("empower_export_reports")
        self.declare_parameter("use_case", "person_table_child_task")
        self.declare_parameter("output_root", "/root/exchange/empower_data")
        use_case = self.get_parameter("use_case").value
        dump_dir = os.path.join(self.get_parameter("output_root").value, "output", use_case)
        report = write_readable_reports(dump_dir)
        self.get_logger().info(f"Wrote readable EMPOWER reports in {dump_dir}: {', '.join(report['files'])}")


def main(args=None):
    rclpy.init(args=args)
    ExportReportsNode()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
