'''
实验名称：按键拍照保存并实时显示图像
版本：v1.4
作者：GPT & 01Studio
实验平台：01Studio CanMV K230
说明：按下按键，摄像头拍照并将图片保存到SD卡的/sdcard/photo文件夹，同时在屏幕上实时显示图像。
'''

from machine import Pin
from machine import FPIOA
import time, os, sys

from media.sensor import * # 导入sensor模块，使用摄像头相关接口
from media.display import * # 导入display模块，使用display相关接口
from media.media import * # 导入media模块，使用meida相关接口

# --- 按键配置 ---
fpioa = FPIOA()
fpioa.set_function(21, FPIOA.GPIO21)
KEY = Pin(21, Pin.IN, Pin.PULL_UP) # 构建KEY对象，带内部上拉

# --- 摄像头配置 ---
sensor = Sensor() # 构建摄像头对象
sensor.reset() # 复位和初始化摄像头
sensor.set_framesize(width=800, height=480) # 设置帧大小为LCD分辨率(800x480)，默认通道0
sensor.set_pixformat(Sensor.RGB565) # 设置输出图像格式，默认通道0

# 初始化显示屏
# 同时使用3.5寸mipi屏和IDE缓冲区显示图像，800x480分辨率
Display.init(Display.ST7701, width=800, height=480, to_ide=True)

# --- 显示屏初始化（选择一种） ---
# K230通常支持多种显示方式，选择其中一种取消注释。
# 默认通过IDE缓冲区显示图像，方便调试。
Display.init(Display.VIRT, sensor.width(), sensor.height()) # 通过IDE缓冲区显示图像
# Display.init(Display.LT9611, to_ide=True) # 如果连接了HDMI显示器，请取消注释此行
# Display.init(Display.ST7701, to_ide=True) # 如果连接了01Studio 3.5寸mipi显示屏，请取消注释此行

MediaManager.init() # 初始化media资源管理器

sensor.run() # 启动sensor

clock = time.clock()

# --- 检查并创建photo文件夹，使用 /sdcard/photo 路径 ---
PHOTO_DIR = "/sdcard/photo"

try:
    os.mkdir(PHOTO_DIR)
    print(f"创建文件夹: {PHOTO_DIR}")
except OSError as e:
    if e.args[0] == 17: # 错误码17表示目录已存在 (EEXIST)
        print(f"文件夹 {PHOTO_DIR} 已存在。")
    else:
        print(f"创建文件夹失败: {e}，请检查SD卡是否插入或权限。")
        # 如果文件夹无法创建，拍照功能将受影响，这里可以选择退出程序
        # sys.exit()

while True:
    clock.tick()

    # --- 实时图像采集与显示 ---
    img = sensor.snapshot() # 捕获当前帧
    Display.show_image(img) # 将图像显示到屏幕上 (IDE/HDMI/MIPI屏)
    # print(clock.fps()) # 打印帧率，如果不需要可以注释掉

    # --- 按键检测逻辑 ---
    if KEY.value() == 0: # 按键被按下
        time.sleep_ms(20) # 消除抖动
        if KEY.value() == 0: # 确认按键被按下

            # 生成文件名 (例如: photo_20250721_154230.jpg)
            # 使用当前日期和时间作为文件名的一部分，确保唯一性
            timestamp = time.localtime()
            filename = "{}/photo_{:04d}{:02d}{:02d}_{:02d}{:02d}{:02d}.jpg".format(
                PHOTO_DIR,
                timestamp[0], timestamp[1], timestamp[2],
                timestamp[3], timestamp[4], timestamp[5]
            )

            try:
                img.save(filename) # 保存图片
                print(f"照片已保存到: {filename}")
            except Exception as e:
                print(f"保存照片失败: {e}，请检查SD卡写入权限或存储空间。")

            while not KEY.value(): # 检测按键是否松开
                pass # 等待按键释放，防止重复触发
            print("按键已释放。")
