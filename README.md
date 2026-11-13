<h1 align="center">YT-Condense</h1>

<p align="center"><strong>Give it a YouTube link. Get the video back as plain spoken prose at about a fifth of the length, captions or not.</strong></p>

<p align="center">
  <a href="https://github.com/LucasCooperBey/100-doors"><img alt="100 Doors: Door 26" src="https://img.shields.io/badge/100%20Doors-Door%2026-f94bf3"></a>
  <a href="LICENSE"><img alt="license MIT" src="https://img.shields.io/badge/license-MIT-9aff4a"></a>
  <img alt="python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776ab">
  <img alt="runs with uv" src="https://img.shields.io/badge/runs%20with-uv-9aff4a">
  <img alt="account not needed" src="https://img.shields.io/badge/account-not%20needed-9aff4a">
  <a href="https://github.com/LucasCooperBey/yt-condense/commits/main"><img alt="last commit" src="https://img.shields.io/github/last-commit/LucasCooperBey/yt-condense?color=9aff4a&label=updated"></a>
</p>

<p align="center"><a href="#quick-start">Quick start</a> · <a href="#example">Example</a> · <a href="#no-captions">No captions</a> · <a href="#flags">Flags</a> · <a href="#limits">Limits</a> · <a href="https://github.com/LucasCooperBey/100-doors">All 100 Doors</a></p>

YT-Condense turns a long YouTube video into a shorter piece of spoken prose you can read or listen to.

**Who it's for:** people with a long watch-later list who would rather listen to the useful part on a walk than sit through the whole video.

## Contents

