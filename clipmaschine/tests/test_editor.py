from clipmaschine.captions import Word, chunk_words
from clipmaschine.config import EditingConfig
from clipmaschine.editor import MediaInfo, plan_blur, plan_crop, plan_split, probe, render_vertical
from clipmaschine.overlay import AssDocument, ass_color, ass_escape, ass_time, karaoke_events, strip_emojis

from .conftest import make_video, needs_ffmpeg

WORDS = [Word(0.0, 0.3, "das"), Word(0.3, 0.6, "war"), Word(0.6, 0.9, "krass"), Word(0.9, 1.2, "oder?"),
         Word(2.0, 2.3, "nochmal"), Word(2.3, 2.6, "bitte")]


def test_ass_helpers():
    assert ass_color("#FFE600") == "&H0000E6FF"
    assert ass_color("#000000", 0x80) == "&H80000000"
    assert ass_time(65.432) == "0:01:05.43"
    assert ass_escape("a {b} \\c\nzweite") == "a (b) /c\\Nzweite"
    assert strip_emojis("Wow 😳🔥 krass!") == "Wow krass!"


def test_chunking_and_karaoke():
    chunks = chunk_words(WORDS, max_words=3)
    assert [c.text for c in chunks] == ["DAS WAR KRASS", "ODER?", "NOCHMAL BITTE"]
    assert chunks[0].end == chunks[1].start                     # Lücke geschlossen
    events = karaoke_events(chunks, "Caption", "#FFE600", "#FFFFFF")
    assert len(events) == 6                                     # ein Event pro Wort
    assert "\\c&H0000E6FF&" not in events[0].text               # ASS-Farbe ohne abschließendes &
    assert "{\\c&H0000E6FF}DAS{\\c&H00FFFFFF} WAR KRASS" in events[0].text
    assert "\\fscx85" in events[0].text and "\\fscx85" not in events[1].text
    doc = AssDocument(1080, 1920, events=events).render()
    assert "PlayResY: 1920" in doc and doc.count("Dialogue:") == 6


def test_layout_geometry():
    cfg = EditingConfig()
    info = MediaInfo(1920, 1080, 30, 60, True)
    blur = plan_blur(info, cfg)
    assert "scale=1080:608[fg]" in blur.filtergraph
    assert blur.hook.alignment == 2 and blur.captions.alignment == 8
    crop = plan_crop(info, cfg)
    assert "crop=608:1080:656:0" in crop.filtergraph
    split = plan_split(info, cfg, {"x": 0.75, "y": 0.0, "w": 0.25, "h": 0.25}, crop_x=0.5)
    assert "crop=480:270:1440:0" in split.filtergraph            # Facecam oben rechts
    assert "vstack" in split.filtergraph and split.hook.pos == (540, 672)
    vertical = plan_blur(MediaInfo(1080, 1920, 30, 30, True), cfg)
    assert vertical.hook.alignment == 8                          # kein Platz über dem Video


@needs_ffmpeg
def test_render_all_layouts(sample_video, tmp_path):
    cfg = EditingConfig(width=540, height=960, preset="ultrafast", crf=30)
    chunks = chunk_words(WORDS)
    for layout, facecam in (("blur", None), ("crop", None), ("split", {"x": 0, "y": 0, "w": 0.3, "h": 0.3})):
        out = render_vertical(sample_video, tmp_path / f"{layout}.mp4", cfg, layout=layout, facecam=facecam,
                              hook="Hook mit Emoji 😳 und {Klammern}", credit="twitch.tv/test", captions=chunks)
        info = probe(out)
        assert (info.width, info.height) == (540, 960)
        assert 1.8 < info.duration < 2.3 and info.has_audio and round(info.fps) == 30
    assert not list(tmp_path.glob(".work_*"))                    # Arbeitsordner aufgeräumt


@needs_ffmpeg
def test_render_vertical_source_without_audio(tmp_path):
    src = make_video(tmp_path / "vertical.mp4", size="720x1280", seconds=1.5, audio=False)
    cfg = EditingConfig(width=540, height=960, preset="ultrafast", crf=30, max_duration=1.0)
    out = render_vertical(src, tmp_path / "out.mp4", cfg, hook="Test", credit=None)
    info = probe(out)
    assert (info.width, info.height) == (540, 960) and not info.has_audio
    assert info.duration <= 1.1
