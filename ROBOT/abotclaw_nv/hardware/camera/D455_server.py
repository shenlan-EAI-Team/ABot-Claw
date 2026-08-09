# 用于从摄像头获取图像数据
import cv2
import zmq
import time
import struct
from collections import deque
import numpy as np
import pyrealsense2 as rs

DEFAULT_D455_SERIAL = "336522303538"
FRAME_HEADER_FORMAT = "<dI"
FRAME_HEADER_SIZE = struct.calcsize(FRAME_HEADER_FORMAT)


class RealSenseCamera(object):
    def __init__(
        self,
        img_shape,
        fps,
        serial_number=None,
        enable_depth=False,
        auto_exposure=False,
        exposure=120,
        gain=32,
        auto_exposure_priority=0,
    ) -> None:
        """
        img_shape: [height, width]
        serial_number: serial number
        """
        self.img_shape = img_shape
        self.fps = fps
        self.serial_number = serial_number
        self.enable_depth = enable_depth
        self.auto_exposure = auto_exposure
        self.exposure = exposure
        self.gain = gain
        self.auto_exposure_priority = auto_exposure_priority

        align_to = rs.stream.color
        self.align = rs.align(align_to)
        self.init_realsense()

    # def init_realsense(self):

    #     self.pipeline = rs.pipeline()
    #     config = rs.config()
    #     if self.serial_number is not None:
    #         config.enable_device(self.serial_number)

    #     config.enable_stream(rs.stream.color, self.img_shape[1], self.img_shape[0], rs.format.bgr8, self.fps)

    #     if self.enable_depth:
    #         config.enable_stream(rs.stream.depth, self.img_shape[1], self.img_shape[0], rs.format.z16, self.fps)

    #     profile = self.pipeline.start(config)
    #     self._device = profile.get_device()
    #     if self._device is None:
    #         print('[Image Server] pipe_profile.get_device() is None .')
    #     if self.enable_depth:
    #         assert self._device is not None
    #         depth_sensor = self._device.first_depth_sensor()
    #         self.g_depth_scale = depth_sensor.get_depth_scale()

    #     self.intrinsics = profile.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()


    def init_realsense(self):
        self.pipeline = rs.pipeline()
        config = rs.config()

        if self.serial_number is None or isinstance(self.serial_number, int):
            raise RuntimeError(
                "[Image Server] serial_number 未指定或类型错误，请传入相机序列号字符串，"
                "避免误抢其他相机。"
            )

        config.enable_device(str(self.serial_number))  # 强制转换为字符串，确保安全
        config.enable_stream(rs.stream.color, self.img_shape[1], self.img_shape[0], rs.format.bgr8, self.fps)

        if self.enable_depth:
            config.enable_stream(rs.stream.depth, self.img_shape[1], self.img_shape[0], rs.format.z16, self.fps)

        profile = self.pipeline.start(config)
        self._device = profile.get_device()
        if self._device is None:
            print('[Image Server] pipe_profile.get_device() is None .')
        self.color_sensor = self._device.first_color_sensor() if self._device is not None else None
        self.configure_color_sensor()
        if self.enable_depth:
            depth_sensor = self._device.first_depth_sensor()
            self.g_depth_scale = depth_sensor.get_depth_scale()

        self.intrinsics = profile.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()

    def _supports_option(self, sensor, option):
        return sensor is not None and sensor.supports(option)

    def _set_option_if_supported(self, sensor, option, value, label):
        if not self._supports_option(sensor, option):
            print(f"[Image Server] RealSense option {label} is not supported on device {self.serial_number}.")
            return
        try:
            sensor.set_option(option, value)
        except Exception as exc:
            print(f"[Image Server] Failed to set RealSense option {label}={value} on device {self.serial_number}: {exc}")

    def _get_option_if_supported(self, sensor, option):
        if not self._supports_option(sensor, option):
            return None
        try:
            return sensor.get_option(option)
        except Exception as exc:
            print(f"[Image Server] Failed to read RealSense option {option} on device {self.serial_number}: {exc}")
            return None

    def configure_color_sensor(self):
        if self.color_sensor is None:
            print(f"[Image Server] No color sensor found on RealSense device {self.serial_number}.")
            return

        # Keep FPS stable in low light instead of silently dropping frame rate.
        self._set_option_if_supported(
            self.color_sensor,
            rs.option.auto_exposure_priority,
            float(self.auto_exposure_priority),
            'auto_exposure_priority',
        )

        self._set_option_if_supported(
            self.color_sensor,
            rs.option.enable_auto_exposure,
            1.0 if self.auto_exposure else 0.0,
            'enable_auto_exposure',
        )

        if not self.auto_exposure:
            self._set_option_if_supported(self.color_sensor, rs.option.exposure, float(self.exposure), 'exposure')
            self._set_option_if_supported(self.color_sensor, rs.option.gain, float(self.gain), 'gain')

        actual_auto_exposure = self._get_option_if_supported(self.color_sensor, rs.option.enable_auto_exposure)
        actual_exposure = self._get_option_if_supported(self.color_sensor, rs.option.exposure)
        actual_gain = self._get_option_if_supported(self.color_sensor, rs.option.gain)
        actual_priority = self._get_option_if_supported(self.color_sensor, rs.option.auto_exposure_priority)
        print(
            "[Image Server] RealSense color settings "
            f"(serial={self.serial_number}): "
            f"auto_exposure={actual_auto_exposure}, "
            f"exposure={actual_exposure}, "
            f"gain={actual_gain}, "
            f"auto_exposure_priority={actual_priority}"
        )


    def get_frame(self):
        frames = self.pipeline.wait_for_frames()
        aligned_frames = self.align.process(frames)
        color_frame = aligned_frames.get_color_frame()

        if self.enable_depth:
            depth_frame = aligned_frames.get_depth_frame()

        if not color_frame:
            return None

        color_image = np.asanyarray(color_frame.get_data())
        # color_image = cv2.cvtColor(color_image, cv2.COLOR_BGR2RGB)
        depth_image = np.asanyarray(depth_frame.get_data()) if self.enable_depth else None
        return color_image, depth_image

    def release(self):
        self.pipeline.stop()


