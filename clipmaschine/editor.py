"""Videoschnitt mit ffmpeg: 16:9-Stream → 9:16-Kurzvideo mit Hook, Untertiteln und Credit."""

from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .captions import CaptionChunk, Word
from .config import EditingConfig
from .overlay import AssDocument, AssEvent, AssStyle, ass_color, ass_escape, karaoke_events, strip_emojis

log = logging.getLogger(__name__)

RENDER_TIMEOUT_SECONDS = 1200


class RenderError(Exception):
    pass


@dataclass
class MediaInfo:
    width: int
    height: int
    duration: float
    fps: float
    has_audio: bool


@dataclass
class Placement:
    alignment: int
    margin_v: int = 0
    pos: tuple[int, int] | None = None   # absolute Position (Mittelpunkt bei alignment 5)

    def tags(self) -> str:
        if self.pos:
            return f"\\an{self.alignment}\\pos({self.pos[0]},{self.pos[1]})"
        return ""


@dataclass
class LayoutPlan:
    name: str
    filtergraph: str                     # endet mit dem Label [base]
    hook: Placement
    captions: Placement
    credit: Placement


def _require(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise RenderError(f"'{binary}' nicht gefunden – bitte ffmpeg installieren (https://ffmpeg.org/download.html)")
    return path


def _even(value: float) -> int:
    return max(2, int(round(value / 2.0)) * 2)


def _run(cmd: list[str], timeout: float, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """ffmpeg/ffprobe mit Zeitlimit – ein hängender Prozess darf die Maschine nicht blockieren."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              cwd=cwd, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RenderError(f"{Path(cmd[0]).name} hat nach {timeout:.0f}s nicht geantwortet – abgebrochen") from exc


_filter_cache: dict[str, bool] = {}


def has_filter(name: str) -> bool:
    """Ist ein ffmpeg-Filter verfügbar? (z. B. 'ass' braucht ffmpeg mit libass)"""
    if name not in _filter_cache:
        try:
            out = _run([_require("ffmpeg"), "-hide_banner", "-filters"], timeout=30).stdout
            _filter_cache[name] = any(line.split()[1:2] == [name] for line in out.splitlines())
        except RenderError:
            _filter_cache[name] = False
        if name == "ass" and not _filter_cache[name]:
            log.warning("ffmpeg ohne libass – Hook, Untertitel und Credit werden weggelassen. Abhilfe: ffmpeg mit "
                        "libass installieren (Mac: brew reinstall ffmpeg, Windows: winget install Gyan.FFmpeg)")
    return _filter_cache[name]


def probe(path: Path) -> MediaInfo:
    cmd = [_require("ffprobe"), "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)]
    proc = _run(cmd, timeout=60)
    if proc.returncode != 0:
        raise RenderError(f"ffprobe konnte {path} nicht lesen: {proc.stderr.strip()[-500:]}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        raise RenderError(f"{path} enthält keine Videospur")
    num, _, den = str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "30/1").partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    duration = float((data.get("format") or {}).get("duration") or video.get("duration") or 0)
    return MediaInfo(
        width=int(video["width"]),
        height=int(video["height"]),
        duration=duration,
        fps=fps or 30.0,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


def _center_crop(src_w: int, src_h: int, target_aspect: float, center_x: float = 0.5) -> tuple[int, int, int, int]:
    """Größter Ausschnitt mit Seitenverhältnis target_aspect (B/H), horizontal um center_x."""
    if src_w / src_h > target_aspect:
        ch = src_h - src_h % 2
        cw = min(src_w - src_w % 2, _even(ch * target_aspect))
    else:
        cw = src_w - src_w % 2
        ch = min(src_h - src_h % 2, _even(cw / target_aspect))
    cx = int(min(max(src_w * center_x - cw / 2, 0), src_w - cw))
    cy = (src_h - ch) // 2
    return cw, ch, cx, cy


def plan_blur(info: MediaInfo, cfg: EditingConfig) -> LayoutPlan:
    """Ganzes Bild in der Mitte, dahinter eine unscharfe, vergrößerte Kopie."""
    W, H = cfg.width, cfg.height
    scale = min(W / info.width, H / info.height)
    fg_w, fg_h = min(W, _even(info.width * scale)), min(H, _even(info.height * scale))
    top = (H - fg_h) // 2
    bottom = top + fg_h
    sw, sh = _even(W / 4), _even(H / 4)     # Unschärfe in kleiner Auflösung = schnell
    blur = max(1, min(cfg.blur_strength, min(sw, sh) // 2 - 1))
    graph = (
        "[0:v]split=2[bgsrc][fgsrc];"
        f"[bgsrc]scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},"
        f"boxblur={blur}:2,scale={W}:{H},eq=brightness=-0.08[bg];"
        f"[fgsrc]scale={fg_w}:{fg_h}[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"
    )
    if top >= int(H * 0.15):  # genug Platz über und unter dem Video
        hook = Placement(alignment=2, margin_v=H - top + int(H * 0.02))
        credit = Placement(alignment=8, margin_v=bottom + int(H * 0.01))
        captions = Placement(alignment=8, margin_v=bottom + int(H * 0.045))
    else:                     # Quelle ist schon (fast) hochkant
        hook = Placement(alignment=8, margin_v=int(H * 0.12))
        credit = Placement(alignment=8, margin_v=int(H * 0.08))
        captions = Placement(alignment=2, margin_v=int(H * 0.25))
    return LayoutPlan("blur", graph, hook, captions, credit)


def plan_crop(info: MediaInfo, cfg: EditingConfig, crop_x: float = 0.5) -> LayoutPlan:
    """Bildfüllender 9:16-Ausschnitt aus der Mitte (oder um crop_x)."""
    W, H = cfg.width, cfg.height
    cw, ch, cx, cy = _center_crop(info.width, info.height, W / H, crop_x)
    graph = f"[0:v]crop={cw}:{ch}:{cx}:{cy},scale={W}:{H}[base]"
    return LayoutPlan(
        "crop", graph,
        hook=Placement(alignment=8, margin_v=int(H * 0.14)),
        captions=Placement(alignment=2, margin_v=int(H * 0.24)),
        credit=Placement(alignment=8, margin_v=int(H * 0.09)),
    )


def plan_split(info: MediaInfo, cfg: EditingConfig, facecam: dict, crop_x: float = 0.5) -> LayoutPlan:
    """Facecam oben, Gameplay unten – das klassische Streamer-Kurzvideo."""
    W, H = cfg.width, cfg.height
    top_h = _even(H * cfg.split_top_ratio)
    bot_h = H - top_h
    fx, fy = (min(max(facecam[k], 0.0), 1.0) for k in ("x", "y"))
    fw, fh = (min(max(facecam[k], 0.02), 1.0) for k in ("w", "h"))
    cam_w = min(_even(info.width * fw), info.width - info.width % 2)
    cam_h = min(_even(info.height * fh), info.height - info.height % 2)
    cam_x = int(min(max(info.width * fx, 0), info.width - cam_w))
    cam_y = int(min(max(info.height * fy, 0), info.height - cam_h))
    gw, gh, gx, gy = _center_crop(info.width, info.height, W / bot_h, crop_x)
    graph = (
        "[0:v]split=2[camsrc][gamesrc];"
        f"[camsrc]crop={cam_w}:{cam_h}:{cam_x}:{cam_y},"
        f"scale={W}:{top_h}:force_original_aspect_ratio=increase,crop={W}:{top_h}[cam];"
        f"[gamesrc]crop={gw}:{gh}:{gx}:{gy},scale={W}:{bot_h}[game];"
        "[cam][game]vstack=inputs=2[base]"
    )
    return LayoutPlan(
        "split", graph,
        hook=Placement(alignment=5, pos=(W // 2, top_h)),         # genau auf der Trennlinie
        captions=Placement(alignment=2, margin_v=int(H * 0.22)),
        credit=Placement(alignment=8, margin_v=int(H * 0.08)),
    )


def make_plan(info: MediaInfo, cfg: EditingConfig, layout: str, facecam: dict | None, crop_x: float) -> LayoutPlan:
    if layout == "split":
        if facecam:
            return plan_split(info, cfg, facecam, crop_x)
        log.warning("Layout 'split' ohne facecam-Angabe – verwende 'blur'")
        layout = "blur"
    if layout == "crop":
        return plan_crop(info, cfg, crop_x)
    return plan_blur(info, cfg)


def build_overlay(plan: LayoutPlan, cfg: EditingConfig, duration: float, hook: str | None,
                  credit: str | None, captions: list[CaptionChunk], teaser: float = 0.0) -> AssDocument | None:
    W, H = cfg.width, cfg.height
    k = W / 1080.0
    doc = AssDocument(W, H)
    hook_text = strip_emojis(hook or "")
    if cfg.hook_uppercase:
        hook_text = hook_text.upper()
    if hook_text:
        doc.styles.append(AssStyle("Hook", cfg.font, int(74 * k), color=cfg.hook_color, outline=7 * k,
                                   shadow=3 * k, alignment=plan.hook.alignment, margin_v=plan.hook.margin_v,
                                   margin_l=int(70 * k), margin_r=int(70 * k)))
        doc.events.append(AssEvent(teaser, duration, "Hook",
                                   f"{{{plan.hook.tags()}\\fad(200,0)}}{ass_escape(hook_text)}", layer=2))
    credit_text = strip_emojis(credit or "")
    if credit_text:
        doc.styles.append(AssStyle("Credit", cfg.font, int(38 * k), alpha=0x30, outline=3 * k, shadow=1 * k,
                                   alignment=plan.credit.alignment, margin_v=plan.credit.margin_v))
        doc.events.append(AssEvent(0, duration, "Credit", f"{{{plan.credit.tags()}}}{ass_escape(credit_text)}"))
    chunks = [c for c in captions if c.start < duration]
    if chunks:
        doc.styles.append(AssStyle("Caption", cfg.font, int(92 * k), color=cfg.caption_color, outline=8 * k,
                                   shadow=3 * k, alignment=plan.captions.alignment, margin_v=plan.captions.margin_v,
                                   margin_l=int(90 * k), margin_r=int(90 * k)))
        events = karaoke_events(chunks, "Caption", cfg.caption_highlight_color, cfg.caption_color)
        for event in events:
            event.end = min(event.end, duration)
        doc.events.extend(e for e in events if e.end > e.start)
    if teaser > 0:
        _teaser_events(doc, plan, cfg, teaser)
    if cfg.progress_bar and duration > 0:
        bar = max(8, int(10 * k))
        color = ass_color(cfg.progress_bar_color)
        if not any(s.name == "Fx" for s in doc.styles):
            doc.styles.append(AssStyle("Fx", cfg.font, 20, outline=0, shadow=0, alignment=7, margin_v=0))
        doc.events.append(AssEvent(
            0, duration, "Fx",
            f"{{\\an7\\pos(0,{H - bar})\\p1\\1c{color}\\bord0\\shad0\\clip(0,{H - bar},0,{H})"
            f"\\t(0,{int(duration * 1000)},\\clip(0,{H - bar},{W},{H}))}}m 0 0 l {W} 0 {W} {bar} 0 {bar}{{\\p0}}",
            layer=3))
    return doc if doc.events else None


def _teaser_events(doc: AssDocument, plan: LayoutPlan, cfg: EditingConfig, teaser: float) -> None:
    """Teaser-Text am Anfang und ein kurzer weißer Blitz beim Übergang in den Hauptteil."""
    W, H = cfg.width, cfg.height
    k = W / 1080.0
    if not any(s.name == "Fx" for s in doc.styles):
        doc.styles.append(AssStyle("Fx", cfg.font, 20, outline=0, shadow=0, alignment=7, margin_v=0))
    text = strip_emojis(cfg.cold_open_text or "").upper()
    if text:
        doc.styles.append(AssStyle("Teaser", cfg.font, int(84 * k), color=cfg.caption_highlight_color,
                                   outline=8 * k, shadow=3 * k, alignment=plan.hook.alignment,
                                   margin_v=plan.hook.margin_v, margin_l=int(60 * k), margin_r=int(60 * k)))
        pop = "\\fscx80\\fscy80\\t(0,120,\\fscx100\\fscy100)"
        doc.events.append(AssEvent(0, teaser, "Teaser", f"{{{plan.hook.tags()}{pop}}}{ass_escape(text)}", layer=4))
    flash = (f"{{\\an7\\pos(0,0)\\p1\\1c&H00FFFFFF&\\bord0\\shad0\\fad(0,170)}}"
             f"m 0 0 l {W} 0 {W} {H} 0 {H}{{\\p0}}")
    doc.events.append(AssEvent(max(teaser - 0.03, 0), teaser + 0.2, "Fx", flash, layer=5))


# ------------------------------------------------------------------ Hook-First-Schnitt
MIN_CLIP_SECONDS = 10.0
PEAK_WINDOW = 0.1             # Messfenster der Lautstärke in Sekunden
PEAK_SMOOTH = 5               # 5 Fenster = 0,5 s glätten
PEAK_MIN_CONTRAST_DB = 4.0    # so viel lauter als der Median muss ein echter Höhepunkt sein


@dataclass
class Segment:
    start: float
    end: float
    zoom: float = 1.0


@dataclass
class CutPlan:
    segments: list[Segment]
    teaser: float = 0.0           # Länge des Teasers am Anfang (0 = keiner)
    peak: float | None = None     # Quellzeit des Höhepunkts

    @property
    def duration(self) -> float:
        return sum(s.end - s.start for s in self.segments)

    @property
    def main_start(self) -> float:
        return self.segments[1 if self.teaser else 0].start

    @property
    def main_end(self) -> float:
        return self.segments[-1].end

    def to_output(self, t: float) -> float | None:
        """Quellzeit → Zeit im fertigen Video (Hauptteil, ohne Teaser)."""
        if t < self.main_start - 1e-6 or t > self.main_end + 1e-6:
            return None
        return self.teaser + (min(t, self.main_end) - self.main_start)


def loudest_moment(path: Path, duration: float) -> float | None:
    """Zeitpunkt des deutlich lautesten Moments (Reaktion, Explosion) – oder None, wenn es keinen gibt."""
    af = (f"aresample=16000,asetnsamples=n={int(16000 * PEAK_WINDOW)}:p=0,astats=metadata=1:reset=1,"
          "ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file=-")
    proc = _run([_require("ffmpeg"), "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", af,
                 "-f", "null", "-"], timeout=120)
    times, levels, current = [], [], None
    for line in proc.stdout.splitlines():
        if "pts_time:" in line:
            current = float(line.rsplit("pts_time:", 1)[1].split()[0])
        elif "RMS_level=" in line and current is not None:
            value = line.split("=", 1)[1].strip()
            times.append(current)
            levels.append(-120.0 if value in ("-inf", "inf", "nan") else max(float(value), -120.0))
    if len(levels) < PEAK_SMOOTH * 3:
        return None
    energy = [10 ** (db / 10) for db in levels]
    half = PEAK_SMOOTH // 2
    smooth = [sum(energy[max(0, i - half):i + half + 1]) / len(energy[max(0, i - half):i + half + 1])
              for i in range(len(energy))]
    candidates = [i for i, t in enumerate(times) if 1.0 <= t <= max(duration - 0.8, 1.0)]
    if not candidates:
        return None
    best = max(candidates, key=lambda i: smooth[i])
    median = sorted(smooth)[len(smooth) // 2]
    if median > 0 and 10 * math.log10(smooth[best] / median) < PEAK_MIN_CONTRAST_DB:
        return None                                  # gleichmäßig laut → kein klarer Höhepunkt
    return times[best] + PEAK_WINDOW / 2


def plan_cut(info: MediaInfo, cfg: EditingConfig, peak: float | None) -> CutPlan:
    total = info.duration if info.duration > 0 else cfg.max_duration
    start, end = 0.0, min(total, cfg.max_duration)
    if cfg.smart_trim and peak is not None and total > cfg.trim_max_seconds:
        start = max(0.0, peak - cfg.trim_before_peak)
        end = min(total, peak + cfg.trim_after_peak)
        if end - start > cfg.trim_max_seconds:
            start = end - cfg.trim_max_seconds
        if end - start < MIN_CLIP_SECONDS:           # Höhepunkt ganz am Anfang → etwas mehr Kontext
            end = min(total, start + MIN_CLIP_SECONDS)
        end = min(end, start + cfg.max_duration)
    segments = [Segment(start, end)]
    if cfg.punch_zoom and peak is not None and start + 0.6 < peak < end - 0.8:
        a, b = peak - 0.25, peak + 0.55
        segments = [Segment(start, a), Segment(a, b, cfg.punch_zoom_factor), Segment(b, end)]
    teaser = 0.0
    if cfg.cold_open and peak is not None:
        a = max(0.0, peak - cfg.cold_open_seconds / 2)
        b = min(total, a + cfg.cold_open_seconds)
        if b - a >= 0.5:
            segments.insert(0, Segment(a, b, 1.15))
            teaser = b - a
    budget = cfg.max_duration                        # Gesamtlänge nie über max_duration
    while sum(s.end - s.start for s in segments) > budget + 1e-6:
        over = sum(s.end - s.start for s in segments) - budget
        segments[-1].end -= min(over, segments[-1].end - segments[-1].start - 0.5)
    return CutPlan(segments, teaser, peak)


def segment_graph(cut: CutPlan, info: MediaInfo) -> str:
    """Schneidet die Segmente aus der Quelle (mit Zoom) und fügt sie zu [vsrc]/[asrc] zusammen."""
    w0, h0 = info.width - info.width % 2, info.height - info.height % 2
    parts, labels = [], []
    for i, seg in enumerate(cut.segments):
        video = f"[0:v]trim=start={seg.start:.3f}:end={seg.end:.3f},setpts=PTS-STARTPTS"
        if seg.zoom > 1.0:
            video += f",scale={_even(w0 * seg.zoom)}:{_even(h0 * seg.zoom)},crop={w0}:{h0}"
        else:
            video += f",scale={w0}:{h0}"
        parts.append(video + f",setsar=1[sv{i}]")
        labels.append(f"[sv{i}]")
        if info.has_audio:
            parts.append(f"[0:a]atrim=start={seg.start:.3f}:end={seg.end:.3f},asetpts=PTS-STARTPTS[sa{i}]")
            labels.append(f"[sa{i}]")
    outputs = "[vsrc][asrc]" if info.has_audio else "[vsrc]"
    parts.append(f"{''.join(labels)}concat=n={len(cut.segments)}:v=1:a={1 if info.has_audio else 0}{outputs}")
    return ";".join(parts)


def shift_captions(chunks: list[CaptionChunk], cut: CutPlan) -> list[CaptionChunk]:
    """Untertitel auf die Zeitachse des geschnittenen Videos legen (nur Hauptteil)."""
    shifted = []
    for chunk in chunks:
        words = []
        for word in chunk.words:
            a, b = cut.to_output(word.start), cut.to_output(word.end)
            if a is not None and b is not None and b > a:
                words.append(Word(a, b, word.text))
        if words:
            end = cut.to_output(chunk.end)
            shifted.append(CaptionChunk(words[0].start, max(end or words[-1].end, words[-1].end), words))
    return shifted


def _filter_path(path: str) -> str:
    """Pfad für eine ffmpeg-Filteroption escapen."""
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def render_vertical(src: Path, out: Path, cfg: EditingConfig, *, layout: str | None = None,
                    facecam: dict | None = None, crop_x: float = 0.5, hook: str | None = None,
                    credit: str | None = None, captions: list[CaptionChunk] | None = None) -> Path:
    ffmpeg = _require("ffmpeg")
    src, out = Path(src).resolve(), Path(out).resolve()
    info = probe(src)
    peak = None
    if info.has_audio and (cfg.smart_trim or cfg.punch_zoom or cfg.cold_open):
        try:
            peak = loudest_moment(src, info.duration)
        except RenderError as exc:
            log.debug("Höhepunkt nicht ermittelbar: %s", exc)
    cut = plan_cut(info, cfg, peak)
    duration = cut.duration
    captions = shift_captions(captions or [], cut)
    plan = make_plan(info, cfg, layout or cfg.layout, facecam, crop_x)

    workdir = out.parent / f".work_{out.stem}"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        video_chain = f"[base]fps={cfg.fps},setsar=1"
        doc = build_overlay(plan, cfg, duration, hook, credit, captions, teaser=cut.teaser)
        if doc and not has_filter("ass"):
            doc = None                     # lieber ohne Texte als gar kein Video
        if doc:
            (workdir / "overlay.ass").write_text(doc.render(), encoding="utf-8")
            # ffmpeg läuft im workdir → relativer Dateiname, kein Escaping-Ärger mit Pfaden
            video_chain += ",ass=overlay.ass"
            if cfg.fonts_dir:
                video_chain += f":fontsdir='{_filter_path(str(Path(cfg.fonts_dir).resolve()))}'"
        layout_graph = plan.filtergraph.replace("[0:v]", "[vsrc]")
        graph = f"{segment_graph(cut, info)};{layout_graph};{video_chain},format=yuv420p[vout]"
        audio_map = []
        if info.has_audio:
            if cfg.loudnorm:
                graph += f";[asrc]loudnorm=I={cfg.target_lufs:g}:TP=-1.5:LRA=11,aresample=48000[aout]"
                audio_map = ["-map", "[aout]"]
            else:
                audio_map = ["-map", "[asrc]"]

        out.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = workdir / f"render{out.suffix or '.mp4'}"
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
            "-filter_complex", graph, "-map", "[vout]", *audio_map,
            "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf),
            "-maxrate", cfg.video_maxrate, "-bufsize", _double_rate(cfg.video_maxrate),
            "-profile:v", "high", "-pix_fmt", "yuv420p",
        ]
        if info.has_audio:
            cmd += ["-c:a", "aac", "-b:a", cfg.audio_bitrate, "-ar", "48000", "-ac", "2"]
        cmd += ["-movflags", "+faststart", str(tmp_out)]
        log.debug("ffmpeg: %s", " ".join(cmd))
        proc = _run(cmd, timeout=RENDER_TIMEOUT_SECONDS, cwd=workdir)
        if proc.returncode != 0:
            raise RenderError(f"ffmpeg-Fehler ({plan.name}): {proc.stderr.strip()[-1500:]}")
        result = probe(tmp_out)
        if (result.width, result.height) != (cfg.width, cfg.height) or result.duration <= 0:
            raise RenderError(f"Unerwartetes Ergebnis: {result.width}x{result.height}, {result.duration:.1f}s")
        tmp_out.replace(out)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    details = [plan.name, f"{duration:.0f}s"]
    if peak is not None:
        details.append(f"Höhepunkt bei {peak:.1f}s")
    if cut.teaser:
        details.append("Teaser")
    log.info("Gerendert (%s): %s", ", ".join(details), out.name)
    return out


def _double_rate(rate: str) -> str:
    rate = rate.strip()
    unit = rate[-1] if rate[-1:].isalpha() else ""
    number = float(rate[:-1] if unit else rate)
    return f"{number * 2:g}{unit}"


def extract_frame(src: Path, out_png: Path, at_seconds: float = 3.0, grid: bool = True) -> Path:
    """Standbild mit 10-%-Raster – hilft beim Ausmessen der Facecam-Position für das split-Layout."""
    ffmpeg = _require("ffmpeg")
    info = probe(src)
    at = min(max(at_seconds, 0), max(info.duration - 0.1, 0))
    vf = "drawgrid=w=iw/10:h=ih/10:t=2:c=red@0.8" if grid else "null"
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(src),
           "-frames:v", "1", "-vf", vf, str(out_png)]
    proc = _run(cmd, timeout=120)
    if proc.returncode != 0:
        raise RenderError(f"Standbild fehlgeschlagen: {proc.stderr.strip()[-500:]}")
    return out_png
