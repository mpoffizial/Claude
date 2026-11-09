import json
from types import SimpleNamespace

from clipmaschine.config import MetadataConfig
from clipmaschine.metadata import (FALLBACK_BETA, MetadataContext, MetadataGenerator, merge_hashtags,
                                   template_metadata)

from .conftest import make_clip


class FakeMessages:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.behaviour(kwargs)
        if isinstance(result, Exception):
            raise result
        return result


def fake_client(behaviour):
    messages = FakeMessages(behaviour)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def text_response(data, stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason,
                           content=[SimpleNamespace(type="thinking", thinking=""),
                                    SimpleNamespace(type="text", text=json.dumps(data))])


def ctx(**kw):
    return MetadataContext(clip=make_clip("C1", title="Er trifft den unmöglichen Schuss 😳"), **kw)


def test_template_metadata_german():
    meta = template_metadata(ctx(extra_hashtags=["TeamA"]), MetadataConfig(use_claude=False))
    assert meta.source == "template"
    assert meta.hook == "Er trifft den unmöglichen Schuss"       # ohne Emoji
    assert meta.title.endswith("| Streamer_A")
    assert "twitch.tv/streamer_a" in meta.description
    assert meta.hashtags[:3] == ["streamer_a", "minecraft", "teama"]


def test_template_hype_hook_is_stable():
    cfg = MetadataConfig(use_claude=False)
    first = template_metadata(ctx(source="chat_hype"), cfg).hook
    assert first == template_metadata(ctx(source="chat_hype"), cfg).hook


def test_merge_hashtags_normalizes():
    assert merge_hashtags(["#Fortnite", "fortnite", "Twitch Clips", "🔥"], ["neu"], limit=3) == \
        ["fortnite", "twitchclips", "neu"]


def test_claude_success_and_request_shape():
    client, messages = fake_client(lambda kw: text_response({
        "hook": "Niemand hat 😱 damit gerechnet",
        "title": "Dieser Schuss ist absurd 😳",
        "description": "Streamer_A trifft das Unmögliche.",
        "hashtags": ["#Minecraft", "Clip Of The Day", "streamer_a"],
    }))
    gen = MetadataGenerator(MetadataConfig(use_claude=True, language="de"), client=client)
    meta = gen.generate(ctx(transcript="was geht ab", context={"chat_keywords": ["kekw"]}))
    assert meta.source == "claude"
    assert meta.hook == "Niemand hat damit gerechnet"
    assert meta.description.endswith("Original: twitch.tv/streamer_a")   # Credit ergänzt
    assert meta.hashtags[:3] == ["minecraft", "clipoftheday", "streamer_a"]

    call = messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["betas"] == [FALLBACK_BETA] and call["fallbacks"] == "default"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["effort"] == "low"
    user_text = call["messages"][0]["content"]
    assert "German" in user_text and "was geht ab" in user_text and "kekw" in user_text


def test_claude_refusal_and_bad_json_fall_back():
    client, _ = fake_client(lambda kw: SimpleNamespace(stop_reason="refusal", content=[]))
    gen = MetadataGenerator(MetadataConfig(use_claude=True), client=client)
    assert gen.generate(ctx()).source == "template"
    assert gen.uses_claude                                                # nur dieser Clip, nicht dauerhaft

    client, _ = fake_client(lambda kw: SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text="kein json")]))
    gen = MetadataGenerator(MetadataConfig(use_claude=True), client=client)
    assert gen.generate(ctx()).source == "template"


def test_missing_credentials_disable_claude():
    error = TypeError('"Could not resolve authentication method. Expected one of api_key, auth_token, ..."')
    client, messages = fake_client(lambda kw: error)
    gen = MetadataGenerator(MetadataConfig(use_claude="auto"), client=client)
    assert gen.generate(ctx()).source == "template"
    assert not gen.uses_claude
    gen.generate(ctx())
    assert len(messages.calls) == 1                                       # kein zweiter Versuch


def test_claude_off_never_calls():
    client, messages = fake_client(lambda kw: text_response({}))
    gen = MetadataGenerator(MetadataConfig(use_claude=False), client=client)
    assert gen.generate(ctx()).source == "template"
    assert messages.calls == []
