"""Offline tests for yt_condense.py.

yt-dlp, faster-whisper, claude, and HTTP are replaced with fakes. Nothing here
talks to YouTube, Anthropic, Ollama, or a clipboard.
"""

from __future__ import annotations

import atexit
import builtins
import io
import json
import os
import shutil
import subprocess
import sys
import types
import urllib.error
from pathlib import Path

# Pytest writes bytecode next to these files, and that bytecode stores the
# absolute path of the checkout. Remove it so a secret scan of the tree stays empty.
sys.dont_write_bytecode = True


def _remove_bytecode() -> None:
    root = Path(__file__).resolve().parents[1]
    for cache in list(root.rglob("__pycache__")):
        shutil.rmtree(cache, ignore_errors=True)


_remove_bytecode()
atexit.register(_remove_bytecode)

import pytest
import yt_dlp
from yt_dlp.utils import DownloadError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yt_condense

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "rolling-captions.vtt"
VIDEO_URL = "https://www.youtube.com/watch?v=vid12345678"
SIMPLE_VTT = """WEBVTT

00:00:00.000 --> 00:00:02.000
hello from the captions
"""
TIMELINE_VTT = """WEBVTT

00:00:10.000 --> 00:00:11.000
early words

00:12:30.000 --> 00:12:31.000
boundary words

00:13:20.000 --> 00:13:21.000
later words

01:01:40.000 --> 01:01:41.000
just before the hour mark

01:03:20.000 --> 01:03:21.000
hour mark words
"""


class FakeYDL:
    caption_vtt: str | None = None
    caption_error: BaseException | None = None
    audio_error: BaseException | None = None
    calls: list[dict] = []

    def __init__(self, params: dict):
        self.params = params
        FakeYDL.calls.append(params)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, url, download=True):
        if self.params.get("skip_download"):
            if FakeYDL.caption_error:
                raise FakeYDL.caption_error
            info = {
                "id": "vid12345678",
                "title": "Fixture Title",
                "webpage_url": url,
                "requested_subtitles": None,
            }
            if FakeYDL.caption_vtt:
                folder = Path(str(self.params["outtmpl"])).parent
                path = folder / "vid12345678.en.vtt"
                path.write_text(FakeYDL.caption_vtt, encoding="utf-8")
                info["requested_subtitles"] = {
                    "en": {"ext": "vtt", "filepath": str(path)}
                }
            return info
        if FakeYDL.audio_error:
            raise FakeYDL.audio_error
        outtmpl = str(self.params["outtmpl"])
        filepath = outtmpl.replace("%(id)s", "vid12345678").replace("%(ext)s", "m4a")
        Path(filepath).write_bytes(b"fake-audio")
        return {
            "id": "vid12345678",
            "title": "Fixture Title",
            "webpage_url": url,
            "filepath": filepath,
            "requested_downloads": [{"filepath": filepath}],
        }


class FakeWhisperModel:
    calls: list[tuple] = []
    segments: list = []

    def __init__(self, model, device="auto", compute_type="int8"):
        if device != "auto" or compute_type != "int8":
            raise AssertionError(f"WhisperModel called with {device!r} {compute_type!r}")
        FakeWhisperModel.calls.append((model, device, compute_type))

    def transcribe(self, audio, language=None):
        info = types.SimpleNamespace(duration=19.0)
        return iter(FakeWhisperModel.segments), info


def install_sources(monkeypatch, caption_vtt=None, caption_error=None, audio_error=None, segments=None):
    FakeYDL.caption_vtt = caption_vtt
    FakeYDL.caption_error = caption_error
    FakeYDL.audio_error = audio_error
    FakeYDL.calls = []
    FakeWhisperModel.calls = []
    FakeWhisperModel.segments = segments or [
        types.SimpleNamespace(start=0.0, end=9.0, text="whisper hello"),
        types.SimpleNamespace(start=9.0, end=19.0, text="whisper world"),
    ]
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    module = types.ModuleType("faster_whisper")
    module.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", module)


def http_response(payload: dict, status: int = 200):
    body = json.dumps(payload).encode("utf-8")

    class Response:
        def __init__(self):
            self.status = status

        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    return Response()


