# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "yt-dlp",
#   "faster-whisper",
# ]
#
# [tool.uv]
# constraint-dependencies = ["av>=11,<19"]
# ///
"""Turn a YouTube video into a shorter piece of spoken prose.

Captions are used when the video has them. Otherwise the audio is downloaded
and transcribed locally. No API key is required.

PyAV 19 dropped the metadata_errors argument that faster-whisper passes to
av.open. The constraint above keeps the PyAV release that call still accepts.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROMPT = """Transform this YouTube transcript into a condensed audio summary.

RULES:
- Flowing conversational prose (NO bullets, NO lists, NO markdown)
- Short sentences suitable for text-to-speech
- Remove ALL filler: "um", "like", "you know", "basically", "so"
- Remove ALL meta-content: intros, outros, "subscribe", sponsors, "in this video"
- Remove ALL repetition: if a point was made, don't restate it
- Keep ONLY: novel insights, specific facts, actionable advice, concrete examples
- Target ~{length}% of original length
- Start IMMEDIATELY with content (no preamble)

VIDEO: {title} ({url})
FROM TIMESTAMP: {from_seconds} seconds

TRANSCRIPT:
{transcript}"""

CHUNK_LIMIT = 60000
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"
AUDIO_SUFFIXES = {".m4a", ".webm", ".opus", ".mp3", ".ogg", ".wav", ".flac", ".m4b"}

TIMING_LINE = re.compile(
    r"^(?P<start>(?:\d{1,3}:)?\d{2}:\d{2}[.,]\d{1,3})\s+-->\s+"
    r"(?P<end>(?:\d{1,3}:)?\d{2}:\d{2}[.,]\d{1,3})"
)
TAG_PATTERN = re.compile(r"<[^>]*>")


class VideoReadError(Exception):
    """yt-dlp could not read the video."""


class ProviderError(Exception):
    """The selected model provider did not return condensed text."""


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        start = resolve_start(args.url, args.start)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.length < 1:
        print("--length must be a positive number of percent.", file=sys.stderr)
        return 2
    if args.provider == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        return 2

    try:
        segments, meta = load_segments(
            args.url, args.lang, args.transcribe, args.whisper_model
        )
    except VideoReadError as exc:
        print(describe_video_error(str(exc)), file=sys.stderr)
        return 3

    segments = [(start_at, text) for start_at, text in segments if start_at >= start and text.strip()]
    if not segments:
        print(
            "No transcript text remains at that start time. "
            "Check the URL, the language, and --from.",
            file=sys.stderr,
        )
        return 2

    out_dir = Path(args.out) if args.out else Path("out") / meta["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    transcript = render_transcript(segments)
    paste = fill_prompt(meta["title"], meta["url"], start, args.length, transcript)
    transcript_path = out_dir / "transcript.txt"
    paste_path = out_dir / "paste-into-chat.txt"
    condensed_path = out_dir / "condensed.txt"
    transcript_path.write_text(transcript, encoding="utf-8")
    paste_path.write_text(paste if paste.endswith("\n") else paste + "\n", encoding="utf-8")
    if condensed_path.exists():
        condensed_path.unlink()

    before = word_count(" ".join(text for _, text in segments))
    try:
        provider, ollama_models = resolve_provider(args.provider)
    except ProviderError as exc:
        return provider_failure(exc, transcript_path, paste_path)

    if provider == "none":
        print(f"Words before: {before}")
        print("Words after: no model ran")
        print(transcript_path)
        print(paste_path)
        print(f"No model was called. Paste this file into any chat model: {paste_path}")
        if args.copy:
            copy_text(paste_path.read_text(encoding="utf-8"))
        if args.speak:
            print("Nothing was condensed, so there is no audio to write.")
        return 0

    try:
        condensed = condense_segments(
            segments,
            meta,
            start,
            args.length,
            provider,
            args.model,
            ollama_models,
        )
    except ProviderError as exc:
        return provider_failure(exc, transcript_path, paste_path)

    condensed_path.write_text(condensed.rstrip() + "\n", encoding="utf-8")
    after = word_count(condensed)
    print(f"Words before: {before}")
    print(f"Words after: {after}")
    print(transcript_path)
    print(condensed_path)
    print(paste_path)
    if args.speak:
        write_speech(condensed, out_dir / "condensed.m4a")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="yt_condense.py",
        description="Turn a YouTube video into a shorter piece of spoken prose.",
    )
    parser.add_argument("url", help="YouTube watch, youtu.be, or shorts URL")
    parser.add_argument(
        "--from",
        dest="start",
        default=None,
        help="Start at seconds (750), mm:ss (12:30), or h:mm:ss (1:02:30). "
        "When omitted, a t= value in the URL is used.",
    )
    parser.add_argument(
        "--length",
        type=int,
        default=20,
        help="Target condensed length as a percent of the transcript (default 20)",
    )
    parser.add_argument("--lang", default="en", help="Caption and transcription language (default en)")
    parser.add_argument(
        "--transcribe",
        action="store_true",
        help="Download the audio and transcribe it even when captions exist",
    )
    parser.add_argument(
        "--whisper-model",
        default="small",
        help="faster-whisper model name (default small). tiny and base are smaller downloads",
    )
    parser.add_argument(
        "--provider",
        choices=("auto", "anthropic", "ollama", "none"),
        default="auto",
        help="auto uses Anthropic when ANTHROPIC_API_KEY is set, otherwise Ollama, otherwise none",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=f"Model name. Anthropic defaults to {DEFAULT_ANTHROPIC_MODEL}. "
        "Ollama defaults to the first model listed by the local server",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output directory (default ./out/<video id>/)",
    )
    parser.add_argument(
        "--speak",
        action="store_true",
        help="On macOS, write condensed.m4a with say and afconvert",
    )
    parser.add_argument(
        "--copy",
        action="store_true",
        help="Copy the paste file to the clipboard (pbcopy, clip, wl-copy, or xclip)",
    )
    return parser.parse_args(argv)


def resolve_start(url: str, start: str | None) -> float:
    if start is not None:
        return parse_start(start)
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    values = query.get("t")
    if not values and parsed.fragment:
        values = urllib.parse.parse_qs(parsed.fragment).get("t")
    if not values:
        return 0.0
    try:
        return parse_start(values[0])
    except ValueError as exc:
        raise ValueError(f"The t= value in the URL could not be read. {exc}") from exc


def parse_start(value: str) -> float:
    """Accept 750, 750s, 12:30, 1:02:30, and YouTube forms such as 12m30s."""
    text = value.strip()
    if re.fullmatch(r"\d+", text):
        return float(text)
    if re.fullmatch(r"\d+s", text):
        return float(text[:-1])
    clock = re.fullmatch(r"(?:(\d+):)?(\d+):(\d{2})", text)
    if clock:
        hours = int(clock.group(1) or 0)
        minutes = int(clock.group(2))
        seconds = int(clock.group(3))
        if seconds >= 60 or (clock.group(1) is not None and minutes >= 60):
            raise ValueError(
                f"Could not read the start time {value!r}. "
                "Use seconds (750), mm:ss (12:30), or h:mm:ss (1:02:30)."
            )
        return float(hours * 3600 + minutes * 60 + seconds)
    hms = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", text)
    if hms and any(hms.groups()):
        hours = int(hms.group(1) or 0)
        minutes = int(hms.group(2) or 0)
        seconds = int(hms.group(3) or 0)
        if minutes >= 60 or seconds >= 60:
            raise ValueError(
                f"Could not read the start time {value!r}. "
                "Use seconds (750), mm:ss (12:30), or h:mm:ss (1:02:30)."
            )
        return float(hours * 3600 + minutes * 60 + seconds)
    raise ValueError(
        f"Could not read the start time {value!r}. "
        "Use seconds (750), mm:ss (12:30), or h:mm:ss (1:02:30)."
    )


def load_segments(
    url: str, lang: str, transcribe: bool, whisper_model: str
) -> tuple[list[tuple[float, str]], dict[str, str]]:
    with tempfile.TemporaryDirectory(prefix="yt-condense-") as tmp:
        directory = Path(tmp)
        if not transcribe:
            info = run_ytdlp(caption_options(directory, lang), url)
            caption_text = read_captions(info, directory)
            segments = parse_vtt(caption_text) if caption_text else []
            if segments:
                return segments, meta_from(info, url)
        info = run_ytdlp(audio_options(directory), url)
        audio_path = find_audio(info, directory)
        if audio_path is None:
            raise VideoReadError("yt-dlp did not save an audio file")
        segments = transcribe_audio(audio_path, whisper_model, lang)
        return segments, meta_from(info, url)


def caption_options(directory: Path, lang: str) -> dict:
    # Manual tracks win for a language. Automatic tracks fill languages that
    # have no manual track. yt-dlp applies that order in process_subtitles
    # when both write flags are set. skip_download still writes the subtitle file.
    return {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": [lang],
        "subtitlesformat": "vtt",
        "outtmpl": str(directory / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "retries": 3,
    }


def audio_options(directory: Path) -> dict:
    # An m4a or webm audio-only stream saves without ffmpeg.
    return {
        "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio",
        "outtmpl": str(directory / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "overwrites": True,
        "retries": 3,
    }


def run_ytdlp(options: dict, url: str) -> dict:
    import yt_dlp
    from yt_dlp.utils import DownloadError

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except DownloadError as exc:
        raise VideoReadError(str(exc)) from exc
    if not isinstance(info, dict) or not info.get("id"):
        raise VideoReadError("yt-dlp returned no video information")
    return info


def read_captions(info: dict, directory: Path) -> str | None:
    requested = info.get("requested_subtitles") or {}
    paths: list[Path] = []
    if isinstance(requested, dict):
        for item in requested.values():
            if isinstance(item, dict) and item.get("filepath"):
                paths.append(Path(item["filepath"]))
    if not paths:
        paths = sorted(directory.glob("*.vtt"))
    for path in paths:
        if path.is_file() and path.stat().st_size > 0:
            return path.read_text(encoding="utf-8", errors="replace")
    return None


def find_audio(info: dict, directory: Path) -> Path | None:
    candidates: list[Path] = []
    if info.get("filepath"):
        candidates.append(Path(info["filepath"]))
    for item in info.get("requested_downloads") or []:
        if isinstance(item, dict) and item.get("filepath"):
            candidates.append(Path(item["filepath"]))
    if directory.is_dir():
        candidates.extend(
            path for path in directory.iterdir() if path.suffix.lower() in AUDIO_SUFFIXES
        )
    for path in candidates:
        if (
            path.is_file()
            and path.stat().st_size > 0
            and path.suffix.lower() in AUDIO_SUFFIXES
        ):
            return path
    return None


def meta_from(info: dict, url: str) -> dict[str, str]:
    raw_id = str(info.get("id") or "video")
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", raw_id) or "video"
    title = str(info.get("title") or safe_id)
    page = str(info.get("webpage_url") or url)
    return {"id": safe_id, "title": title, "url": page}


def parse_vtt(text: str) -> list[tuple[float, str]]:
    """Parse WebVTT into timed lines, then drop rolling auto-caption repeats."""
    cleaned = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[tuple[float, str]] = []
    for block in re.split(r"\n\s*\n", cleaned):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        timing_at = None
        start = 0.0
        for index, line in enumerate(lines):
            match = TIMING_LINE.match(line)
            if match:
                timing_at = index
                start = clock_to_seconds(match.group("start"))
                break
        if timing_at is None:
            continue
        spoken: list[str] = []
        for line in lines[timing_at + 1 :]:
            plain = TAG_PATTERN.sub("", line)
            plain = html.unescape(plain)
            plain = re.sub(r"\s+", " ", plain).strip()
            if plain:
                spoken.append(plain)
        if spoken:
            cues.append((start, " ".join(spoken)))
    return dedupe_rolling_cues(cues)


def clock_to_seconds(token: str) -> float:
    token = token.replace(",", ".")
    parts = token.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    minutes, seconds = parts
    return int(minutes) * 60 + float(seconds)


def dedupe_rolling_cues(cues: list[tuple[float, str]]) -> list[tuple[float, str]]:
    """Drop the lines YouTube auto captions repeat from one cue into the next.

    Each cue is compared with the previous cue's full text. A cue that only
    repeats that text is dropped. A cue that extends it, or overlaps it by
    several words, keeps the new tail. Wrapped lines inside one cue are already
    joined, so a normal manual caption stays intact.
    """
    segments: list[tuple[float, str]] = []
    previous = ""
    for start, text in cues:
        fresh = fresh_caption_text(previous, text)
        previous = text
        if fresh:
            segments.append((start, fresh))
    return segments


def fresh_caption_text(previous: str, current: str) -> str:
    previous = previous.strip()
    current = current.strip()
    if not current:
        return ""
    if not previous:
        return current
    if current == previous or previous.endswith(" " + current):
        return ""
    if current.startswith(previous) and (
        len(current) == len(previous) or current[len(previous)].isspace()
    ):
        return current[len(previous) :].strip()
    previous_words = previous.split()
    current_words = current.split()
    overlap = 0
    for size in range(min(len(previous_words), len(current_words)), 0, -1):
        if previous_words[-size:] == current_words[:size]:
            overlap = size
            break
    if overlap >= 3 or overlap == len(current_words):
        return " ".join(current_words[overlap:])
    return current


def transcribe_audio(path: Path, model_name: str, lang: str) -> list[tuple[float, str]]:
    # The whisper models are public. Without this, an expired saved Hugging Face
    # login is sent with the download and the download is refused.
    os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
    import faster_whisper

    model = faster_whisper.WhisperModel(model_name, device="auto", compute_type="int8")
    iterator, info = model.transcribe(str(path), language=lang or None)
    total_minutes = float(getattr(info, "duration", 0.0) or 0.0) / 60
    segments: list[tuple[float, str]] = []
    for segment in iterator:
        text = segment.text.strip()
        if text:
            segments.append((float(segment.start), text))
        done_minutes = float(segment.end) / 60
        print(f"{done_minutes:.1f} of {total_minutes:.1f} minutes", flush=True)
    return segments


def format_stamp(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    return f"[{minutes:02d}:{secs:02d}]"


def render_transcript(segments: list[tuple[float, str]]) -> str:
    lines = [
        f"{format_stamp(start)} {text.strip()}"
        for start, text in segments
        if text.strip()
    ]
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def chunk_segments(
    segments: list[tuple[float, str]], limit: int = CHUNK_LIMIT
) -> list[list[tuple[float, str]]]:
    """Split only between segments once the rendered transcript passes the limit."""
    chunks: list[list[tuple[float, str]]] = []
    current: list[tuple[float, str]] = []
    current_length = 0
    for segment in segments:
        line = f"{format_stamp(segment[0])} {segment[1].strip()}"
        extra = len(line) if not current else len(line) + 1
        if current and current_length + extra > limit:
            chunks.append(current)
            current = [segment]
            current_length = len(line)
        else:
            current.append(segment)
            current_length += extra
    if current:
        chunks.append(current)
    return chunks


def fill_prompt(title: str, url: str, from_seconds: float, length: int, transcript: str) -> str:
    head, tail = PROMPT.split("{transcript}")
    head = (
        head.replace("{length}", str(length))
        .replace("{title}", title)
        .replace("{url}", url)
        .replace("{from_seconds}", str(int(from_seconds)))
    )
    return head + transcript.rstrip("\n") + tail


def resolve_provider(choice: str) -> tuple[str, list[str] | None]:
    if choice == "anthropic":
        return "anthropic", None
    if choice == "none":
        return "none", None
    if choice == "ollama":
        models = ollama_tags(2)
        if models is None:
            raise ProviderError("Ollama did not answer at http://localhost:11434/api/tags")
        return "ollama", models
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic", None
    models = ollama_tags(2)
    if models is not None:
        return "ollama", models
    return "none", None


def ollama_tags(timeout: float) -> list[str] | None:
    request = urllib.request.Request(OLLAMA_TAGS_URL, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            body = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    if status != 200:
        return None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    names: list[str] = []
    for item in payload.get("models") or []:
        if isinstance(item, dict):
            name = item.get("name") or item.get("model")
            if isinstance(name, str) and name:
                names.append(name)
    return names


def condense_segments(
    segments: list[tuple[float, str]],
    meta: dict[str, str],
    from_seconds: float,
    length: int,
    provider: str,
    model: str | None,
    ollama_models: list[str] | None,
) -> str:
    chunks = chunk_segments(segments)
    if provider == "anthropic":
        chosen = model or DEFAULT_ANTHROPIC_MODEL
    else:
        chosen = select_ollama_model(model, ollama_models)
    if len(chunks) > 1:
        print(f"Condensing the transcript in {len(chunks)} parts.")
    pieces: list[str] = []
    for chunk in chunks:
        prompt = fill_prompt(
            meta["title"],
            meta["url"],
            from_seconds,
            length,
            render_transcript(chunk),
        )
        if provider == "anthropic":
            pieces.append(condense_anthropic(prompt, chosen))
        else:
            pieces.append(condense_ollama(prompt, chosen))
    return "\n\n".join(piece.strip() for piece in pieces if piece.strip())


def select_ollama_model(explicit: str | None, known: list[str] | None) -> str:
    if explicit:
        print(f"Using Ollama model {explicit}")
        return explicit
    names = known if known is not None else ollama_tags(2)
    if not names:
        raise ProviderError(
            "Ollama did not list a model. Pass --model, or pull a model in Ollama."
        )
    print(f"Using Ollama model {names[0]}")
    return names[0]


def condense_anthropic(prompt: str, model: str) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise ProviderError("ANTHROPIC_API_KEY is not set.")
    payload = {
        "model": model,
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = post_json(ANTHROPIC_URL, payload, headers, 120)
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ProviderError("Anthropic returned a response that was not JSON.") from exc
    parts: list[str] = []
    for block in data.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    condensed = "".join(parts).strip()
    if not condensed:
        raise ProviderError("Anthropic returned no text.")
    return condensed


def condense_ollama(prompt: str, model: str) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    body = post_json(OLLAMA_CHAT_URL, payload, {"content-type": "application/json"}, 120)
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ProviderError("Ollama returned a response that was not JSON.") from exc
    message = data.get("message") if isinstance(data, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ProviderError("Ollama returned no text.")
    return content.strip()


def post_json(url: str, payload: dict, headers: dict[str, str], timeout: float) -> str:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
        except Exception:
            raw = b""
        detail = raw.decode("utf-8", errors="replace") if raw else (exc.reason or "")
        raise ProviderError(f"HTTP {exc.code} from {url}: {detail[:300]}") from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"Could not reach {url}: {exc.reason}") from exc


def provider_failure(exc: ProviderError, transcript_path: Path, paste_path: Path) -> int:
    print(
        f"The provider call failed: {exc}. "
        f"The transcript and paste file were still written: {transcript_path} and {paste_path}",
        file=sys.stderr,
    )
    return 4


def describe_video_error(message: str) -> str:
    text = message.lower()
    if "private" in text:
        kind = "private"
    elif "age" in text or "sign in to confirm" in text:
        kind = "age gated"
    elif "country" in text or "region" in text or "geo" in text:
        kind = "region locked"
    elif "removed" in text or "deleted" in text or "terminated" in text:
        kind = "removed"
    else:
        return f"The video could not be read. yt-dlp reported: {message}"
    return f"The video could not be read ({kind}). yt-dlp reported: {message}"


def word_count(text: str) -> int:
    return len(text.split())


def copy_text(text: str) -> None:
    commands = (
        ["pbcopy"],
        ["clip"],
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
    )
    for command in commands:
        if shutil.which(command[0]):
            try:
                subprocess.run(command, input=text.encode("utf-8"), check=True)
            except (OSError, subprocess.CalledProcessError) as exc:
                print(f"Could not copy to the clipboard with {command[0]}: {exc}")
                return
            print(f"Copied the paste file to the clipboard with {command[0]}.")
            return
    print("Could not copy to the clipboard. None of pbcopy, clip, wl-copy, or xclip was found.")


def write_speech(text: str, dest: Path) -> None:
    if sys.platform != "darwin":
        print(
            "Speech output needs macOS, which provides the say and afconvert commands. "
            "On this system, pass condensed.txt to your own text to speech tool."
        )
        return
    aiff = dest.with_suffix(".aiff")
    try:
        subprocess.run(["say", "-o", str(aiff)], input=text.encode("utf-8"), check=True)
        subprocess.run(
            ["afconvert", "-f", "m4af", "-d", "aac", str(aiff), str(dest)],
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Speech output failed: {exc}")
        return
    finally:
        if aiff.exists():
            aiff.unlink()
    print(dest)


if __name__ == "__main__":
    sys.exit(main())
