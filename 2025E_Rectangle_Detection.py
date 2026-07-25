import time, os, sys
import math
from media.sensor import *
from media.display import *
from media.media import *
from machine import UART, FPIOA
from machine import Pin, Timer
from time import ticks_ms

# 定义存储参数的文件路径
CONFIG_FILE_PATH = '/sdcard/config_data.txt'

# 默认的参数值
DEFAULT_G_MIN = 0
DEFAULT_G_MAX = 70
DEFAULT_BASE_RADIUS = 45

# 从文件中读取参数
def read_config_from_file(file_path):
    """
    从指定文件中读取 G_min, G_max 和 BASE_RADIUS 的值。
    文件应包含3行，每行一个整数。
    """
    try:
        with open(file_path, 'r') as f:
            lines = f.readlines()
            if len(lines) == 3:
                g_min = int(lines[0].strip())
                g_max = int(lines[1].strip())
                base_radius = int(lines[2].strip())
                print(f"成功读取参数: G_min={g_min}, G_max={g_max}, BASE_RADIUS={base_radius}")
                return g_min, g_max, base_radius
            else:
                print("文件格式不正确，使用默认参数。")
                return DEFAULT_G_MIN, DEFAULT_G_MAX, DEFAULT_BASE_RADIUS
    except Exception as e:
        print(f"读取文件 {file_path} 时发生错误: {e}。使用默认参数。")
        return DEFAULT_G_MIN, DEFAULT_G_MAX, DEFAULT_BASE_RADIUS

# 将参数写入文件
def write_config_to_file(file_path, g_min, g_max, base_radius):
    """
    将 G_min, G_max 和 BASE_RADIUS 写入到指定文件，每个值占一行。
    """
    try:
        with open(file_path, 'w') as f:
            f.write(str(g_min) + '\n')
            f.write(str(g_max) + '\n')
            f.write(str(base_radius) + '\n')
            f.flush()
        print("参数成功写入文件。")
    except Exception as e:
        print(f"写入文件 {file_path} 时发生错误: {e}")


# 在程序启动时，首先从文件读取参数
g_min, g_max, base_radius = read_config_from_file(CONFIG_FILE_PATH)

# 定义要调整的参数名称和索引，用于显示和选择
param_names = ["G_min", "G_max", "BASE_RADIUS", "结束调整"]
selected_param_index = 0

# 指示是否处于参数调整模式，True初始状态为调整，False是开始追踪目标
is_in_adjustment_mode = False
# 指示调整方向，True为上升，False为下降，初始调整方式为上升
is_adjust_direction_up = True

# 定义颜色阈值和blob设置
thresholds = [[g_min, g_max]]       # 灰度二值化阈值 [G_min, G_max]
MIN_AREA = 1500                     # 最小面积阈值
MAX_AREA = 55000                    # 最大面积阈值
MIN_ASPECT_RATIO = 0.9              # 最小宽高比
MAX_ASPECT_RATIO = 2                # 最大宽高比

BASE_RADIUS = base_radius           # 基础半径（虚拟坐标单位）
POINTS_PER_CIRCLE = 24              # 增加采样点使圆形更平滑

lcd_width = 800
lcd_height = 480
# 基础矩形比例，根据实际调整
RECT_WIDTH = 210
RECT_HEIGHT = 95
TARGET_ASPECT_RATIO = RECT_WIDTH / RECT_HEIGHT # 目标宽高比

# 摄像头分辨率 (将在 main 函数中初始化后获取)
sensor_width = 0
sensor_height = 0

# 存储上一次成功检测到的矩形中心坐标
last_valid_cx = 0
last_valid_cy = 0


# ---------------------- 工具函数（这里不要改） ----------------------
fpioa = FPIOA()
fpioa.set_function(3, FPIOA.UART1_TXD)
fpioa.set_function(4, FPIOA.UART1_RXD)
uart = UART(UART.UART1, 115200)