def test_vtt_parsing_drops_tags_settings_and_rolling_duplicates():
    """source: VTT parsing drops cue settings, styling tags, timestamp tags, and duplicate rolling lines"""
    segments = yt_condense.parse_vtt(FIXTURE.read_text(encoding="utf-8"))
    texts = [text for _, text in segments]
    joined = "\n".join(texts)
    assert [round(start, 3) for start, _ in segments] == [0.0, 2.0, 4.5, 8.0, 10.0, 10.4, 12.5]
    assert texts == [
        "hello everyone welcome",
        "this is the real point",
        "and here is a fact worth keeping",
        "the duplicate line should be dropped",
        "and then more words arrive",
        "in the AT&T window",
        "a wrapped line that stays together as one sentence",
    ]
    assert "align:start" not in joined
    assert "position:" not in joined
    assert "<c" not in joined
    assert "00:00:01.000" not in joined
    assert "colorE5E5E5" not in joined
    assert joined.count("hello everyone welcome") == 1
    assert joined.count("and here is a fact worth keeping") == 1
    assert "&amp;" not in joined


def test_from_formats_and_url_t_drop_earlier_segments(monkeypatch, tmp_path, capsys):
    """source: --from accepts 750, 12:30, and 1:02:30, and a t= in the URL drops earlier segments"""
    install_sources(monkeypatch, caption_vtt=TIMELINE_VTT)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    early = tmp_path / "seconds"
    code = yt_condense.main(
        [VIDEO_URL, "--from", "750", "--provider", "none", "--out", str(early)]
    )
    assert code == 0
    seconds_text = (early / "transcript.txt").read_text(encoding="utf-8")
    assert "[12:30] boundary words" in seconds_text
    assert "[13:20] later words" in seconds_text
    assert "early words" not in seconds_text
    assert "FROM TIMESTAMP: 750 seconds" in (early / "paste-into-chat.txt").read_text(encoding="utf-8")

    clock = tmp_path / "clock"
    code = yt_condense.main(
        [VIDEO_URL, "--from", "12:30", "--provider", "none", "--out", str(clock)]
    )
    assert code == 0
    assert (clock / "transcript.txt").read_text(encoding="utf-8") == seconds_text

    hour = tmp_path / "hour"
    code = yt_condense.main(
        [VIDEO_URL, "--from", "1:02:30", "--provider", "none", "--out", str(hour)]
    )
    assert code == 0
    hour_text = (hour / "transcript.txt").read_text(encoding="utf-8")
    assert hour_text == "[63:20] hour mark words\n"
    assert "just before the hour mark" not in hour_text
    assert "FROM TIMESTAMP: 3750 seconds" in (hour / "paste-into-chat.txt").read_text(encoding="utf-8")

    flagged = tmp_path / "flag-wins"
    code = yt_condense.main(
        [
            VIDEO_URL + "&t=0",
            "--from",
            "12:30",
            "--provider",
            "none",
            "--out",
            str(flagged),
        ]
    )
    assert code == 0
    assert "early words" not in (flagged / "transcript.txt").read_text(encoding="utf-8")

    from_url = tmp_path / "url-t"
    code = yt_condense.main(
        [VIDEO_URL + "&t=12m30s", "--provider", "none", "--out", str(from_url)]
    )
    assert code == 0
    url_text = (from_url / "transcript.txt").read_text(encoding="utf-8")
    assert url_text == seconds_text
    assert "early words" not in url_text
    capsys.readouterr()


def test_captions_are_used_and_audio_is_not_downloaded(monkeypatch, tmp_path):
    """source: a video with captions uses them and never downloads audio"""
    install_sources(monkeypatch, caption_vtt=SIMPLE_VTT)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    code = yt_condense.main([VIDEO_URL, "--provider", "none"])
    assert code == 0
    assert len(FakeYDL.calls) == 1
    options = FakeYDL.calls[0]
    assert options["skip_download"] is True
    assert options["writesubtitles"] is True
    assert options["writeautomaticsub"] is True
    assert options["subtitlesformat"] == "vtt"
    assert options["subtitleslangs"] == ["en"]
    assert FakeWhisperModel.calls == []
    transcript = (tmp_path / "out" / "vid12345678" / "transcript.txt").read_text(encoding="utf-8")
    assert transcript == "[00:00] hello from the captions\n"
    assert "whisper hello" not in transcript


