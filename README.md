# YT-Condense

YT-Condense turns a long YouTube video into a shorter piece of spoken prose you can read or listen to.

## Quick start

Install uv with its official installer:

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Open a new terminal so `uv` is on your PATH. Clone this repository, change into its folder, and run:

```
uv run yt_condense.py "https://www.youtube.com/watch?v=VIDEO_ID" --provider none
```

The first run installs the Python packages, which takes a few minutes. The ready-to-paste prompt lands here:

```
out/VIDEO_ID/paste-into-chat.txt
```

Paste that file into any chat model. The reply is the shorter version. The transcript with time stamps is in `out/VIDEO_ID/transcript.txt`.

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
```

`URL` is a watch, youtu.be, or shorts link. A `t=` in the URL sets the start when `--from` is omitted. `--from` accepts seconds (`750`), `mm:ss` (`12:30`), and `h:mm:ss` (`1:02:30`), and it wins over `t=` when both are present. Segments that start before that point are dropped.

`--length` is the target size as a percent of the transcript. The default is 20. A transcript over 60,000 characters is split on segment boundaries, each part is condensed, and the parts are joined in order with a blank line. `condensed.txt` is written after each part.

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

A model can drop or bend a fact while shortening. Check the video before acting on what you heard. Transcription speed depends on the machine. The transcript is untrusted text from the video. The claude provider does not give that text tools, MCP servers, slash commands, or the files in your current folder. If a later chunk fails, `condensed.txt` keeps the finished chunks and the command exits 4.

Made by Lucas Cooper-Bey as Door 26 of 100 Doors, one free tool a day: https://sandiegoaistudio.com/season
If you like seeing how other people wire tools like this together, San Diego AI Studio runs Sharathon, where builders show their own setups: https://www.skool.com/sandiegoaistudio
