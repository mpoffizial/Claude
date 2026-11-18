"""Kanal-Banner: Platz im Layout, Einblenden als Bild oder Video-Loop, Pfad-Auflösung."""

import subprocess
from pathlib import Path

from clipmaschine.config import EditingConfig, load_config, resolve_asset
from clipmaschine.editor import MediaInfo, plan_blur, plan_crop, probe, render_vertical

from .conftest import make_video, needs_ffmpeg


def _pixel(video: Path, x: int, y: int, at: float = 1.0) -> tuple[int, int, int]:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(video), "-frames:v", "1",
                          "-vf", f"format=rgb24,crop=2:2:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return raw[0], raw[1], raw[2]


def _dark_source(path: Path, seconds: float = 3.0) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x101010:s=1280x720:rate=30:"
                    f"duration={seconds}", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(path)], check=True)
    return path


def test_banner_space_only_above_landscape_video():
    cfg = EditingConfig()
    box = plan_blur(MediaInfo(1920, 1080, 30, 60, True), cfg).banner_box
    assert box is not None
    _, y, _, h = box
    assert y >= int(1920 * 0.08)                                  # unter den App-Tabs
    assert y + h <= 656 - int(1920 * 0.02) - int(1920 * 0.13)     # Platz für zwei Zeilen Hook bleibt
    assert plan_blur(MediaInfo(1080, 1920, 30, 30, True), cfg).banner_box is None   # Hochkant: kein Platz
    assert plan_crop(MediaInfo(1920, 1080, 30, 60, True), cfg).banner_box is None   # bildfüllend: kein Platz


@needs_ffmpeg
def test_image_banner_is_drawn_above_the_video(tmp_path):
    banner = tmp_path / "banner.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=800x200", "-frames:v", "1",
                    str(banner)], check=True)
    cfg = EditingConfig(width=540, height=960, preset="ultrafast", crf=30, banner=str(banner),
                        banner_border_color="", smart_trim=False, punch_zoom=False)
    out = render_vertical(_dark_source(tmp_path / "src.mp4"), tmp_path / "out.mp4", cfg, hook="Hook", credit=None)
    _, y, _, h = plan_blur(MediaInfo(1280, 720, 30, 3, True), cfg).banner_box
    r, g, b = _pixel(out, 270, y + h // 2)
    assert r > 200 and g < 60 and b < 60                          # roter Banner mittig in der freien Fläche
    r, g, b = _pixel(out, 270, 960 // 2)
    assert r < 60                                                 # das Video selbst bleibt unberührt
    assert abs(probe(out).duration - 3.0) < 0.2                   # endloses Banner-Bild verlängert nichts


@needs_ffmpeg
def test_video_banner_loops_over_longer_clip(tmp_path):
    banner = tmp_path / "loop.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=640x160:rate=30:duration=1",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(banner)], check=True)
    cfg = EditingConfig(width=540, height=960, preset="ultrafast", crf=30, banner=str(banner),
                        smart_trim=False, punch_zoom=False)
    out = render_vertical(_dark_source(tmp_path / "src.mp4"), tmp_path / "out.mp4", cfg, hook=None, credit=None)
    _, y, _, h = plan_blur(MediaInfo(1280, 720, 30, 3, True), cfg).banner_box
    r, g, b = _pixel(out, 270, y + h // 2, at=2.5)              # nach Ende des 1-s-Loops immer noch da
    assert b > 200 and r < 60
    info = probe(out)
    assert (info.width, info.height) == (540, 960) and abs(info.duration - 3.0) < 0.2


@needs_ffmpeg
def test_missing_banner_or_vertical_source_renders_without_banner(tmp_path):
    cfg = EditingConfig(width=540, height=960, preset="ultrafast", crf=30, banner=str(tmp_path / "fehlt.mp4"))
    out = render_vertical(make_video(tmp_path / "a.mp4"), tmp_path / "a_out.mp4", cfg, hook="Hook", credit=None)
    assert probe(out).width == 540
    vertical = make_video(tmp_path / "v.mp4", size="720x1280", seconds=1.5)
    cfg.banner = str(tmp_path / "a.mp4")
    out = render_vertical(vertical, tmp_path / "v_out.mp4", cfg, hook="Hook", credit=None)
    assert probe(out).height == 960


def test_banner_path_is_found_beside_config_or_in_program_folder(tmp_path):
    (tmp_path / "eigen.png").write_bytes(b"x")
    assert resolve_asset("eigen.png", tmp_path) == (tmp_path / "eigen.png").resolve()
    shipped = resolve_asset("presets/branding/gta6_profilbild_800.jpg", tmp_path)
    assert shipped.is_file() and shipped.parent.name == "branding"
    config = tmp_path / "config.yaml"
    config.write_text("streamers: [x]\ndata_dir: data\nediting:\n  banner: presets/branding/gta6_banner_loop.mp4\n",
                      encoding="utf-8")
    assert Path(load_config(config).editing.banner).is_absolute()
