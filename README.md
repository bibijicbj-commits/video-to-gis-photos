# 视频转GIS图片

仓库名：`video-to-gis-photos`

把大疆航拍 **MP4/MOV + 同名 SRT** 按间隔抽成 JPEG，并把字幕里的 GPS、高度、拍摄时间写入图片，方便导入 iTwin Capture、RealityScan、Postshot、COLMAP。

## 使用

1. 安装 [Python 3.11](https://www.python.org/downloads/)
2. 双击 `启动工具.bat`（会自动安装依赖并打开界面）
3. 选择视频目录（视频和同名 `.SRT` 放在一起）
4. 设置抽帧间隔（默认 1 秒），点 **开始抽帧**
5. 需要中途停下时点 **停止**，已经导出的图片会保留

导入摄影测量软件前，不要勾选「画面叠加 GPS」。

## 命令行

```bat
cd /d 本目录
py -3.11 extract_frames.py --input 视频目录 --output 输出目录 --interval 1
py -3.11 repair_for_itwin.py --input 已有图片目录 --output 修复输出目录
```

## 依赖

- Python 3.11
- `piexif`、`pillow`、`imageio-ffmpeg`（见 `requirements.txt`）
- FFmpeg：系统 PATH、WinGet，或由 `imageio-ffmpeg` 提供

## 文件

| 文件 | 作用 |
|---|---|
| `启动工具.bat` | 用户入口 |
| `gui.py` | 图形界面 |
| `extract_frames.py` | 抽帧、解析 SRT、写入 EXIF |
| `repair_for_itwin.py` | 修复已有 JPEG，方便 iTwin 读取 |
