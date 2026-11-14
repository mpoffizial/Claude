"""Virales Format "Hook-First": Höhepunkt-Erkennung, Schnittplan, Teaser, Zoom, Fortschrittsbalken."""

import subprocess

from clipmaschine.captions import CaptionChunk, Word
from clipmaschine.config import EditingConfig
from clipmaschine.editor import (CutPlan, MediaInfo, Segment, build_overlay, loudest_moment, plan_blur, plan_cut,
                                 probe, render_vertical, shift_captions)

from .conftest import needs_ffmpeg

INFO = MediaInfo(1920, 1080, 40.0, 60, True)


def test_plan_cut_trims_around_peak_with_zoom_and_teaser():
    cfg = EditingConfig(cold_open=True)
    cut = plan_cut(INFO, cfg, peak=20.0)
    teaser, before, zoom, after = cut.segments
    assert abs(teaser.start - 19.4) < 1e-6 and abs(teaser.end - 20.6) < 1e-6 and teaser.zoom == 1.15
    assert abs(cut.teaser - 1.2) < 1e-6
    assert before.start == 8.0 and after.end == 25.0                 # 12 s davor, 5 s danach
    assert zoom.zoom == cfg.punch_zoom_factor and zoom.start < 20.0 < zoom.end
    assert abs(cut.duration - 18.2) < 1e-6
    assert abs(cut.to_output(8.0) - 1.2) < 1e-6
    assert cut.to_output(7.9) is None                                # vor dem Hauptteil: weggeschnitten
    assert abs(cut.to_output(20.0) - 13.2) < 1e-6


def test_plan_cut_edge_cases():
    cfg = EditingConfig()
    assert plan_cut(INFO, cfg, None).segments == [Segment(0.0, 40.0)]  # kein Höhepunkt → ganzer Clip
    early = plan_cut(INFO, cfg, peak=1.5)
    assert early.segments[0].start == 0.0 and early.duration >= 10.0  # Höhepunkt am Anfang → genug Kontext
    short = plan_cut(MediaInfo(1920, 1080, 15.0, 60, True), cfg, peak=9.0)
    assert short.segments[0].start == 0.0 and short.segments[-1].end == 15.0   # kurze Clips bleiben ganz
    off = plan_cut(INFO, EditingConfig(smart_trim=False, punch_zoom=False), peak=20.0)
    assert off.segments == [Segment(0.0, 40.0)]
    long = plan_cut(MediaInfo(1920, 1080, 90.0, 60, True),
                    EditingConfig(trim_before_peak=80, trim_after_peak=5, trim_max_seconds=80, cold_open=True),
                    peak=85.0)
    assert long.duration <= EditingConfig().max_duration + 1e-6       # nie länger als max_duration


def test_captions_follow_the_cut():
    cut = CutPlan([Segment(19.4, 20.6), Segment(8.0, 25.0)], teaser=1.2, peak=20.0)
    chunks = [CaptionChunk(5.0, 6.0, [Word(5.0, 6.0, "WEG")]),
              CaptionChunk(10.0, 11.0, [Word(10.0, 10.5, "HALLO"), Word(10.5, 11.0, "CHAT")])]
    shifted = shift_captions(chunks, cut)
    assert len(shifted) == 1 and [w.text for w in shifted[0].words] == ["HALLO", "CHAT"]
    assert abs(shifted[0].start - 3.2) < 1e-6                        # 1,2 s Teaser + 2 s


def test_overlay_has_teaser_flash_and_progress_bar():
    cfg = EditingConfig(cold_open=True)
    plan = plan_blur(INFO, cfg)
    doc = build_overlay(plan, cfg, 18.2, "Hook-Text", "twitch.tv/x", [], teaser=1.2).render()
    assert "WARTE BIS ZUM ENDE" in doc
    hook = next(line for line in doc.splitlines() if line.startswith("Dialogue") and "Hook-Text" in line)
    assert hook.split(",")[1] == "0:00:01.20"                        # Hook erst nach dem Teaser
    assert "\\t(0,18200,\\clip(0,1910,1080,1920))" in doc               # Balken wächst über die ganze Länge
    assert "\\fad(0,170)" in doc                                     # Blitz beim Übergang


@needs_ffmpeg
def test_render_hook_first_end_to_end(tmp_path):
    src = tmp_path / "peak.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=40",
                    "-f", "lavfi", "-i", "aevalsrc='0.05*sin(2*PI*220*t)+if(between(t,30,31),0.8*sin(2*PI*440*t),0)'"
                                         ":s=48000:d=40",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(src)], check=True)
    peak = loudest_moment(src, 40.0)
    assert peak is not None and 29.8 <= peak <= 31.2
    cfg = EditingConfig(width=540, height=960, preset="ultrafast", crf=30, cold_open=True)
    out = render_vertical(src, tmp_path / "out.mp4", cfg, hook="Test", credit="twitch.tv/x",
                          captions=[CaptionChunk(25.0, 26.0, [Word(25.0, 26.0, "JA")])])
    info = probe(out)
    assert (info.width, info.height) == (540, 960) and info.has_audio
    assert 17.5 < info.duration < 18.8                               # 1,2 s Teaser + 12 s + 5 s statt 40 s


@needs_ffmpeg
def test_flat_audio_has_no_peak(sample_video):
    assert loudest_moment(sample_video, 2.0) is None                 # gleichmäßiger Ton → nichts kürzen
