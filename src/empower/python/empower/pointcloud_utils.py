import struct

import numpy as np
from sensor_msgs.msg import PointField
from sensor_msgs_py import point_cloud2
from std_msgs.msg import Header


FIELDS_XYZRGB = [
    PointField(name="x", offset=0,datatype=PointField.FLOAT32, count=1),
    PointField(name="y", offset=4, datatype=PointField.FLOAT32, count=1),
    PointField(name="z",offset=8, datatype=PointField.FLOAT32, count=1),
    PointField(name="rgb", offset=12,datatype=PointField.UINT32,count=1),
]


def depth_image_to_point_cloud(depth_image, camera_intrinsics):
    height, width = depth_image.shape
    v,u = np.indices((height, width))
    x = (u - camera_intrinsics[0, 2]) * depth_image / camera_intrinsics[0, 0]
    y= (v - camera_intrinsics[1,2]) * depth_image / camera_intrinsics[1,1]
    z = depth_image
    return np.dstack((x, y,z)).reshape(-1, 3)


def rgb_to_uint32(color):
    color = np.asarray(color, dtype=float)
    if color.max(initial=0.0) <= 1.0:
        color = np.floor(color * 255.0)

    r,g, b = np.clip(color, 0,255).astype(np.uint32)
    return int((r << 16) | (g << 8) | b)


def open3d_to_pointcloud2(open3d_cloud, frame_id, stamp):
    points =np.asarray(open3d_cloud.points, dtype=np.float32)

    colors = np.asarray(open3d_cloud.colors, dtype=float)
    if colors.size == 0:
        colors = np.zeros_like(points)
    rows = []
    for point, color in zip(points, colors):
        if np.all(np.isfinite(point)):
            rows.append((float(point[0]), float(point[1]),float(point[2]),rgb_to_uint32(color)))
    header = Header()
    header.stamp = stamp
    header.frame_id = frame_id
    return point_cloud2.create_cloud(header,FIELDS_XYZRGB, rows)


def pack_rgb_float(r, g,b):
    return struct.unpack("f", struct.pack("I", (int(r) << 16) | (int(g) << 8) | int(b)))[0]