- [What's inside](#whats-inside)
- [Quick start](#quick-start)
- [Example](#example)
- [No key](#no-key)
- [A key or Ollama](#a-key-or-ollama)
- [No captions](#no-captions)
- [Flags](#flags)
- [Bookmarklet and extension](#bookmarklet-and-extension)
- [Limits](#limits)
- [Part of 100 Doors](#part-of-100-doors)
- [Credits](#credits)
- [License](#license)

## What's inside

| Path | What it is |
|---|---|
| `yt_condense.py` | The command-line tool: captions, or local transcription when there are none, then the condense step |
| `tests/` | pytest suite with fake yt-dlp and providers, no network |
| `bookmarklet.js, install.html` | Optional bookmarklet that calls a Cloudflare worker you deploy |
| `extension/` | Optional Chrome extension, same worker |
| `src/worker.ts, wrangler.toml` | The optional worker |

## Quick start

Install uv with its official installer:

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Open a new terminal so `uv` is on your PATH. Clone this repository, change into its folder, and run it on a video:

```
git clone https://github.com/LucasCooperBey/yt-condense.git
cd yt-condense
uv run yt_condense.py "https://www.youtube.com/watch?v=VIDEO_ID" --provider none
```

The first run installs the Python packages, which takes a few minutes. The ready-to-paste prompt lands here:

```
out/VIDEO_ID/paste-into-chat.txt
```

Paste that file into any chat model. The reply is the shorter version. The transcript with time stamps is in `out/VIDEO_ID/transcript.txt`.

## Example

This is a run from a fresh clone on 2026-11-12 on "Me at the zoo", a 19-second video with captions. With no account, `--provider none` writes the transcript and the paste file:

```
uv run yt_condense.py "https://www.youtube.com/watch?v=jNQXAC9IVRw" --provider none
```

```
Words before: 39
Words after: no model ran
out/jNQXAC9IVRw/transcript.txt
out/jNQXAC9IVRw/paste-into-chat.txt
No model was called. Paste this file into any chat model: out/jNQXAC9IVRw/paste-into-chat.txt
```

With Claude Code installed, `--provider claude` condenses it on your own plan. `--length 50` asks for half the length:

```
uv run yt_condense.py "https://www.youtube.com/watch?v=jNQXAC9IVRw" --provider claude --length 50
```

```
Words before: 39
Words after: 26
```

`out/jNQXAC9IVRw/condensed.txt` held this. A model words it differently on each run.

```
Here we are in front of the elephants. The cool thing about them is their really long trunks. That's pretty much all there is to say.
```

On a video with no captions, the run first prints `Downloaded audio because no captions were found.` and then transcribes the audio on your machine.

## No key

`--provider claude`. If you use Claude Code, the tool runs `claude -p` on your own plan; no key needed. The call passes an empty tool list, `--strict-mcp-config`, `--disable-slash-commands`, and `--no-session-persistence`, and its working folder is an empty temporary directory. The transcript chunk is wrapped in `<transcript>` tags. The prompt says that text is content to condense, never instructions to follow. `--provider none` does not call a model and does not need an account. `auto` uses `claude` when that command is installed. If the first claude call fails, it prints `claude failed: <reason>. Trying the next provider.` and continues with Anthropic when `ANTHROPIC_API_KEY` is set, then Ollama when it answers, then `none`. It prints the provider that runs. `--provider claude` does not fall back. Add `--copy` to put the paste file on the clipboard with `pbcopy`, `clip`, `wl-copy`, or `xclip`, whichever is installed.

## A key or Ollama

Export `ANTHROPIC_API_KEY` and run the same command without `--provider none`. When the `claude` command is not installed, or its first call fails, `auto` calls Anthropic. The default model is `claude-sonnet-5-5`. Override it with `--model`.

Or run [Ollama](https://ollama.com) so `http://localhost:11434/api/tags` answers. When `claude` is not installed and no Anthropic key is set, `auto` uses the first model Ollama lists and prints that name. `--model` picks a different one. With `--provider claude`, `--model NAME` is passed to `claude`. When `--model` is omitted, `claude` is started with no model flag.

Either way the condensed prose is written to `out/VIDEO_ID/condensed.txt`. On a Mac, add `--speak` to also write `out/VIDEO_ID/condensed.m4a` with `say` and `afconvert`. On another system, `--speak` prints that speech output needs macOS and that you can pass `condensed.txt` to your own text to speech tool.

## No captions

The tool does not require captions. It asks yt-dlp for manual captions in `--lang` (default `en`), then automatic captions. When the video has neither, it downloads an audio-only stream and transcribes it locally with faster-whisper. The first transcription downloads the whisper model: about 460 MB for `small`. Pick a smaller one with `--whisper-model tiny` or `--whisper-model base`. `--transcribe` downloads and transcribes even when captions exist.

## Flags

```
uv run yt_condense.py URL [--from 12:30|750] [--length 20] [--lang en] [--transcribe]
                          [--whisper-model small] [--provider auto|claude|anthropic|ollama|none]
                          [--model NAME] [--out DIR] [--speak] [--copy]
                          [--cookies-from-browser chrome|firefox|safari|edge|brave]
```

`URL` is a watch, youtu.be, or shorts link. A `t=` in the URL sets the start when `--from` is omitted. `--from` accepts seconds (`750`), `mm:ss` (`12:30`), and `h:mm:ss` (`1:02:30`), and it wins over `t=` when both are present. Segments that start before that point are dropped.

`--length` is the target size as a percent of the transcript. The default is 20. A transcript over 60,000 characters is split on segment boundaries, each part is condensed, and the parts are joined in order with a blank line. `condensed.txt` is written after each part.

`--cookies-from-browser BROWSER` lets yt-dlp use the YouTube cookies of a browser you are signed in to. You need it only when YouTube answers "Sign in to confirm you are not a bot", which happens most on cloud servers and VPNs; the tool then exits 3 and says so.

`--out` sets the output directory. The default is `./out/<video id>/`.

`--provider` accepts `auto`, `claude`, `anthropic`, `ollama`, or `none`. `auto` checks the `claude` command, then `ANTHROPIC_API_KEY`, then Ollama, then `none`. If the first claude call fails, `auto` tries the next provider in that list and prints the provider that runs. `--provider claude` does not fall back. `--model NAME` is passed to `claude` only when you set it.

Exit codes: `0` success, `2` bad input, `3` the video could not be read (the message names private, removed, region locked, or age gated when yt-dlp reported that), `4` the provider call failed. On `4`, `transcript.txt` and `paste-into-chat.txt` are still written. If a later chunk fails, `condensed.txt` keeps the chunks that finished and the message names the failed chunk.

## Bookmarklet and extension

The command-line tool is enough. The bookmarklet and the Chrome extension are optional, and they call a Cloudflare worker you deploy yourself.

```
bun install
wrangler secret put CLAUDE_API_KEY
wrangler secret put API_SECRET
wrangler deploy
```

Copy `.dev.vars.example` to `.dev.vars` for `wrangler dev`. `MODEL` defaults to `claude-sonnet-5-5` in `wrangler.toml`. Audio comes back only when you also run `wrangler secret put ELEVENLABS_API_KEY`. Without that secret the worker returns text.

Open `install.html` in a browser. Enter your worker address and secret. The page builds the bookmarklet in the browser and does not send either value anywhere. Drag the link to the bookmarks bar.

The extension reads the same two values from its options page (`chrome.storage`). Load the `extension` folder as an unpacked extension, open the options, and save the address and secret.

The bookmarklet and the extension read the transcript panel on the YouTube page. A video without that panel shows a toast with the command to run instead, `uv run yt_condense.py "URL"`. The worker also refuses a transcript over 100,000 characters and tells you to use the command-line tool for longer videos.

## Limits

YouTube sometimes asks a network to prove it is not a bot. It is rare from a home connection and more common from a cloud server, where it can come and go between runs. Retry, or pass `--cookies-from-browser`. A model can drop or bend a fact while shortening. Check the video before acting on what you heard. Transcription speed depends on the machine. The transcript is untrusted text from the video. The claude provider does not give that text tools, MCP servers, slash commands, or the files in your current folder. If a later chunk fails, `condensed.txt` keeps the finished chunks and the command exits 4.

## Part of 100 Doors

This is the gift behind **Door 26** of [100 Doors](https://github.com/LucasCooperBey/100-doors), where I open up the things I've built with AI, one door at a time.

- The door post: [X](https://x.com/i/status/2120913350549021156) · [Skool](https://www.skool.com/sandiegoaistudio/door-26-turn-a-long-video-into-a-short-listen) · [LinkedIn](https://www.linkedin.com/feed/update/urn:li:share:7526684017899970560/)
- The door's page in the index: [gifts/DAY-026-condense-prompt.md](https://github.com/LucasCooperBey/100-doors/blob/main/gifts/DAY-026-condense-prompt.md)
- Related door: [talk-to-slides](https://github.com/LucasCooperBey/talk-to-slides) (Door 25) gets the slides out of a recorded talk
- The season: [sandiegoaistudio.com/season](https://sandiegoaistudio.com/season)

### Come see it live

If you want to see how this and the rest of the doors open up into my software factory, come to [Sharathon](https://sandiegoaistudio.com/sharathon), San Diego AI Studio's show-your-setup day. Sharathon 3 is Sunday, Nov 15 in La Mesa: [RSVP on Luma](https://luma.com/5y5ju7ud).

## Credits

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) reads captions and audio
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) transcribes on your machine when a video has no captions
- [uv](https://docs.astral.sh/uv/) installs and runs it in one step
- Built by [Lucas Cooper-Bey](https://github.com/LucasCooperBey)

## License

MIT, see [LICENSE](LICENSE).