def test_no_captions_downloads_audio_and_transcribes(monkeypatch, tmp_path, capsys):
    """source: Door 26, 11-12: the copy said the video needs captions"""
    install_sources(monkeypatch, caption_vtt=None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = tmp_path / "heard"
    code = yt_condense.main([VIDEO_URL, "--provider", "none", "--out", str(out)])
    assert code == 0
    assert FakeYDL.calls[0]["skip_download"] is True
    assert FakeYDL.calls[0]["writesubtitles"] is True
    assert "bestaudio" in FakeYDL.calls[1]["format"]
    assert FakeYDL.calls[1].get("skip_download") is not True
    assert FakeWhisperModel.calls == [("small", "auto", "int8")]
    transcript = (out / "transcript.txt").read_text(encoding="utf-8")
    assert "whisper hello" in transcript
    assert "whisper world" in transcript
    captured = capsys.readouterr()
    assert "0.1 of 0.3 minutes" in captured.out
    assert "0.3 of 0.3 minutes" in captured.out


def test_transcribe_flag_skips_captions(monkeypatch, tmp_path):
    """source: --transcribe forces transcription even when captions exist"""
    install_sources(monkeypatch, caption_vtt=SIMPLE_VTT)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = tmp_path / "forced"
    code = yt_condense.main(
        [
            VIDEO_URL,
            "--transcribe",
            "--whisper-model",
            "tiny",
            "--provider",
            "none",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    assert len(FakeYDL.calls) == 1
    assert "bestaudio" in FakeYDL.calls[0]["format"]
    assert FakeYDL.calls[0].get("writesubtitles") is not True
    assert FakeWhisperModel.calls == [("tiny", "auto", "int8")]
    transcript = (out / "transcript.txt").read_text(encoding="utf-8")
    assert "whisper hello" in transcript
    assert "hello from the captions" not in transcript


def test_chunking_splits_on_segment_boundaries_and_keeps_order(monkeypatch, tmp_path):
    """source: a transcript over 60000 characters splits only on segment boundaries and stays in order"""
    text_one = "STARTONE " + ("alpha " * 7000) + "ENDONE"
    text_two = "STARTTWO " + ("beta " * 7000) + "ENDTWO"
    text_three = "ENDTHREE tail"
    vtt = (
        "WEBVTT\n\n"
        f"00:00:00.000 --> 00:00:01.000\n{text_one}\n\n"
        f"00:00:10.000 --> 00:00:11.000\n{text_two}\n\n"
        f"00:00:20.000 --> 00:00:21.000\n{text_three}\n"
    )
    install_sources(monkeypatch, caption_vtt=vtt)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    prompts: list[str] = []

    def fake_urlopen(request, timeout=None):
        sent = json.loads(request.data.decode("utf-8"))
        prompt = sent["messages"][0]["content"]
        prompts.append(prompt)
        if "STARTONE" in prompt:
            text = "FIRST CHUNK"
        elif "STARTTWO" in prompt:
            text = "SECOND CHUNK"
        else:
            text = "OTHER CHUNK"
        return http_response({"content": [{"type": "text", "text": text}]})

    monkeypatch.setattr(yt_condense.urllib.request, "urlopen", fake_urlopen)
    out = tmp_path / "chunks"
    code = yt_condense.main(
        [VIDEO_URL, "--provider", "auto", "--out", str(out)]
    )
    assert code == 0
    assert len(prompts) == 2
    assert text_one in prompts[0]
    assert "ENDONE" in prompts[0]
    assert "STARTTWO" not in prompts[0]
    assert "ENDTHREE" not in prompts[0]
    assert text_two in prompts[1]
    assert "ENDTWO" in prompts[1]
    assert text_three in prompts[1]
    assert "STARTONE" not in prompts[1]
    assert (out / "condensed.txt").read_text(encoding="utf-8") == "FIRST CHUNK\n\nSECOND CHUNK\n"
    paste = (out / "paste-into-chat.txt").read_text(encoding="utf-8")
    assert text_one in paste
    assert text_two in paste
    assert text_three in paste


def test_auto_picks_anthropic_before_ollama(monkeypatch, tmp_path):
    """source: provider auto picks anthropic when ANTHROPIC_API_KEY is set, before Ollama"""
    install_sources(monkeypatch, caption_vtt=SIMPLE_VTT)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seen: list[str] = []

    def fake_urlopen(request, timeout=None):
        seen.append(request.full_url)
        if "11434" in request.full_url:
            return http_response({"models": [{"name": "should-not-run"}]})
        sent = json.loads(request.data.decode("utf-8"))
        assert sent["model"] == "claude-sonnet-5-5"
        assert sent["max_tokens"] == 4000
        assert sent["messages"][0]["role"] == "user"
        assert request.get_header("X-api-key") == "test-key"
        assert request.get_header("Anthropic-version") == "2023-06-01"
        assert request.get_header("Content-type") == "application/json"
        return http_response({"content": [{"type": "text", "text": "Short spoken version."}]})

    monkeypatch.setattr(yt_condense.urllib.request, "urlopen", fake_urlopen)
    out = tmp_path / "anthropic"
    code = yt_condense.main([VIDEO_URL, "--provider", "auto", "--out", str(out)])
    assert code == 0
    assert seen == ["https://api.anthropic.com/v1/messages"]
    assert (out / "condensed.txt").read_text(encoding="utf-8") == "Short spoken version.\n"


def test_auto_picks_ollama_when_no_anthropic_key(monkeypatch, tmp_path, capsys):
    """source: provider auto picks ollama when no Anthropic key is set and local tags answer within two seconds"""
    install_sources(monkeypatch, caption_vtt=SIMPLE_VTT)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    seen: list[tuple[str, float | None]] = []

    def fake_urlopen(request, timeout=None):
        seen.append((request.full_url, timeout))
        if request.full_url.endswith("/api/tags"):
            assert timeout == 2
            return http_response(
                {"models": [{"name": "llama3.2:latest"}, {"name": "mistral:latest"}]}
            )
        sent = json.loads(request.data.decode("utf-8"))
        assert sent["model"] == "llama3.2:latest"
        assert sent["stream"] is False
        assert "api.anthropic.com" not in request.full_url
        return http_response({"message": {"role": "assistant", "content": "Local short version."}})

    monkeypatch.setattr(yt_condense.urllib.request, "urlopen", fake_urlopen)
    out = tmp_path / "ollama"
    code = yt_condense.main([VIDEO_URL, "--provider", "auto", "--out", str(out)])
    assert code == 0
    assert seen[0][0] == "http://localhost:11434/api/tags"
    assert seen[1][0] == "http://localhost:11434/api/chat"
    assert "api.anthropic.com" not in [url for url, _ in seen]
    assert (out / "condensed.txt").read_text(encoding="utf-8") == "Local short version.\n"
    assert "Using Ollama model llama3.2:latest" in capsys.readouterr().out


def test_auto_picks_none_when_no_key_and_ollama_is_down(monkeypatch, tmp_path):
    """source: provider auto picks none when no Anthropic key is set and Ollama does not answer"""
    install_sources(monkeypatch, caption_vtt=SIMPLE_VTT)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(yt_condense.urllib.request, "urlopen", fake_urlopen)
    out = tmp_path / "none-auto"
    code = yt_condense.main([VIDEO_URL, "--provider", "auto", "--out", str(out)])
    assert code == 0
    assert (out / "transcript.txt").is_file()
    assert (out / "paste-into-chat.txt").is_file()
    assert not (out / "condensed.txt").exists()


def test_none_provider_writes_prompt_with_full_transcript(monkeypatch, tmp_path, capsys):
    """source: the none provider writes paste-into-chat.txt holding the prompt and the full transcript"""
    install_sources(monkeypatch, caption_vtt=SIMPLE_VTT)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-be-used")
    out = tmp_path / "paste"
    code = yt_condense.main([VIDEO_URL, "--provider", "none", "--out", str(out)])
    assert code == 0
    paste = (out / "paste-into-chat.txt").read_text(encoding="utf-8")
    assert paste.startswith("Transform this YouTube transcript into a condensed audio summary.")
    assert "Target ~20% of original length" in paste
    assert "NO bullets, NO lists, NO markdown" in paste
    assert "VIDEO: Fixture Title (https://www.youtube.com/watch?v=vid12345678)" in paste
    assert "FROM TIMESTAMP: 0 seconds" in paste
    assert "hello from the captions" in paste
    assert (out / "transcript.txt").read_text(encoding="utf-8") == "[00:00] hello from the captions\n"
    assert not (out / "condensed.txt").exists()
    captured = capsys.readouterr().out
    assert "Paste this file into any chat model" in captured
    assert "Words before:" in captured
    assert "Words after: no model ran" in captured


def test_provider_failure_exits_4_and_still_writes_transcript(monkeypatch, tmp_path, capsys):
    """source: a provider failure exits 4 and still writes the transcript and paste file"""
    install_sources(monkeypatch, caption_vtt=SIMPLE_VTT)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url,
            500,
            "server error",
            hdrs=None,
            fp=io.BytesIO(b"server error"),
        )

    monkeypatch.setattr(yt_condense.urllib.request, "urlopen", fake_urlopen)
    out = tmp_path / "failed"
    code = yt_condense.main([VIDEO_URL, "--provider", "anthropic", "--out", str(out)])
    assert code == 4
    assert (out / "transcript.txt").read_text(encoding="utf-8") == "[00:00] hello from the captions\n"
    paste = (out / "paste-into-chat.txt").read_text(encoding="utf-8")
    assert "hello from the captions" in paste
    assert not (out / "condensed.txt").exists()
    error = capsys.readouterr().err
    assert "still written" in error
    assert "transcript.txt" in error
    assert "paste-into-chat.txt" in error


def test_bad_start_exits_2(monkeypatch, capsys):
    """source: a start time the tool cannot read is bad input and exits 2"""
    install_sources(monkeypatch, caption_vtt=SIMPLE_VTT)
    code = yt_condense.main([VIDEO_URL, "--from", "nope"])
    assert code == 2
    error = capsys.readouterr().err
    assert "nope" in error
    assert FakeYDL.calls == []


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        ("ERROR: Private video. Sign in if you've been granted access to this video", "private"),
        ("ERROR: This video has been removed by the uploader", "removed"),
        ("ERROR: The uploader has not made this video available in your country", "region locked"),
        ("ERROR: Sign in to confirm your age. This video may be inappropriate for some users.", "age gated"),
    ],
)
def test_unreadable_video_exits_3_and_names_the_reason(monkeypatch, tmp_path, capsys, message, kind):
    """source: a private, removed, region locked, or age gated video exits 3 and says which yt-dlp reported"""
    install_sources(monkeypatch, caption_error=DownloadError(message))
    code = yt_condense.main([VIDEO_URL, "--provider", "none", "--out", str(tmp_path / kind)])
    assert code == 3
    error = capsys.readouterr().err
    assert kind in error
    assert message in error
    assert "yt-dlp reported" in error
    assert not (tmp_path / kind).exists()