fpioa.set_function(33, FPIOA.GPIO33)
KEY_SELECT_PARAM = Pin(33, Pin.IN, Pin.PULL_UP)
fpioa.set_function(34, FPIOA.GPIO34)
KEY_ADJUST_DIR = Pin(34, Pin.IN, Pin.PULL_UP)
fpioa.set_function(35, FPIOA.GPIO35)
KEY_TRIGGER_ADJUST = Pin(35, Pin.IN, Pin.PULL_UP)

def pack_data(x, y):
    """
    数据打包函数，把坐标转换成串口通信的格式。
    格式为：起始字节 + X高字节 + X低字节 + Y高字节 + Y低字节 + 结束字节
    """
    x = max(0, min(x, sensor_width - 1))
    y = max(0, min(y, sensor_height - 1))

    data = bytearray([
        0x2C,
        (x >> 8) & 0xFF,
        x & 0xFF,
        (y >> 8) & 0xFF,
        y & 0xFF,
        0x5B
    ])
    return data

def calculate_distance(p1, p2):
    return math.sqrt((p2[0] - p1[0])**2 + (p2[1] - p1[1])**2)

def calculate_center(points):
    if not points:
        return (0, 0)
    sum_x = sum(p[0] for p in points)
    sum_y = sum(p[1] for p in points)
    return (sum_x / len(points), sum_y / len(points))

def is_valid_rect(corners):
    if len(corners) != 4:
        return False
    edges = [calculate_distance(corners[i], corners[(i+1)%4]) for i in range(4)]
    ratio1 = edges[0] / max(edges[2], 0.1)
    ratio2 = edges[1] / max(edges[3], 0.1)
    valid_ratio = 0.5 < ratio1 < 1.5 and 0.5 < ratio2 < 1.5

    area = 0
    for i in range(4):
        x1, y1 = corners[i]
        x2, y2 = corners[(i+1) % 4]
        area += (x1 * y2 - x2 * y1)
    area = abs(area) / 2
    valid_area = MIN_AREA < area < MAX_AREA

    min_x = min(p[0] for p in corners)
    max_x = max(p[0] for p in corners)
    min_y = min(p[1] for p in corners)
    max_y = max(p[1] for p in corners)
    width = max_x - min_x
    height = max_y - min_y
    aspect_ratio = width / max(height, 0.1)
    valid_aspect = MIN_ASPECT_RATIO < aspect_ratio < MAX_ASPECT_RATIO
    return valid_ratio and valid_area and valid_aspect

def get_perspective_matrix(src_pts, dst_pts):
    A = []
    B = []
    for i in range(4):
        x, y = src_pts[i]
        u, v = dst_pts[i]
        A.append([x, y, 1, 0, 0, 0, -u*x, -u*y])
        A.append([0, 0, 0, x, y, 1, -v*x, -v*y])
        B.append(u)
        B.append(v)
    n = 8
    for i in range(n):
        max_row = i
        for j in range(i, len(A)):
            if abs(A[j][i]) > abs(A[max_row][i]):
                max_row = j
        A[i], A[max_row] = A[max_row], A[i]
        B[i], B[max_row] = B[max_row], B[i]
        pivot = A[i][i]
        if abs(pivot) < 1e-8:
            return None
        for j in range(i, n):
            A[i][j] /= pivot
        B[i] /= pivot
        for j in range(len(A)):
            if j != i and A[j][i] != 0:
                factor = A[j][i]
                for k in range(i, n):
                    A[j][k] -= factor * A[i][k]
                B[j] -= factor * B[i]
    return [
        [B[0], B[1], B[2]],
        [B[3], B[4], B[5]],
        [B[6], B[7], 1.0]
    ]

