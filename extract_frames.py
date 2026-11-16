"""Extract stills from DJI MP4 + SRT and write matching telemetry into JPEGs."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

import piexif

try:
    from imageio_ffmpeg import get_ffmpeg_exe
except ImportError:  # pragma: no cover
    get_ffmpeg_exe = None


TIME_RE = re.compile(
    r"(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})[,.](?P<ms>\d+)"
)
FIELD_RE = re.compile(r"\[(\w+)\s*:\s*([^\]]+)\]")
DT_RE = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2})(?:[,.](?P<frac>\d+))?"
)
VIDEO_EXTS = {".mp4", ".mov"}
SKIP_DIR_NAMES = {
    "__pycache__",
    ".git",
    "frame_tool",
    "video-to-gis-photos",
    "P",
    "sparse",
    "postshot_colmap",
    "Project files",
    "output",
    "output_1s",
    "output_itwin",
}

_current_proc: subprocess.Popen | None = None


def stopped(cancel: Any = None) -> bool:
    return cancel is not None and cancel.is_set()


def _stop_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    if sys.platform == "win32" and proc.pid:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        return
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except Exception:
        pass
    if proc.poll() is None:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass


def stop_current_ffmpeg() -> None:
    _stop_process(_current_proc)


@dataclass
class Telemetry:
    index: int
    start_sec: float
    end_sec: float
    capture_time: str
    latitude: float | None
    longitude: float | None
    altitude: float | None
    rel_alt: float | None
    abs_alt: float | None
    iso: str | None
    shutter: str | None
    fnum: str | None
    ev: str | None
    ct: str | None
    color_md: str | None
    focal_len: str | None
    dzoom_ratio: str | None
    raw: str


def parse_srt_time(value: str) -> float:
    match = TIME_RE.search(value.strip())
    if not match:
        raise ValueError(f"无法解析时间码: {value!r}")
    ms = match.group("ms").ljust(3, "0")[:3]
    return (
        int(match.group("h")) * 3600
        + int(match.group("m")) * 60
        + int(match.group("s"))
        + int(ms) / 1000.0
    )


def parse_srt(path: Path) -> list[Telemetry]:
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\n\s*\n", text.strip())
    records: list[Telemetry] = []

    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue

        start_raw, end_raw = [part.strip() for part in lines[1].split("-->")]
        body = "\n".join(lines[2:])
        fields = {key: value.strip() for key, value in FIELD_RE.findall(body)}
        dt_match = DT_RE.search(body)

        def maybe_float(name: str) -> float | None:
            value = fields.get(name)
            if value is None:
                return None
            match = re.match(r"[-+]?\d+(?:\.\d+)?", value.strip())
            if not match:
                return None
            return float(match.group(0))

        combo = re.search(
            r"rel_alt\s*:\s*([-+]?\d+(?:\.\d+)?)\s+abs_alt\s*:\s*([-+]?\d+(?:\.\d+)?)",
            body,
        )
        rel_alt = maybe_float("rel_alt")
        abs_alt = maybe_float("abs_alt")
        if combo:
            rel_alt = float(combo.group(1))
            abs_alt = float(combo.group(2))
        altitude = maybe_float("altitude")
        if altitude is None:
            altitude = abs_alt if abs_alt is not None else rel_alt

        records.append(
            Telemetry(
                index=int(lines[0]) if lines[0].isdigit() else len(records) + 1,
                start_sec=round(parse_srt_time(start_raw), 3),
                end_sec=round(parse_srt_time(end_raw), 3),
                capture_time=dt_match.group(0) if dt_match else "",
                latitude=maybe_float("latitude"),
                longitude=maybe_float("longitude"),
                altitude=altitude,
                rel_alt=rel_alt,
                abs_alt=abs_alt,
                iso=fields.get("iso"),
                shutter=fields.get("shutter"),
                fnum=fields.get("fnum"),
                ev=fields.get("ev"),
                ct=fields.get("ct"),
                color_md=fields.get("color_md"),
                focal_len=fields.get("focal_len"),
                dzoom_ratio=fields.get("dzoom_ratio"),
                raw=re.sub(r"</?font[^>]*>", "", body).strip(),
            )
        )

    if not records:
        raise ValueError(f"SRT 中没有解析到有效记录: {path}")
    return records


def nearest_telemetry(records: list[Telemetry], t: float) -> Telemetry:
    best = records[0]
    best_dist = abs((best.start_sec + best.end_sec) / 2 - t)
    for rec in records:
        if rec.start_sec <= t < rec.end_sec:
            return rec
        dist = min(abs(rec.start_sec - t), abs(rec.end_sec - t))
        if dist < best_dist:
            best = rec
            best_dist = dist
    return best


def find_ffmpeg() -> str:
    from shutil import which

    exe = which("ffmpeg")
    if exe:
        return exe

    winget = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
    if winget.exists():
        matches = sorted(winget.glob("Gyan.FFmpeg*/**/ffmpeg.exe"))
        if matches:
            return str(matches[0])

    if get_ffmpeg_exe is not None:
        return get_ffmpeg_exe()
    raise RuntimeError("未找到 FFmpeg。请安装 FFmpeg，或 pip install imageio-ffmpeg")


def emit(message: str, log=None) -> None:
    print(message)
    if log:
        log(message)


def load_font(size: int):
    from PIL import ImageFont

    candidates = [
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\msyhbd.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\simsun.ttc"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
    ]
    for font_path in candidates:
        if font_path.exists():
            return ImageFont.truetype(str(font_path), size=size, index=0)
    return ImageFont.load_default()


def draw_overlay(image_path: Path, rec: Telemetry, video_time: float) -> None:
    from PIL import Image, ImageDraw

    image = Image.open(image_path).convert("RGB")
    width, height = image.size
    font_size = max(32, height // 42)
    font = load_font(font_size)
    small = load_font(max(24, font_size * 3 // 4))

    lat = f"{rec.latitude:.6f}" if rec.latitude is not None else "--"
    lon = f"{rec.longitude:.6f}" if rec.longitude is not None else "--"
    alt = f"{rec.altitude:.1f} m" if rec.altitude is not None else "--"
    lines = [
        rec.capture_time.split(",")[0] or f"视频 {video_time:.1f}s",
        f"纬度 {lat}    经度 {lon}    高度 {alt}",
        f"ISO {rec.iso or '--'}    快门 {rec.shutter or '--'}    视频时间 {video_time:.1f}s",
    ]

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    padding = font_size // 2
    line_gap = max(8, font_size // 6)
    text_heights = []
    max_text_w = 0
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font if i == 0 else small)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        max_text_w = max(max_text_w, text_w)
        text_heights.append(text_h)

    bar_h = padding * 2 + sum(text_heights) + line_gap * (len(lines) - 1)
    bar_w = min(width, max_text_w + padding * 2)
    x0, y0 = padding, height - bar_h - padding
    draw.rounded_rectangle(
        (x0, y0, x0 + bar_w, y0 + bar_h),
        radius=max(8, font_size // 4),
        fill=(0, 0, 0, 160),
    )

    y = y0 + padding
    for i, line in enumerate(lines):
        used = font if i == 0 else small
        draw.text((x0 + padding, y), line, font=used, fill=(255, 255, 255, 255))
        y += text_heights[i] + line_gap

    Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB").save(
        image_path, quality=95, subsampling=0
    )


def write_gpx(path: Path, video_name: str, rows: list[dict]) -> None:
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="V_to_T" xmlns="http://www.topografix.com/GPX/1/1">',
        f"  <trk><name>{video_name}</name><trkseg>",
    ]
    for row in rows:
        lat, lon = row.get("latitude"), row.get("longitude")
        if lat is None or lon is None:
            continue
        ele = row.get("altitude")
        match = DT_RE.search(row.get("capture_time") or "")
        dt = None
        if match:
            frac = (match.group("frac") or "0")[:6].ljust(6, "0")
            try:
                dt = datetime.strptime(
                    f"{match.group('date')} {match.group('time')}.{frac}",
                    "%Y-%m-%d %H:%M:%S.%f",
                )
            except ValueError:
                dt = None
        time_xml = f"<time>{dt.strftime('%Y-%m-%dT%H:%M:%S')}</time>" if dt else ""
        ele_xml = f"<ele>{ele}</ele>" if ele is not None else ""
        parts.append(
            f'    <trkpt lat="{lat:.6f}" lon="{lon:.6f}">{ele_xml}{time_xml}</trkpt>'
        )
    parts.extend(["  </trkseg></trk>", "</gpx>", ""])
    path.write_text("\n".join(parts), encoding="utf-8")


def extract_jpegs(
    ffmpeg: str,
    video: Path,
    out_dir: Path,
    interval: float,
    max_duration: float | None,
    cancel=None,
) -> list[Path]:
    global _current_proc
    pattern = out_dir / "frame_%04d.jpg"
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video),
    ]
    if max_duration is not None:
        cmd.extend(["-t", f"{max_duration:.3f}"])
    cmd.extend(
        [
            "-vf",
            f"fps=1/{interval}",
            "-q:v",
            "2",
            str(pattern),
        ]
    )
    popen_kw: dict = {}
    if sys.platform == "win32":
        popen_kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(cmd, **popen_kw)
    _current_proc = proc
    try:
        while True:
            if stopped(cancel):
                _stop_process(proc)
                break
            try:
                proc.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
            except KeyboardInterrupt:
                _stop_process(proc)
                raise
        frames = sorted(out_dir.glob("frame_*.jpg"))
        if stopped(cancel):
            return frames
        if proc.returncode:
            raise subprocess.CalledProcessError(proc.returncode, cmd)
        return frames
    finally:
        if _current_proc is proc:
            _current_proc = None


def to_dms(value: float) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    value = abs(value)
    degrees = int(value)
    minutes_f = (value - degrees) * 60
    minutes = int(minutes_f)
    seconds = (minutes_f - minutes) * 60
    sec = Fraction(seconds).limit_denominator(100000)
    return ((degrees, 1), (minutes, 1), (sec.numerator, sec.denominator))


def to_rational(value: float, max_den: int = 10000) -> tuple[int, int]:
    frac = Fraction(abs(value)).limit_denominator(max_den)
    return (frac.numerator, frac.denominator)


def parse_shutter(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    text = value.strip()
    try:
        if "/" in text:
            num, den = text.split("/", 1)
            return (int(float(num)), int(float(den)))
        return to_rational(float(text), 100000)
    except ValueError:
        return None


def dji_fnumber(value: str | None) -> float | None:
    if not value:
        return None
    try:
        raw = float(value)
    except ValueError:
        return None
    return raw / 100.0 if raw >= 20 else raw


def dji_focal_mm(value: str | None) -> float | None:
    if not value:
        return None
    try:
        raw = float(value)
    except ValueError:
        return None
    return raw / 10.0 if raw >= 50 else raw


def capture_datetime(rec: Telemetry) -> datetime | None:
    match = DT_RE.search(rec.capture_time)
    if not match:
        return None
    frac = (match.group("frac") or "0")[:6].ljust(6, "0")
    try:
        return datetime.strptime(
            f"{match.group('date')} {match.group('time')}.{frac}",
            "%Y-%m-%d %H:%M:%S.%f",
        )
    except ValueError:
        return None


def write_exif(image_path: Path, rec: Telemetry, video_time: float) -> None:
    dt = capture_datetime(rec)
    write_clean_exif(
        image_path,
        latitude=rec.latitude,
        longitude=rec.longitude,
        altitude=rec.altitude,
        captured=dt,
        iso=rec.iso,
        shutter=rec.shutter,
        fnum=rec.fnum,
        focal_len=rec.focal_len,
    )


def write_clean_exif(
    image_path: Path,
    *,
    latitude: float | None,
    longitude: float | None,
    altitude: float | None,
    captured: datetime | None,
    iso: str | None = None,
    shutter: str | None = None,
    fnum: str | None = None,
    focal_len: str | None = None,
) -> None:
    """Write a conservative EXIF block that photogrammetry viewers can parse."""
    zeroth: dict = {
        piexif.ImageIFD.Make: "DJI",
        piexif.ImageIFD.Model: "DJI",
        piexif.ImageIFD.Software: "V_to_T",
        piexif.ImageIFD.Orientation: 1,
    }
    exif: dict = {
        piexif.ExifIFD.ColorSpace: 1,
    }
    gps: dict = {
        piexif.GPSIFD.GPSVersionID: (2, 3, 0, 0),
    }

    if captured:
        stamp = captured.strftime("%Y:%m:%d %H:%M:%S")
        zeroth[piexif.ImageIFD.DateTime] = stamp
        exif[piexif.ExifIFD.DateTimeOriginal] = stamp
        exif[piexif.ExifIFD.DateTimeDigitized] = stamp
        gps[piexif.GPSIFD.GPSDateStamp] = captured.strftime("%Y:%m:%d")
        gps[piexif.GPSIFD.GPSTimeStamp] = (
            (captured.hour, 1),
            (captured.minute, 1),
            (captured.second, 1),
        )

    if latitude is not None:
        gps[piexif.GPSIFD.GPSLatitude] = to_dms(latitude)
        gps[piexif.GPSIFD.GPSLatitudeRef] = "N" if latitude >= 0 else "S"
    if longitude is not None:
        gps[piexif.GPSIFD.GPSLongitude] = to_dms(longitude)
        gps[piexif.GPSIFD.GPSLongitudeRef] = "E" if longitude >= 0 else "W"
    if altitude is not None:
        gps[piexif.GPSIFD.GPSAltitude] = to_rational(altitude, 100)
        gps[piexif.GPSIFD.GPSAltitudeRef] = 0 if altitude >= 0 else 1

    if iso:
        try:
            exif[piexif.ExifIFD.ISOSpeedRatings] = int(float(iso))
        except ValueError:
            pass
    shutter_r = parse_shutter(shutter)
    if shutter_r:
        exif[piexif.ExifIFD.ExposureTime] = shutter_r
    fnumber = dji_fnumber(fnum)
    if fnumber:
        exif[piexif.ExifIFD.FNumber] = to_rational(fnumber, 100)
    focal = dji_focal_mm(focal_len)
    if focal:
        exif[piexif.ExifIFD.FocalLength] = to_rational(focal, 100)

    piexif.insert(piexif.dump({"0th": zeroth, "Exif": exif, "GPS": gps}), str(image_path))


def normalize_jpeg(image_path: Path) -> None:
    from PIL import Image

    with Image.open(image_path) as image:
        rgb = image.convert("RGB")
        rgb.save(image_path, format="JPEG", quality=95, subsampling=0)


def dms_to_deg(values) -> float:
    deg, minutes, seconds = values
    return deg[0] / deg[1] + minutes[0] / minutes[1] / 60 + seconds[0] / seconds[1] / 3600


def read_gps(image_path: Path) -> dict:
    data = piexif.load(str(image_path))
    gps = data.get("GPS") or {}
    lat = gps.get(piexif.GPSIFD.GPSLatitude)
    lon = gps.get(piexif.GPSIFD.GPSLongitude)
    alt = gps.get(piexif.GPSIFD.GPSAltitude)
    result = {}
    if lat:
        sign = 1 if gps.get(piexif.GPSIFD.GPSLatitudeRef, b"N") in (b"N", "N") else -1
        result["latitude"] = sign * dms_to_deg(lat)
    if lon:
        sign = 1 if gps.get(piexif.GPSIFD.GPSLongitudeRef, b"E") in (b"E", "E") else -1
        result["longitude"] = sign * dms_to_deg(lon)
    if alt:
        result["altitude"] = alt[0] / alt[1]
    dt = (data.get("Exif") or {}).get(piexif.ExifIFD.DateTimeOriginal)
    if dt:
        result["datetime"] = dt.decode() if isinstance(dt, bytes) else dt
    return result


def matching_srt(video: Path) -> Path | None:
    for suffix in (".SRT", ".srt"):
        candidate = video.with_suffix(suffix)
        if candidate.exists():
            return candidate
    return None


def iter_videos(input_dir: Path) -> list[Path]:
    videos: list[Path] = []
    seen: set[Path] = set()
    for current, dirnames, filenames in os.walk(input_dir):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIR_NAMES]
        for name in filenames:
            path = Path(current) / name
            if path.suffix.lower() not in VIDEO_EXTS:
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            videos.append(path)
    videos.sort()
    return videos


def find_pairs(input_dir: Path, only: str | None = None, log=None) -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    for video in iter_videos(input_dir):
        if only and only.lower() not in video.stem.lower():
            continue
        srt = matching_srt(video)
        if srt:
            pairs.append((video, srt))
        else:
            emit(f"跳过 {video.name}: 没有同名 SRT", log)
    return pairs


def process_pair(
    video: Path,
    srt: Path,
    output_root: Path,
    interval: float,
    max_duration: float | None,
    ffmpeg: str,
    overlay: bool = False,
    export_gpx: bool = True,
    log=None,
    cancel=None,
) -> dict:
    records = parse_srt(srt)
    out_dir = output_root / video.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in list(out_dir.glob("frame_*.jpg")) + list(out_dir.glob(f"{video.stem}_*.jpg")):
        old.unlink()

    emit(f"抽帧: {video.name}  间隔 {interval}s", log)
    images = extract_jpegs(ffmpeg, video, out_dir, interval, max_duration, cancel=cancel)
    if stopped(cancel):
        emit(f"已停止抽帧 {video.name}，已导出 {len(images)} 张，正在写入 GPS", log)
    rows = []
    dests: list[Path] = []
    for i, image in enumerate(images):
        t = i * interval
        rec = nearest_telemetry(records, t)
        dest = out_dir / f"{video.stem}_{i:04d}.jpg"
        image.rename(dest)
        if overlay and not stopped(cancel):
            draw_overlay(dest, rec, t)
        normalize_jpeg(dest)
        write_exif(dest, rec, t)
        dests.append(dest)
        rows.append(
            {
                "file": dest.name,
                "video_time_sec": round(t, 3),
                **{k: v for k, v in asdict(rec).items() if k != "raw"},
            }
        )

    csv_path = out_dir / f"{video.stem}_frames.csv"
    gpx_path = out_dir / f"{video.stem}.gpx"
    if rows:
        with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        if export_gpx:
            write_gpx(gpx_path, video.stem, rows)

    gps_ok = 0
    samples = []
    sample_indexes = {0, len(dests) // 2, len(dests) - 1} if dests else set()
    for i, (row, image) in enumerate(zip(rows, dests)):
        gps = read_gps(image)
        if "latitude" in gps and "longitude" in gps:
            gps_ok += 1
        if i in sample_indexes:
            samples.append(
                {
                    "file": image.name,
                    "srt": {
                        "lat": row["latitude"],
                        "lon": row["longitude"],
                        "alt": row["altitude"],
                    },
                    "exif": gps,
                }
            )

    summary = {
        "video": video.name,
        "srt_records": len(records),
        "duration_sec": round(records[-1].end_sec, 3),
        "extracted": len(rows),
        "gps_written": gps_ok,
        "csv": str(csv_path),
        "gpx": str(gpx_path) if export_gpx else None,
        "samples": samples,
    }
    status = "已停止" if stopped(cancel) else "完成"
    emit(
        f"{status} {video.name}: {len(rows)} 张, GPS {gps_ok}/{len(rows)}, "
        f"SRT {len(records)} 条, 时长 {records[-1].end_sec:.1f}s",
        log,
    )
    return summary


def run_batch(
    input_dir: Path,
    output_dir: Path,
    interval: float = 1.0,
    max_duration: float | None = None,
    only: str | None = None,
    overlay: bool = False,
    export_gpx: bool = True,
    log=None,
    cancel=None,
) -> list[dict]:
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    pairs = find_pairs(input_dir, only, log=log)
    if not pairs:
        raise FileNotFoundError("没有找到 MP4/MOV + 同名 SRT 配对文件")

    ffmpeg = find_ffmpeg()
    emit(f"FFmpeg: {ffmpeg}", log)
    summaries = []
    for video, srt in pairs:
        if stopped(cancel):
            emit("已停止，跳过剩余视频", log)
            break
        summaries.append(
            process_pair(
                video,
                srt,
                output_dir,
                interval,
                max_duration,
                ffmpeg,
                overlay=overlay,
                export_gpx=export_gpx,
                log=log,
                cancel=cancel,
            )
        )
        if stopped(cancel):
            emit("已停止，跳过剩余视频", log)
            break

    report = output_dir / "run_summary.json"
    report.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    emit(f"汇总: {report}", log)
    return summaries


def main() -> int:
    parser = argparse.ArgumentParser(description="从航拍视频按间隔截图，并把同名 SRT 写入 JPEG")
    parser.add_argument("--input", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--interval", type=float, default=1.0, help="抽帧间隔（秒）")
    parser.add_argument("--max-duration", type=float, default=None, help="只处理前 N 秒，用于试跑")
    parser.add_argument("--only", type=str, default=None, help="只处理文件名包含该字符串的视频")
    parser.add_argument("--overlay", action="store_true", help="在画面上烧录时间/经纬度/高度")
    parser.add_argument("--no-gpx", action="store_true", help="不导出 GPX")
    parser.add_argument("--gui", action="store_true", help="打开图形界面")
    args = parser.parse_args()

    if args.gui:
        from gui import main as gui_main

        gui_main()
        return 0

    input_dir = args.input.resolve()
    output_dir = (args.output or (input_dir / "output")).resolve()
    try:
        run_batch(
            input_dir,
            output_dir,
            interval=args.interval,
            max_duration=args.max_duration,
            only=args.only,
            overlay=args.overlay,
            export_gpx=not args.no_gpx,
        )
    except FileNotFoundError as exc:
        print(exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