def test_copy_sends_the_paste_text_to_the_first_clipboard_tool(monkeypatch, tmp_path):
    """source: --copy puts the paste file on the clipboard with pbcopy, clip, wl-copy, or xclip"""
    install_sources(monkeypatch, caption_vtt=SIMPLE_VTT)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    copied: dict[str, bytes] = {}

    def which(name):
        return "/bin/pbcopy" if name == "pbcopy" else None

    def run(command, input=None, check=False):
        copied["command"] = command
        copied["input"] = input
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(yt_condense.shutil, "which", which)
    monkeypatch.setattr(yt_condense.subprocess, "run", run)
    out = tmp_path / "copy"
    code = yt_condense.main([VIDEO_URL, "--provider", "none", "--copy", "--out", str(out)])
    assert code == 0
    assert copied["command"] == ["pbcopy"]
    assert copied["input"] == (out / "paste-into-chat.txt").read_bytes()


def test_auto_picks_claude_even_when_an_anthropic_key_is_set(monkeypatch, tmp_path, capsys):
    # source: Door 26, 11-12: a Claude plan user should not need an API key
    install_sources(monkeypatch, caption_vtt=SIMPLE_VTT)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    which_calls: list[str] = []
    runs: list[dict] = []

    def which(name, *args, **kwargs):
        which_calls.append(name)
        if name == "claude":
            return "claude"
        return None

    def run(command, input=None, capture_output=False, text=False, timeout=None):
        runs.append(
            {
                "command": command,
                "input": input,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
            }
        )
        return types.SimpleNamespace(returncode=0, stdout="From the Claude plan.\n", stderr="")

    def fake_urlopen(request, timeout=None):
        raise AssertionError(request.full_url)

    monkeypatch.setattr(yt_condense.shutil, "which", which)
    monkeypatch.setattr(yt_condense.subprocess, "run", run)
    monkeypatch.setattr(yt_condense.urllib.request, "urlopen", fake_urlopen)
    out = tmp_path / "claude-auto"
    code = yt_condense.main([VIDEO_URL, "--provider", "auto", "--out", str(out)])
    assert code == 0
    assert which_calls == ["claude"]
    assert len(runs) == 1
    expected = yt_condense.fill_prompt(
        "Fixture Title",
        VIDEO_URL,
        0,
        20,
        "[00:00] hello from the captions\n",
    )
    assert "hello from the captions" in expected
    assert runs[0]["command"] == ["claude", "-p", "--output-format", "text"]
    assert runs[0]["input"] == expected
    assert runs[0]["capture_output"] is True
    assert runs[0]["text"] is True
    assert runs[0]["timeout"] == 900
    assert (out / "condensed.txt").read_text(encoding="utf-8") == "From the Claude plan.\n"
    assert "Using claude" in capsys.readouterr().out