def transform_points(points, matrix):
    transformed = []
    for (x, y) in points:
        x_hom = x * matrix[0][0] + y * matrix[0][1] + matrix[0][2]
        y_hom = x * matrix[1][0] + y * matrix[1][1] + matrix[1][2]
        w_hom = x * matrix[2][0] + y * matrix[2][1] + matrix[2][2]
        if abs(w_hom) > 1e-8:
            transformed.append((x_hom / w_hom, y_hom / w_hom))
    return transformed

def sort_corners(corners):
    center = calculate_center(corners)
    sorted_corners = sorted(corners, key=lambda p: math.atan2(p[1]-center[1], p[0]-center[0]))
    if len(sorted_corners) == 4:
        left_top_idx = 0
        min_sum_xy = float('inf')
        for i, p in enumerate(sorted_corners):
            if p[0] + p[1] < min_sum_xy:
                min_sum_xy = p[0] + p[1]
                left_top_idx = i
        sorted_corners = sorted_corners[left_top_idx:] + sorted_corners[:left_top_idx]
    return sorted_corners

def get_rectangle_orientation(corners):
    if len(corners) != 4:
        return 0
    top_edge = (corners[1][0] - corners[0][0], corners[1][1] - corners[0][1])
    right_edge = (corners[2][0] - corners[1][0], corners[2][1] - corners[1][1])
    if calculate_distance(corners[0], corners[1]) > calculate_distance(corners[1], corners[2]):
        main_edge = top_edge
    else:
        main_edge = right_edge
    angle = math.atan2(main_edge[1], main_edge[0])
    return angle

