import time
import os
from media.sensor import *
from media.display import *
from media.media import *
from time import ticks_ms
from machine import FPIOA, Pin, PWM, Timer
from machine import UART

# 初始LAB颜色阈值定义
# 注意：black_threshold 现在是一个列表，以便在运行时修改
black_threshold = [1, 27, -37, 37, -42, 35]  # 黑色胶带LAB阈值 (L Min, L Max, A Min, A Max, B Min, B Max)

# LCD屏幕和摄像头分辨率定义
# 摄像头设置为VGA (640x480)，显示屏设置为800x480，可能会有轻微拉伸，但图像会居中显示
lcd_width = 800
lcd_height = 480
sensor_width = 640
sensor_height = 480

# 初始化摄像头
sensor = Sensor(width=sensor_width, height=sensor_height)
sensor.reset()
sensor.set_framesize(Sensor.VGA)  # 分辨率640x480
sensor.set_pixformat(Sensor.RGB565)
time.sleep(2)

# 初始化显示屏，使用mipi屏和IDE缓冲区显示图像
Display.init(Display.ST7701, width=lcd_width, height=lcd_height, to_ide=True)
MediaManager.init()  # 初始化媒体资源管理器
sensor.run()  # 启动摄像头

# 初始化串口，引脚3映射为UART1的TXD，引脚4映射为UART1的RXD
fpioa = FPIOA()
fpioa.set_function(3, FPIOA.UART1_TXD)
fpioa.set_function(4, FPIOA.UART1_RXD)

# 设置串口号1和波特率115200
uart = UART(UART.UART1, 115200)

# 配置按键引脚
# GPIO33选择LAB参数，GPIO34选择数值调整方向 (上升/下降)，GPIO35触发数值调整
fpioa.set_function(33, FPIOA.GPIO33)
KEY_SELECT_PARAM = Pin(33, Pin.IN, Pin.PULL_UP)
fpioa.set_function(34, FPIOA.GPIO34)
KEY_ADJUST_DIR = Pin(34, Pin.IN, Pin.PULL_UP)
fpioa.set_function(35, FPIOA.GPIO35)
KEY_TRIGGER_ADJUST = Pin(35, Pin.IN, Pin.PULL_UP)

clock = time.clock()  # 用于计算帧率

# 定义LAB参数名称和索引，用于显示和选择
param_names = ["L_min", "L_max", "A_min", "A_max", "B_min", "B_max", "结束调整"]
selected_param_index = 0  # 当前选中的参数索引，0对应L_min

# 指示是否处于阈值调整模式，True初始状态为调整，False是色块追踪
is_in_adjustment_mode = False
# 指示调整方向，True为上升，False为下降，初始调整方式为上升
is_adjust_direction_up = True

# 数据打包函数，把坐标转换成串口通信的格式（别改！！！！）
# 格式为：起始字节 + X高字节 + X低字节 + Y高字节 + Y低字节 + 结束字节
def pack_data(x, y):
    # 限制坐标范围，K230 帧分辨率为 640x480
    x = max(0, min(x, sensor_width - 1))  # X坐标范围 0-(sensor_width-1)
    y = max(0, min(y, sensor_height - 1))  # Y坐标范围 0-(sensor_height-1)

    data = bytearray([
        0x2C,  # 起始字节
        (x >> 8) & 0xFF,  # X 高字节
        x & 0xFF,  # X 低字节
        (y >> 8) & 0xFF,  # Y 高字节
        y & 0xFF,  # Y 低字节
        0x5B  # 结束字节
    ])
    return data

