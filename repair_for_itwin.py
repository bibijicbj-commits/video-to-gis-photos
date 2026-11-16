"""Re-encode existing frames into iTwin-safe JPEGs with clean GPS EXIF."""

from __future__ import annotations

import argparse
import csv
import re
from datetime import datetime
from pathlib import Path

import piexif

from extract_frames import emit, normalize_jpeg, read_gps, stopped, write_clean_exif

NAME_RE = re.compile(r"^(?P<stem>.+)_(?P<idx>\d{4})(?:_t.*)?$", re.I)


def parse_exif_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def camera_from_exif(path: Path) -> dict:
    data = piexif.load(str(path))
    exif = data.get("Exif") or {}
    result = {"iso": None, "shutter": None, "fnum": None, "focal_len": None}
    iso = exif.get(piexif.ExifIFD.ISOSpeedRatings)
    if iso is not None:
        result["iso"] = str(iso)
    shutter = exif.get(piexif.ExifIFD.ExposureTime)
    if shutter and shutter[1]:
        result["shutter"] = f"{shutter[0]}/{shutter[1]}"
    fnumber = exif.get(piexif.ExifIFD.FNumber)
    if fnumber and fnumber[1]:
        result["fnum"] = str(100 * fnumber[0] / fnumber[1])
    focal = exif.get(piexif.ExifIFD.FocalLength)
    if focal and focal[1]:
        result["focal_len"] = str(10 * focal[0] / focal[1])
    return result


def repair_folder(src_dir: Path, dst_dir: Path, log=None, cancel=None) -> int:
    images = sorted(p for p in src_dir.glob("*.jpg") if p.is_file())
    images += sorted(p for p in src_dir.glob("*.JPG") if p.is_file() and p not in images)
    dst_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for src in images:
        if stopped(cancel):
            emit(f"已停止修复 {src_dir.name}，已处理 {len(rows)} 张", log)
            break
        match = NAME_RE.match(src.stem)
        if match:
            name = f"{match.group('stem')}_{match.group('idx')}.jpg"
        else:
            name = re.sub(r"[^A-Za-z0-9_.-]+", "_", src.stem) + ".jpg"
            name = name.replace("..", ".")
        dest = dst_dir / name
        dest.write_bytes(src.read_bytes())
        gps = read_gps(dest)
        cam = camera_from_exif(dest)
        normalize_jpeg(dest)
        write_clean_exif(
            dest,
            latitude=gps.get("latitude"),
            longitude=gps.get("longitude"),
            altitude=gps.get("altitude"),
            captured=parse_exif_datetime(gps.get("datetime")),
            **cam,
        )
        rows.append(
            {
                "file": dest.name,
                "source": src.name,
                "latitude": gps.get("latitude"),
                "longitude": gps.get("longitude"),
                "altitude": gps.get("altitude"),
                "datetime": gps.get("datetime"),
            }
        )
        emit(f"修复 {src.name} -> {dest.name}", log)
    if rows:
        csv_path = dst_dir / f"{dst_dir.name}_frames.csv"
        with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    return len(rows)


def repair_root(src_root: Path, dst_root: Path, log=None, cancel=None) -> int:
    src_root = src_root.resolve()
    dst_root = dst_root.resolve()
    folders = [p for p in src_root.iterdir() if p.is_dir() and any(p.glob("*.jpg"))]
    if not folders:
        folders = [src_root] if any(src_root.glob("*.jpg")) else []
    if not folders:
        raise FileNotFoundError(f"没有找到 JPEG: {src_root}")

    total = 0
    for folder in folders:
        if stopped(cancel):
            emit("已停止，跳过剩余目录", log)
            break
        count = repair_folder(folder, dst_root / folder.name, log=log, cancel=cancel)
        total += count
        emit(f"{folder.name}: {count} 张", log)
    status = "已停止" if stopped(cancel) else "完成"
    emit(f"{status} {total} 张，输出目录: {dst_root}", log)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="把已导出的 JPEG 整理成 iTwin 可浏览格式")
    parser.add_argument("--input", type=Path, default=Path(__file__).resolve().parents[1] / "output")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "output_itwin")
    args = parser.parse_args()
    try:
        repair_root(args.input, args.output)
    except FileNotFoundError as exc:
        print(exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