# ====================主程序开始====================
def main():
    global is_in_adjustment_mode, selected_param_index, is_adjust_direction_up
    global thresholds, BASE_RADIUS, sensor_width, sensor_height
    global last_valid_cx, last_valid_cy

    try:
        sensor = Sensor(width=1280, height=960)
        sensor.reset()
        sensor.set_framesize(width=320, height=240)
        sensor.set_pixformat(Sensor.RGB565)
        time.sleep(2)

        sensor_width = sensor.width()
        sensor_height = sensor.height()

        last_valid_cx = sensor_width // 2
        last_valid_cy = sensor_height // 2

        Display.init(Display.ST7701, width=lcd_width, height=lcd_height, to_ide=True)
        MediaManager.init()
        sensor.run()

        while True:
            img = sensor.snapshot()

            """
            初始化GPIO33到35用于脱机阈值调整
            第一个用来选择要调整的阈值（L_min到B_max，也可以选择不调整，进入追踪阶段），第二个用来选择是上调这个参数还是下调，第三个用来触发调整操作。
            举个例子，通过GPIO33接收按键传来的0信号，最终选择调整A_max，再通过GPIO34选择调整方式为下调，这时按一下连接GPIO35的按键，给它一个低电平信号，这时A_max就会下降1
            """

            # 确定哪个引脚有输入
            gpio33_pressed = False
            if KEY_SELECT_PARAM.value() == 0:
                time.sleep_ms(50)
                if KEY_SELECT_PARAM.value() == 0:
                    gpio33_pressed = True
                    while KEY_SELECT_PARAM.value() == 0:
                        time.sleep_ms(10)

            gpio34_pressed = False
            if KEY_ADJUST_DIR.value() == 0:
                time.sleep_ms(50)
                if KEY_ADJUST_DIR.value() == 0:
                    gpio34_pressed = True
                    is_adjust_direction_up = not is_adjust_direction_up
                    while KEY_ADJUST_DIR.value() == 0:
                        time.sleep_ms(10)

            gpio35_pressed = False
            if KEY_TRIGGER_ADJUST.value() == 0:
                time.sleep_ms(50)
                if KEY_TRIGGER_ADJUST.value() == 0:
                    gpio35_pressed = True
                    while KEY_TRIGGER_ADJUST.value() == 0:
                        time.sleep_ms(10)

            # --- 阈值调整逻辑 ---
            if gpio33_pressed:
                selected_param_index = (selected_param_index + 1) % len(param_names)
                if selected_param_index == len(param_names) - 1:
                    is_in_adjustment_mode = False
                    # 退出调整模式时，将当前参数写入文件
                    write_config_to_file(CONFIG_FILE_PATH, thresholds[0][0], thresholds[0][1], BASE_RADIUS)
                else:
                    is_in_adjustment_mode = True

            if gpio35_pressed:
                if is_in_adjustment_mode and selected_param_index < len(param_names) - 1:
                    if param_names[selected_param_index] == "G_min":
                        current_value = thresholds[0][0]
                        if is_adjust_direction_up:
                            current_value += 1
                        else:
                            current_value -= 1
                        thresholds[0][0] = max(0, min(current_value, thresholds[0][1]))
                    elif param_names[selected_param_index] == "G_max":
                        current_value = thresholds[0][1]
                        if is_adjust_direction_up:
                            current_value += 1
                        else:
                            current_value -= 1
                        thresholds[0][1] = max(thresholds[0][0], min(current_value, 255))
                    elif param_names[selected_param_index] == "BASE_RADIUS":
                        current_value = BASE_RADIUS
                        if is_adjust_direction_up:
                            current_value += 1
                        else:
                            current_value -= 1
                        BASE_RADIUS = max(10, min(current_value, 200))
                    # 每次调整后，立即将更新后的参数写入文件
                    write_config_to_file(CONFIG_FILE_PATH, thresholds[0][0], thresholds[0][1], BASE_RADIUS)


            # --- 主逻辑分支：阈值调整阶段 或 目标追踪阶段 ---
            display_img = img.copy()

            gray_img = img.to_grayscale()
            binary_img = gray_img.binary(thresholds)
            binary_img.erode(1)
            binary_img.dilate(3)

            if is_in_adjustment_mode:
                display_img = binary_img
                data_to_send = pack_data(0, 0)
                uart.write(data_to_send)
            else:
                min_area_found = float('inf')
                smallest_rect = None

                for r in binary_img.find_rects(threshold=500):
                    corners = r.corners()
                    if is_valid_rect(corners):
                        area = 0
                        for i in range(4):
                            x1, y1 = corners[i]
                            x2, y2 = corners[(i+1) % 4]
                            area += (x1 * y2 - x2 * y1)
                        area = abs(area) / 2
                        if area < min_area_found:
                            min_area_found = area
                            smallest_rect = corners

                if smallest_rect:
                    sorted_corners = sort_corners(smallest_rect)
                    for i in range(4):
                        x1, y1 = sorted_corners[i]
                        x2, y2 = sorted_corners[(i+1) % 4]
                        display_img.draw_line(x1, y1, x2, y2, color=(255, 0, 0), thickness=1)
                    for p in sorted_corners:
                        display_img.draw_circle(p[0], p[1], 5, color=(0, 255, 0), thickness=1)

                    width = calculate_distance(sorted_corners[0], sorted_corners[1])
                    height = calculate_distance(sorted_corners[1], sorted_corners[2])
                    actual_aspect = width / max(height, 0.1)
                    print(f"发现疑似目标，宽{width}，高{height}")

                    is_horizontal = actual_aspect >= 1.0
                    if is_horizontal:
                        virtual_rect = [(0, 0), (RECT_WIDTH, 0), (RECT_WIDTH, RECT_HEIGHT), (0, RECT_HEIGHT)]
                    else:
                        virtual_rect = [(0, 0), (RECT_HEIGHT, 0), (RECT_HEIGHT, RECT_WIDTH), (0, RECT_WIDTH)]

                    if is_horizontal:
                        radius_x = BASE_RADIUS
                        radius_y = BASE_RADIUS / actual_aspect
                    else:
                        radius_x = BASE_RADIUS * actual_aspect
                        radius_y = BASE_RADIUS

                    virtual_center = (RECT_WIDTH/2, RECT_HEIGHT/2) if is_horizontal else (RECT_HEIGHT/2, RECT_WIDTH/2)
                    virtual_circle_points = []
                    for i in range(POINTS_PER_CIRCLE):
                        angle = 2 * math.pi * i / POINTS_PER_CIRCLE
                        x = virtual_center[0] + radius_x * math.cos(angle)
                        y = virtual_center[1] + radius_y * math.sin(angle)
                        virtual_circle_points.append((x, y))

                    matrix = get_perspective_matrix(virtual_rect, sorted_corners)
                    if matrix:
                        mapped_points = transform_points(virtual_circle_points, matrix)
                        int_points = [(int(round(x)), int(round(y))) for x, y in mapped_points]

                        for (x, y) in int_points:
                            display_img.draw_circle(x, y, 2, color=(255, 0, 255), thickness=2)

                        mapped_center = transform_points([virtual_center], matrix)
                        if mapped_center:
                            cx, cy = map(int, map(round, mapped_center[0]))
                            display_img.draw_circle(cx, cy, 3, color=(0, 0, 255), thickness=1)
                            data_to_send = pack_data(cx, cy)
                            print(f"找到目标，中心点{(cx, cy)}")
                            uart.write(data_to_send)
                            last_valid_cx = cx
                            last_valid_cy = cy
                else:
                    data_to_send = pack_data(last_valid_cx, last_valid_cy)
                    uart.write(data_to_send)

            temp = machine.temperature()
            current_stage_str = '阶段: 阈值调整' if is_in_adjustment_mode else '阶段: 目标追踪'
            display_img.draw_string_advanced(0, 0, 30, current_stage_str, color=(255, 255, 255))
            display_img.draw_string_advanced(0, 30, 30, '温度: '+str("%.2f"%temp), color=(255, 255, 255))
            param_display_str = f'参数: {param_names[selected_param_index]}' if is_in_adjustment_mode else '参数: 不调整'
            display_img.draw_string_advanced(0, 60, 30, param_display_str, color=(255, 255, 255))
            adjust_direction_display_str = "不调整"
            if is_in_adjustment_mode and selected_param_index < len(param_names) - 1:
                adjust_direction_display_str = "上升" if is_adjust_direction_up else "下降"
            display_img.draw_string_advanced(0, 90, 30, f'调整方式: {adjust_direction_display_str}', color=(255, 255, 255))

            # 显示完整的当前参数值
            threshold_values_str = f'灰度阈值: {thresholds[0]}'
            display_img.draw_string_advanced(0, 120, 30, threshold_values_str, color=(255, 255, 255))
            base_radius_str = f'基础半径: {BASE_RADIUS}'
            display_img.draw_string_advanced(0, 150, 30, base_radius_str, color=(255, 255, 255))

            # 在调整模式下，显示当前正在调整的具体参数值
            if is_in_adjustment_mode and selected_param_index < len(param_names) - 1:
                current_adjusted_value_str = ""
                if param_names[selected_param_index] == "G_min":
                    current_adjusted_value_str = f'当前值: {thresholds[0][0]}'
                elif param_names[selected_param_index] == "G_max":
                    current_adjusted_value_str = f'当前值: {thresholds[0][1]}'
                elif param_names[selected_param_index] == "BASE_RADIUS":
                    current_adjusted_value_str = f'当前值: {BASE_RADIUS}'
                display_img.draw_string_advanced(0, 180, 30, current_adjusted_value_str, color=(255, 255, 255))

            Display.show_image(display_img, x=round((lcd_width-sensor_width)/2), y=round((lcd_height-sensor_height)/2))
            time.sleep_ms(10)

    except Exception as e:
        print(f"错误: {str(e)}")
    finally:
        if 'sensor' in locals() and isinstance(sensor, Sensor):
            sensor.stop()
        Display.deinit()
        os.exitpoint(os.EXITPOINT_ENABLE_SLEEP)
        time.sleep_ms(20)
        MediaManager.deinit()

if __name__ == "__main__":
    main()