# 色块追踪逻辑函数
def find_target(img, current_black_threshold):
    # 用LAB寻找黑色外边框
    black_blobs = img.find_blobs([current_black_threshold], area_threshold=1, merge=True, margin=1)

    for blob in black_blobs:
        x, y, w, h = blob.rect()

        # 跳过太小或太大的区域
        if w < 100 or h < 100 or w > 500 or h > 500:
            continue

        # 只关注长宽比在0.33到3之间的目标
        if w / h < 0.33 or w / h > 3:
            continue

        # 在黑色边框区域内确认白色区域
        inner_w = int(w * 0.9)  # 白色区域比黑色边框小10%
        inner_h = int(h * 0.9)
        inner_x = x + (w - inner_w) // 2
        inner_y = y + (h - inner_h) // 2

        # 检查该区域是否主要为白色
        white_pixels = 0
        total_pixels = 0

        # 采样内部区域中心部分
        try:
            for i in range(inner_x + 5, inner_x + inner_w - 5, 5):
                for j in range(inner_y + 5, inner_y + inner_h - 5, 5):
                    pixel = img.get_pixel(i, j)
                    # R+G+B > 300 视为白色 (此逻辑不变，因为它追踪的是白色区域)
                    if pixel[0] + pixel[1] + pixel[2] > 300:
                        white_pixels += 1
                    total_pixels += 1

            # 如果白色像素占比超过阈值，则认为有效
            if total_pixels > 0 and white_pixels / total_pixels > 0.4:
                print(f"检测到符合条件的黑色外框: 宽={w}, 高={h}")
                return (x, y, w, h), (inner_x, inner_y, inner_w, inner_h)

        except Exception as e:
            return None, None

    return None, None