def test_auto_falls_through_to_anthropic_when_claude_is_absent(monkeypatch, tmp_path, capsys):
    # source: Door 26, 11-12: auto falls through to Anthropic when claude is not on PATH
    install_sources(monkeypatch, caption_vtt=SIMPLE_VTT)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    which_calls: list[str] = []

    def which(name, *args, **kwargs):
        which_calls.append(name)
        return None

    def run(command, input=None, capture_output=False, text=False, timeout=None):
        raise AssertionError(command)

    def fake_urlopen(request, timeout=None):
        sent = json.loads(request.data.decode("utf-8"))
        assert request.full_url == "https://api.anthropic.com/v1/messages"
        assert sent["model"] == "claude-sonnet-5-5"
        return http_response({"content": [{"type": "text", "text": "From Anthropic."}]})

    monkeypatch.setattr(yt_condense.shutil, "which", which)
    monkeypatch.setattr(yt_condense.subprocess, "run", run)
    monkeypatch.setattr(yt_condense.urllib.request, "urlopen", fake_urlopen)
    out = tmp_path / "no-claude"
    code = yt_condense.main([VIDEO_URL, "--provider", "auto", "--out", str(out)])
    assert code == 0
    assert which_calls == ["claude"]
    assert (out / "condensed.txt").read_text(encoding="utf-8") == "From Anthropic.\n"
    assert "Using anthropic" in capsys.readouterr().out


