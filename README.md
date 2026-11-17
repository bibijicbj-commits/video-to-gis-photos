# 视频转GIS图片

仓库名：`video-to-gis-photos`

把大疆航拍 **MP4/MOV + 同名 SRT** 按间隔抽成 JPEG，并把字幕里的 GPS、高度、拍摄时间写入图片，方便导入 iTwin Capture、RealityScan、Metashape、Postshot、COLMAP。

变焦还在变化的帧（字幕 `delta` 不是 0）不会导出。倍率停住的帧会留下，焦距写成 `镜头焦距 × (dzoom_ratio / 10000)`，同时写入 35 mm 等效焦距。1 倍和 2 倍放在同一文件夹，靠焦距不同分成不同相机。某个变焦挡位只剩 1 张时也会剔除。

## 写入图片的信息

这些字段写在 JPEG 的 EXIF 里。字幕里的变焦倍率和 `delta` 不写入图片，只用来决定是否保留，以及焦距乘多少。没有姿态角。

| 信息 | EXIF 字段 | 内容 |
|---|---|---|
| 相机标识 | Make、Model、Software | DJI、DJI、V_to_T |
| 方向 | Orientation | 1，不旋转 |
| 色彩空间 | ColorSpace | sRGB |
| 拍摄时间 | DateTime、DateTimeOriginal、DateTimeDigitized，以及 GPS 日期和时间 | 字幕拍摄时刻，到秒 |
| 纬度 | GPSLatitude、GPSLatitudeRef | 字幕纬度，北纬 N，南纬 S |
| 经度 | GPSLongitude、GPSLongitudeRef | 字幕经度，东经 E，西经 W |
| 高度 | GPSAltitude、GPSAltitudeRef | 字幕高度，单位米；正值为海拔 |
| 感光度 | ISOSpeedRatings | 字幕 iso |
| 快门 | ExposureTime | 字幕 shutter，如 1/1000 |
| 光圈 | FNumber | 字幕 fnum。280 写成 f/2.8 |
| 焦距 | FocalLength | 镜头焦距乘变焦倍率。1 倍 22.4 mm，2 倍 44.8 mm |
| 35 mm 等效焦距 | FocalLengthIn35mmFilm | 与焦距相同，但只能存整数。22.4 写成 22，44.8 写成 45 |

勾选「画面叠加 GPS」时，时间和经纬高还会画到画面上。导入空三前不要勾选，空三只读 EXIF。

## 使用

1. 安装 [Python 3.11](https://www.python.org/downloads/)
2. 双击 `启动工具.bat`（会自动安装依赖并打开界面）
3. 选择视频目录（视频和同名 `.SRT` 放在一起）
4. 在列表里勾选要处理的视频。扫描到的配对默认不勾选。按住 Shift 点击，会把上次点击到这次点击之间的视频都勾上。需要全部处理时点 **全选**
5. 设置抽帧间隔（默认 1 秒），点 **开始抽帧**
6. 需要中途停下时点 **停止**，已经导出的图片会保留

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
