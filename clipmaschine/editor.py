"""Videoschnitt mit ffmpeg: 16:9-Stream → 9:16-Kurzvideo mit Hook, Untertiteln und Credit."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .captions import CaptionChunk
from .config import EditingConfig
from .overlay import AssDocument, AssEvent, AssStyle, ass_escape, karaoke_events, strip_emojis

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
                  credit: str | None, captions: list[CaptionChunk]) -> AssDocument | None:
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
        doc.events.append(AssEvent(0, duration, "Hook", f"{{{plan.hook.tags()}\\fad(200,0)}}{ass_escape(hook_text)}",
                                   layer=2))
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
    return doc if doc.events else None


def _filter_path(path: str) -> str:
    """Pfad für eine ffmpeg-Filteroption escapen."""
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def render_vertical(src: Path, out: Path, cfg: EditingConfig, *, layout: str | None = None,
                    facecam: dict | None = None, crop_x: float = 0.5, hook: str | None = None,
                    credit: str | None = None, captions: list[CaptionChunk] | None = None) -> Path:
    ffmpeg = _require("ffmpeg")
    src, out = Path(src).resolve(), Path(out).resolve()
    info = probe(src)
    duration = min(info.duration, cfg.max_duration) if info.duration > 0 else cfg.max_duration
    plan = make_plan(info, cfg, layout or cfg.layout, facecam, crop_x)

    workdir = out.parent / f".work_{out.stem}"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        video_chain = f"[base]fps={cfg.fps},setsar=1"
        doc = build_overlay(plan, cfg, duration, hook, credit, captions or [])
        if doc:
            (workdir / "overlay.ass").write_text(doc.render(), encoding="utf-8")
            # ffmpeg läuft im workdir → relativer Dateiname, kein Escaping-Ärger mit Pfaden
            video_chain += ",ass=overlay.ass"
            if cfg.fonts_dir:
                video_chain += f":fontsdir='{_filter_path(str(Path(cfg.fonts_dir).resolve()))}'"
        graph = f"{plan.filtergraph};{video_chain},format=yuv420p[vout]"
        audio_map = []
        if info.has_audio:
            if cfg.loudnorm:
                graph += f";[0:a]loudnorm=I={cfg.target_lufs:g}:TP=-1.5:LRA=11,aresample=48000[aout]"
                audio_map = ["-map", "[aout]"]
            else:
                audio_map = ["-map", "0:a:0"]

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
    log.info("Gerendert (%s): %s", plan.name, out.name)
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