def test_claude_nonzero_exit_exits_4_and_keeps_transcript(monkeypatch, tmp_path, capsys):
    # source: Door 26, 11-12: a failing claude command exits 4 and still writes the transcript and paste file
    install_sources(monkeypatch, caption_vtt=SIMPLE_VTT)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    kept = "fail-" * 60
    assert len(kept) == 300
    stderr_text = kept + "ENDMARKER"

    def which(name, *args, **kwargs):
        return None

    def run(command, input=None, capture_output=False, text=False, timeout=None):
        return types.SimpleNamespace(returncode=1, stdout="", stderr=stderr_text)

    def fake_urlopen(request, timeout=None):
        raise AssertionError(request.full_url)

    monkeypatch.setattr(yt_condense.shutil, "which", which)
    monkeypatch.setattr(yt_condense.subprocess, "run", run)
    monkeypatch.setattr(yt_condense.urllib.request, "urlopen", fake_urlopen)
    out = tmp_path / "claude-fail"
    code = yt_condense.main([VIDEO_URL, "--provider", "claude", "--out", str(out)])
    assert code == 4
    assert (out / "transcript.txt").read_text(encoding="utf-8") == "[00:00] hello from the captions\n"
    paste = (out / "paste-into-chat.txt").read_text(encoding="utf-8")
    assert "hello from the captions" in paste
    assert not (out / "condensed.txt").exists()
    error = capsys.readouterr().err
    assert kept in error
    assert "ENDMARKER" not in error
    assert "still written" in error