class OpenCVCamera():
    def __init__(self, device_id, img_shape, fps):
        """
        decive_id: /dev/video* or *
        img_shape: [height, width]
        """
        self.id = device_id
        self.fps = fps
        self.img_shape = img_shape
        self.cap = cv2.VideoCapture(self.id, cv2.CAP_V4L2)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc('M', 'J', 'P', 'G'))
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.img_shape[0])
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self.img_shape[1])
        self.cap.set(cv2.CAP_PROP_FPS, self.fps)

        # Test if the camera can read frames
        if not self._can_read_frame():
            print(f"[Image Server] Camera {self.id} Error: Failed to initialize the camera or read frames. Exiting...")
            self.release()

    def _can_read_frame(self):
        success, _ = self.cap.read()
        return success

    def release(self):
        self.cap.release()

    def get_frame(self):
        ret, color_image = self.cap.read()
        if not ret:
            return None
        return color_image


class ImageServer:
    def __init__(self, config, port = 5555, depth_port = 5556, Unit_Test = False):
        """
        config example1:
        {
            'fps':30                                                          # frame per second
            'head_camera_type': 'opencv',                                     # opencv or realsense
            'head_camera_image_shape': [480, 1280],                           # Head camera resolution  [height, width]
            'head_camera_id_numbers': [0],                                    # '/dev/video0' (opencv)
            'wrist_camera_type': 'realsense', 
            'wrist_camera_image_shape': [480, 640],                           # Wrist camera resolution  [height, width]
            'wrist_camera_id_numbers': ["218622271789", "241222076627"],      # realsense camera's serial number
        }

        config example2:
        {
            'fps':30                                                          # frame per second
            'head_camera_type': 'realsense',                                  # opencv or realsense
            'head_camera_image_shape': [480, 640],                            # Head camera resolution  [height, width]
            'head_camera_id_numbers': ["218622271739"],                       # realsense camera's serial number
            'wrist_camera_type': 'opencv', 
            'wrist_camera_image_shape': [480, 640],                           # Wrist camera resolution  [height, width]
            'wrist_camera_id_numbers': [0,1],                                 # '/dev/video0' and '/dev/video1' (opencv)
        }

        If you are not using the wrist camera, you can comment out its configuration, like this below:
        config:
        {
            'fps':30                                                          # frame per second
            'head_camera_type': 'opencv',                                     # opencv or realsense
            'head_camera_image_shape': [480, 1280],                           # Head camera resolution  [height, width]
            'head_camera_id_numbers': [0],                                    # '/dev/video0' (opencv)
            #'wrist_camera_type': 'realsense', 
            #'wrist_camera_image_shape': [480, 640],                           # Wrist camera resolution  [height, width]
            #'wrist_camera_id_numbers': ["218622271789", "241222076627"],      # serial number (realsense)
        }
        """
        print(config)
        self.fps = config.get('fps', 30)
        self.head_camera_type = config.get('head_camera_type', 'opencv')
        self.head_image_shape = config.get('head_camera_image_shape', [480, 1280])      # (height, width)
        self.head_camera_id_numbers = config.get('head_camera_id_numbers', [DEFAULT_D455_SERIAL])
        self.realsense_auto_exposure = config.get('realsense_auto_exposure', False)
        self.realsense_exposure = config.get('realsense_exposure', 120)
        self.realsense_gain = config.get('realsense_gain', 32)
        self.realsense_auto_exposure_priority = config.get('realsense_auto_exposure_priority', 0)
        self.jpeg_quality = config.get('jpeg_quality', 95)
        self._next_frame_id = 0
        self._last_depth = None
    
        self.wrist_camera_type = config.get('wrist_camera_type', None)
        self.wrist_image_shape = config.get('wrist_camera_image_shape', [480, 640])    # (height, width)
        self.wrist_camera_id_numbers = config.get('wrist_camera_id_numbers', None)

        self.port = port
        self.depth_port = depth_port
        self.Unit_Test = Unit_Test
        
        # 检查是否启用深度
        self.enable_depth = config.get('enable_depth', False)
        
        # Initialize head cameras
        self.head_cameras = []
        if self.head_camera_type == 'opencv':
            for device_id in self.head_camera_id_numbers:
                camera = OpenCVCamera(device_id=device_id, img_shape=self.head_image_shape, fps=self.fps)
                self.head_cameras.append(camera)
        elif self.head_camera_type == 'realsense':
            for serial_number in self.head_camera_id_numbers:
                camera = RealSenseCamera(
                    img_shape=self.head_image_shape, 
                    fps=self.fps, 
                    serial_number=serial_number,
                    enable_depth=self.enable_depth,
                    auto_exposure=self.realsense_auto_exposure,
                    exposure=self.realsense_exposure,
                    gain=self.realsense_gain,
                    auto_exposure_priority=self.realsense_auto_exposure_priority,
                )
                self.head_cameras.append(camera)
        else:
            print(f"[Image Server] Unsupported head_camera_type: {self.head_camera_type}")

        # Initialize wrist cameras if provided
        self.wrist_cameras = []
        if self.wrist_camera_type and self.wrist_camera_id_numbers:
            if self.wrist_camera_type == 'opencv':
                for device_id in self.wrist_camera_id_numbers:
                    camera = OpenCVCamera(device_id=device_id, img_shape=self.wrist_image_shape, fps=self.fps)
                    self.wrist_cameras.append(camera)
            elif self.wrist_camera_type == 'realsense':
                for serial_number in self.wrist_camera_id_numbers:
                    camera = RealSenseCamera(
                        img_shape=self.wrist_image_shape,
                        fps=self.fps,
                        serial_number=serial_number,
                        auto_exposure=self.realsense_auto_exposure,
                        exposure=self.realsense_exposure,
                        gain=self.realsense_gain,
                        auto_exposure_priority=self.realsense_auto_exposure_priority,
                    )
                    self.wrist_cameras.append(camera)
            else:
                print(f"[Image Server] Unsupported wrist_camera_type: {self.wrist_camera_type}")

        # Set ZeroMQ context and socket
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PUB)
        self.socket.bind(f"tcp://*:{self.port}")
        
        # 深度图像 socket (单独端口)
        self.depth_socket = None
        if self.enable_depth:
            self.depth_socket = self.context.socket(zmq.PUB)
            self.depth_socket.bind(f"tcp://*:{self.depth_port}")
            print(f"[Image Server] Depth stream enabled on port {self.depth_port}")

        if self.Unit_Test:
            self._init_performance_metrics()

        for cam in self.head_cameras:
            if isinstance(cam, OpenCVCamera):
                print(f"[Image Server] Head camera {cam.id} resolution: {cam.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)} x {cam.cap.get(cv2.CAP_PROP_FRAME_WIDTH)}")
            elif isinstance(cam, RealSenseCamera):
                print(f"[Image Server] Head camera {cam.serial_number} resolution: {cam.img_shape[0]} x {cam.img_shape[1]}")
            else:
                print("[Image Server] Unknown camera type in head_cameras.")

        for cam in self.wrist_cameras:
            if isinstance(cam, OpenCVCamera):
                print(f"[Image Server] Wrist camera {cam.id} resolution: {cam.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)} x {cam.cap.get(cv2.CAP_PROP_FRAME_WIDTH)}")
            elif isinstance(cam, RealSenseCamera):
                print(f"[Image Server] Wrist camera {cam.serial_number} resolution: {cam.img_shape[0]} x {cam.img_shape[1]}")
            else:
                print("[Image Server] Unknown camera type in wrist_cameras.")

        print("[Image Server] Image server has started, waiting for client connections...")



    def _init_performance_metrics(self):
        self.frame_count = 0  # Total frames sent
        self.time_window = 1.0  # Time window for FPS calculation (in seconds)
        self.frame_times = deque()  # Timestamps of frames sent within the time window
        self.start_time = time.time()  # Start time of the streaming

    def _update_performance_metrics(self, current_time):
        # Add current time to frame times deque
        self.frame_times.append(current_time)
        # Remove timestamps outside the time window
        while self.frame_times and self.frame_times[0] < current_time - self.time_window:
            self.frame_times.popleft()
        # Increment frame count
        self.frame_count += 1

    def _print_performance_metrics(self, current_time):
        if self.frame_count % 30 == 0:
            elapsed_time = current_time - self.start_time
            real_time_fps = len(self.frame_times) / self.time_window
            print(f"[Image Server] Real-time FPS: {real_time_fps:.2f}, Total frames sent: {self.frame_count}, Elapsed time: {elapsed_time:.2f} sec")

    def _get_frame_metadata(self):
        stamp = time.time()
        frame_id = self._next_frame_id
        self._next_frame_id += 1
        return stamp, frame_id

    def _pack_frame_message(self, payload, stamp, frame_id):
        header = struct.pack(FRAME_HEADER_FORMAT, stamp, frame_id)
        return header + payload

    def _close(self):
        for cam in self.head_cameras:
            cam.release()
        for cam in self.wrist_cameras:
            cam.release()
        self.socket.close()
        if self.depth_socket is not None:
            self.depth_socket.close()
        self.context.term()
        print("[Image Server] The server has been closed.")

    def send_process(self):
        try:
            while True:
                head_frames = []
                for cam in self.head_cameras:
                    depth_image = None
                    if self.head_camera_type == 'opencv':
                        color_image = cam.get_frame()
                        if color_image is None:
                            print("[Image Server] Head camera frame read is error.")
                            break
                    elif self.head_camera_type == 'realsense':
                        color_image, depth_image = cam.get_frame()
                        if color_image is None:
                            print("[Image Server] Head camera frame read is error.")
                            break
                    head_frames.append(color_image)
                    
                    # 保存深度图像用于后续发送
                    if self.enable_depth and depth_image is not None:
                        self._last_depth = depth_image
                if len(head_frames) != len(self.head_cameras):
                    break
                head_color = cv2.hconcat(head_frames)
                
                if self.wrist_cameras:
                    wrist_frames = []
                    for cam in self.wrist_cameras:
                        if self.wrist_camera_type == 'opencv':
                            color_image = cam.get_frame()
                            if color_image is None:
                                print("[Image Server] Wrist camera frame read is error.")
                                break
                        elif self.wrist_camera_type == 'realsense':
                            color_image, depth_iamge = cam.get_frame()
                            if color_image is None:
                                print("[Image Server] Wrist camera frame read is error.")
                                break
                        wrist_frames.append(color_image)
                    wrist_color = cv2.hconcat(wrist_frames)

                    # Concatenate head and wrist frames
                    full_color = cv2.hconcat([head_color, wrist_color])
                else:
                    full_color = head_color

                ret, buffer = cv2.imencode(
                    '.jpg',
                    full_color,
                    [int(cv2.IMWRITE_JPEG_QUALITY), int(self.jpeg_quality)],
                )
                if not ret:
                    print("[Image Server] Frame imencode is failed.")
                    continue

                jpg_bytes = buffer.tobytes()
                timestamp, frame_id = self._get_frame_metadata()
                message = self._pack_frame_message(jpg_bytes, timestamp, frame_id)

                self.socket.send(message)
                
                # 发送深度图像 (如果启用)
                if self.enable_depth and self.depth_socket is not None and hasattr(self, '_last_depth') and self._last_depth is not None:
                    # 深度图像使用 uint16 格式 (毫米)
                    depth_bytes = self._last_depth.tobytes()
                    depth_message = self._pack_frame_message(depth_bytes, timestamp, frame_id)
                    self.depth_socket.send(depth_message)

                if self.Unit_Test:
                    current_time = time.time()
                    self._update_performance_metrics(current_time)
                    self._print_performance_metrics(current_time)

        except KeyboardInterrupt:
            print("[Image Server] Interrupted by user.")
        finally:
            self._close()


if __name__ == "__main__":
    config = {
        'fps': 30,
        #'head_camera_type': 'opencv'
        'head_camera_type': 'realsense',
        'head_camera_image_shape': [480, 640],  # Head camera resolution
        'head_camera_id_numbers': [DEFAULT_D455_SERIAL],
        'enable_depth': True,  # 启用深度图像
        'realsense_auto_exposure': False,
        'realsense_exposure': 120,
        'realsense_gain': 32,
        'realsense_auto_exposure_priority': 0,
        'jpeg_quality': 95,
        #'wrist_camera_type': 'opencv',
        #'wrist_camera_image_shape': [480, 640],  # Wrist camera resolution
        #'wrist_camera_id_numbers': [2, 4],
    }

    # RGB 端口: 5555, 深度端口: 5556
    server = ImageServer(config, port=5555, depth_port=5556, Unit_Test=False)
    server.send_process()