while True:
    clock.tick()  # 更新帧率计时器
    img = sensor.snapshot()  # 拍摄一张彩色图片

    # GPIO33参数选择按键检测 (带消抖)
    gpio33_pressed = False
    if KEY_SELECT_PARAM.value() == 0:  # 按键按下 (低电平)
        time.sleep_ms(50)  # 软件消抖
        if KEY_SELECT_PARAM.value() == 0:
            gpio33_pressed = True
            # 等待按键释放，防止重复触发
            while KEY_SELECT_PARAM.value() == 0:
                time.sleep_ms(10)

    # GPIO34调整方向按键检测 (带消抖)
    gpio34_pressed = False
    if KEY_ADJUST_DIR.value() == 0:
        time.sleep_ms(50)
        if KEY_ADJUST_DIR.value() == 0:
            gpio34_pressed = True
            # 短按GPIO34时切换调整方向状态
            is_adjust_direction_up = not is_adjust_direction_up
            # 等待按键释放
            while KEY_ADJUST_DIR.value() == 0:
                time.sleep_ms(10)

    # GPIO35触发调整按键检测 (带消抖)
    gpio35_pressed = False
    if KEY_TRIGGER_ADJUST.value() == 0:
        time.sleep_ms(50)
        if KEY_TRIGGER_ADJUST.value() == 0:
            gpio35_pressed = True
            # 等待按键释放
            while KEY_TRIGGER_ADJUST.value() == 0:
                time.sleep_ms(10)

    # 阈值调整逻辑
    if gpio33_pressed:  # 选择参数按键被按下
        selected_param_index = (selected_param_index + 1) % len(param_names)
        if selected_param_index == len(param_names) - 1:  # 如果选择了“结束调整”
            is_in_adjustment_mode = False  # 退出调整模式，进入色块追踪阶段
        else:
            is_in_adjustment_mode = True  # 进入/保持调整模式

    if gpio35_pressed:  # 触发调整按键被按下
        if is_in_adjustment_mode and selected_param_index < len(param_names) - 1:  # 确保在调整模式且不是“结束调整”选项
            current_value = black_threshold[selected_param_index]
            if not is_adjust_direction_up:  # 如果调整方向为下降
                current_value -= 1
            else:  # 如果调整方向为上升
                current_value += 1

            # 对LAB值进行范围限制
            if selected_param_index in [0, 1]:  # L_min, L_max (0-100)
                current_value = max(0, min(100, current_value))
            elif selected_param_index in [2, 3, 4, 5]:  # A_min, A_max, B_min, B_max (-128 to 127)
                current_value = max(-128, min(127, current_value))

            black_threshold[selected_param_index] = current_value  # 更新黑色阈值

    # --- 主逻辑分支：阈值调整阶段 或 色块追踪阶段 ---
    display_img = img.copy()

    if is_in_adjustment_mode:
        # 阈值调整阶段，对原始图像进行二值化，显示二值化图像
        # 仅对黑色阈值进行二值化显示，方便用户调整
        binary_img_for_display = img.binary([tuple(black_threshold)])
        display_img = binary_img_for_display

        # 串口发送(0,0)坐标，表示当前处于调整模式
        data_to_send = pack_data(0, 0)
        uart.write(data_to_send)

    else:
        # 色块追踪阶段，利用调整后的黑色阈值进行追踪
        outer, inner = find_target(img, tuple(black_threshold)) # 传入调整后的黑色阈值

        if outer:
            # 绘制黑色外边框（绿色）
            display_img.draw_rectangle(outer[0], outer[1], outer[2], outer[3], color=(0, 255, 0), thickness=2)

            if inner:
                # 绘制白色内区域（蓝色边框）
                display_img.draw_rectangle(inner[0], inner[1], inner[2], inner[3], color=(0, 0, 255), thickness=2)

                # 计算，绘制，发送中心点
                center_x = outer[0] + outer[2] // 2
                center_y = outer[1] + outer[3] // 2
                display_img.draw_cross(center_x, center_y, color=(255, 0, 0), size=10)
                data_to_send = pack_data(center_x, center_y)
                uart.write(data_to_send)  # 串口通信发送中心坐标
                print("发送中心点坐标: ({}, {})".format(center_x, center_y))

        else:
            # 如果没有检测到符合条件的色块，发送 (0,0) 坐标
            data_to_send = pack_data(0, 0)
            uart.write(data_to_send)  # 串口通信发送中心坐标
            print("未检测到目标，发送 (0,0)")

    # 获取温度值
    temp = machine.temperature()

    # 叠加显示信息
    current_stage_str = '阶段: 阈值调整' if is_in_adjustment_mode else '阶段: 色块追踪'
    display_img.draw_string_advanced(0, 0, 30, current_stage_str, color=(255, 255, 255))

    # 显示当前温度
    display_img.draw_string_advanced(0, 30, 30, '温度: '+str("%.2f"%temp), color=(255, 255, 255))

    # 显示当前正在调整的阈值参数 (或“不调整”)
    param_display_str = f'参数: {param_names[selected_param_index]}' if is_in_adjustment_mode else '参数: 不调整'
    display_img.draw_string_advanced(0, 60, 30, param_display_str, color=(255, 255, 255))

    # 显示调整方式 (上升/下降 或 “不调整”)
    adjust_direction_display_str = "不调整"
    if is_in_adjustment_mode and selected_param_index < len(param_names) - 1:
        adjust_direction_display_str = "上升" if is_adjust_direction_up else "下降"
    display_img.draw_string_advanced(0, 90, 30, f'调整方式: {adjust_direction_display_str}', color=(255, 255, 255))

    # 显示完整的当前黑色胶带LAB阈值
    lab_values_str = f'黑胶带LAB: {black_threshold}'
    display_img.draw_string_advanced(0, 120, 30, lab_values_str, color=(255, 255, 255))

    # 在调整模式下，显示当前正在调整的具体参数值或提示
    if is_in_adjustment_mode and selected_param_index < len(param_names) - 1:
        display_img.draw_string_advanced(0, 150, 30, f'当前值: {black_threshold[selected_param_index]}', color=(255, 255, 255))

    Display.show_image(display_img, x=round((lcd_width - sensor_width) / 2), y=round((lcd_height - sensor_height) / 2))
    time.sleep(0.01)  # 短暂延时，避免CPU占用过高