def test_claude_timeout_exits_4_and_keeps_transcript(monkeypatch, tmp_path, capsys):
    # source: Door 26, 11-12: a claude timeout is a provider failure and the transcript is still written
    install_sources(monkeypatch, caption_vtt=SIMPLE_VTT)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    kept = "T" * 300

    def which(name, *args, **kwargs):
        return None

    def run(command, input=None, capture_output=False, text=False, timeout=None):
        assert timeout == 900
        raise subprocess.TimeoutExpired(command, timeout, stderr=kept + "TIMEOUTTAIL")

    def fake_urlopen(request, timeout=None):
        raise AssertionError(request.full_url)

    monkeypatch.setattr(yt_condense.shutil, "which", which)
    monkeypatch.setattr(yt_condense.subprocess, "run", run)
    monkeypatch.setattr(yt_condense.urllib.request, "urlopen", fake_urlopen)
    out = tmp_path / "claude-timeout"
    code = yt_condense.main([VIDEO_URL, "--provider", "claude", "--out", str(out)])
    assert code == 4
    assert (out / "transcript.txt").is_file()
    assert (out / "paste-into-chat.txt").is_file()
    assert not (out / "condensed.txt").exists()
    error = capsys.readouterr().err
    assert kept in error
    assert "TIMEOUTTAIL" not in error


def test_claude_empty_stdout_exits_4_and_keeps_transcript(monkeypatch, tmp_path, capsys):
    # source: Door 26, 11-12: claude with empty stdout is a provider failure
    install_sources(monkeypatch, caption_vtt=SIMPLE_VTT)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def which(name, *args, **kwargs):
        return None

    def run(command, input=None, capture_output=False, text=False, timeout=None):
        return types.SimpleNamespace(returncode=0, stdout="", stderr="no text came back")

    def fake_urlopen(request, timeout=None):
        raise AssertionError(request.full_url)

    monkeypatch.setattr(yt_condense.shutil, "which", which)
    monkeypatch.setattr(yt_condense.subprocess, "run", run)
    monkeypatch.setattr(yt_condense.urllib.request, "urlopen", fake_urlopen)
    out = tmp_path / "claude-empty"
    code = yt_condense.main([VIDEO_URL, "--provider", "claude", "--out", str(out)])
    assert code == 4
    assert (out / "transcript.txt").is_file()
    assert (out / "paste-into-chat.txt").is_file()
    assert not (out / "condensed.txt").exists()
    assert "no text came back" in capsys.readouterr().err


def test_claude_model_flag_is_passed_through(monkeypatch, tmp_path):
    # source: Door 26, 11-12: --model is passed through to claude
    install_sources(monkeypatch, caption_vtt=SIMPLE_VTT)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runs: list[dict] = []

    def which(name, *args, **kwargs):
        return None

    def run(command, input=None, capture_output=False, text=False, timeout=None):
        runs.append({"command": command, "input": input})
        return types.SimpleNamespace(returncode=0, stdout="Named model summary.\n", stderr="")

    def fake_urlopen(request, timeout=None):
        raise AssertionError(request.full_url)

    monkeypatch.setattr(yt_condense.shutil, "which", which)
    monkeypatch.setattr(yt_condense.subprocess, "run", run)
    monkeypatch.setattr(yt_condense.urllib.request, "urlopen", fake_urlopen)
    out = tmp_path / "claude-model"
    code = yt_condense.main(
        [
            VIDEO_URL,
            "--provider",
            "claude",
            "--model",
            "example-model",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    assert runs[0]["command"] == [
        "claude",
        "-p",
        "--output-format",
        "text",
        "--model",
        "example-model",
    ]
    assert "hello from the captions" in runs[0]["input"]
    assert (out / "condensed.txt").read_text(encoding="utf-8") == "Named model summary.\n"


def test_public_whisper_models_do_not_send_an_implicit_huggingface_token(monkeypatch):
    # source: Door 26, 11-12: an expired Hugging Face login must not be sent to a public whisper model
    install_sources(monkeypatch, caption_vtt=None)
    monkeypatch.delenv("HF_HUB_DISABLE_IMPLICIT_TOKEN", raising=False)
    seen: list[str | None] = []
    real_import = builtins.__import__

    def tracking_import(name, *args, **kwargs):
        if name == "faster_whisper":
            seen.append(os.environ.get("HF_HUB_DISABLE_IMPLICIT_TOKEN"))
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", tracking_import)
    heard = yt_condense.transcribe_audio(Path("clip.m4a"), "tiny", "en")
    assert heard
    assert seen == ["1"]
    assert os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] == "1"

    seen.clear()
    monkeypatch.setenv("HF_HUB_DISABLE_IMPLICIT_TOKEN", "0")
    yt_condense.transcribe_audio(Path("clip.m4a"), "tiny", "en")
    assert seen == ["0"]
    assert os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] == "0"
