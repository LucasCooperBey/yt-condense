let audioPlayer = null;
let isProcessing = false;

function init() {
  const checkPlayer = setInterval(() => {
    const controls = document.querySelector(".ytp-right-controls");
    if (controls && !document.getElementById("yt-condense-btn")) {
      addCondenseButton(controls);
      clearInterval(checkPlayer);
    }
  }, 1000);
}

function addCondenseButton(controls) {
  const btn = document.createElement("button");
  btn.id = "yt-condense-btn";
  btn.className = "ytp-button";
  btn.title = "Condense and listen from the current time";

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "24");
  svg.setAttribute("height", "24");
  svg.setAttribute("fill", "white");

  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z");
  svg.appendChild(path);
  btn.appendChild(svg);

  btn.onclick = handleCondense;
  controls.insertBefore(btn, controls.firstChild);
}

function commandForThisVideo() {
  const page = new URL(location.href);
  let videoId = page.searchParams.get("v");
  const parts = page.pathname.split("/").filter(Boolean);
  if (!videoId && (parts[0] === "shorts" || parts[0] === "live" || parts[0] === "embed") && parts[1]) {
    videoId = parts[1];
  }
  const watchUrl = videoId
    ? "https://www.youtube.com/watch?v=" + videoId
    : location.href.split("#")[0];
  return 'uv run yt_condense.py "' + watchUrl + '"';
}

function loadConfig() {
  return new Promise((resolve) => {
    chrome.storage.local.get(["workerUrl", "apiSecret"], (stored) => {
      resolve({
        workerUrl: (stored && stored.workerUrl) || "",
        apiSecret: (stored && stored.apiSecret) || "",
      });
    });
  });
}

async function handleCondense() {
  if (isProcessing) return;
  isProcessing = true;

  const btn = document.getElementById("yt-condense-btn");
  const originalContent = btn.firstChild;

  try {
    btn.textContent = "";
    const spinner = document.createElement("div");
    spinner.className = "yt-condense-spinner";
    btn.appendChild(spinner);

    const config = await loadConfig();
    if (!config.workerUrl || !config.apiSecret) {
      showToast("Set the worker address and secret on the extension options page.", true);
      return;
    }

    showToast("Opening the transcript...");
    const video = document.querySelector("video");
    const currentTime = video ? Math.floor(video.currentTime) : 0;
    const page = new URL(location.href);
    const videoId = page.searchParams.get("v") || "unknown";
    const transcript = await getTranscript(currentTime);

    if (!transcript || transcript.length < 100) {
      throw new Error(
        "The transcript from this point is shorter than 100 characters. Rewind and try again, or run: " +
          commandForThisVideo()
      );
    }

    showToast("Condensing " + Math.round(transcript.length / 1000) + "k chars...");

    const response = await fetch(config.workerUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer " + config.apiSecret,
      },
      body: JSON.stringify({
        transcript,
        videoId,
        title: document.title,
        url: location.href.split("#")[0],
        fromTimestamp: currentTime,
        length: 20,
        outputFormat: "audio",
      }),
    });

    if (!response.ok) {
      throw new Error(await response.text());
    }

    const result = await readResult(response);
    if (result.audio) {
      showToast("Playing audio...");
      playAudio(result.audio, result.text || "");
    } else if (result.text) {
      await navigator.clipboard.writeText(result.text);
      showToast("Copied the condensed text to the clipboard.");
    } else {
      throw new Error("The worker returned no text.");
    }
  } catch (err) {
    showToast(err.message, true);
    console.error("YT Condense error:", err);
  } finally {
    btn.textContent = "";
    btn.appendChild(originalContent);
    isProcessing = false;
  }
}

async function readResult(response) {
  const raw = await response.text();
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object") {
      return {
        text: typeof parsed.text === "string" ? parsed.text : "",
        audio: typeof parsed.audio === "string" ? parsed.audio : "",
      };
    }
  } catch (err) {
    return { text: raw, audio: "" };
  }
  return { text: raw, audio: "" };
}

async function getTranscript(fromTimestamp) {
  let segments = document.querySelectorAll("ytd-transcript-segment-renderer");

  if (!segments.length) {
    const expandDesc = document.querySelector("#expand") ||
      document.querySelector("tp-yt-paper-button#expand");
    if (expandDesc) {
      expandDesc.click();
      await sleep(500);
    }

    const showTranscriptBtn = document.querySelector('button[aria-label="Show transcript"]');
    if (showTranscriptBtn) {
      showTranscriptBtn.click();
      await sleep(1500);
      segments = document.querySelectorAll("ytd-transcript-segment-renderer");
    }
  }

  if (!segments.length) {
    const menuBtn = document.querySelector('button[aria-label="More actions"]');
    if (menuBtn) {
      menuBtn.click();
      await sleep(500);
      const menuItems = document.querySelectorAll("ytd-menu-service-item-renderer");
      for (const item of menuItems) {
        if ((item.textContent || "").toLowerCase().includes("transcript")) {
          item.click();
          await sleep(1500);
          break;
        }
      }
      segments = document.querySelectorAll("ytd-transcript-segment-renderer");
    }
  }

  if (!segments.length) {
    throw new Error(
      "No transcript panel on this page. Run the command-line tool: " + commandForThisVideo()
    );
  }

  let transcript = "";
  segments.forEach((seg) => {
    const timeEl = seg.querySelector(".segment-timestamp");
    const textEl = seg.querySelector(".segment-text, yt-formatted-string");
    if (!timeEl || !textEl) return;

    const timeParts = timeEl.textContent.trim().split(":").map(Number);
    let seconds = 0;
    if (timeParts.length === 2) {
      seconds = timeParts[0] * 60 + timeParts[1];
    } else if (timeParts.length === 3) {
      seconds = timeParts[0] * 3600 + timeParts[1] * 60 + timeParts[2];
    }

    if (seconds >= fromTimestamp) {
      transcript += textEl.textContent.trim() + " ";
    }
  });

  return transcript;
}

function playAudio(base64Audio, text) {
  if (audioPlayer) {
    audioPlayer.remove();
  }

  audioPlayer = document.createElement("div");
  audioPlayer.id = "yt-condense-player";

  const header = document.createElement("div");
  header.className = "yt-condense-player-header";

  const title = document.createElement("span");
  title.textContent = "YT Condense";
  header.appendChild(title);

  const closeBtn = document.createElement("button");
  closeBtn.className = "yt-condense-close";
  closeBtn.textContent = "\u00D7";
  closeBtn.onclick = () => {
    const audio = audioPlayer.querySelector("audio");
    if (audio) audio.pause();
    audioPlayer.remove();
    audioPlayer = null;
  };
  header.appendChild(closeBtn);
  audioPlayer.appendChild(header);

  const audio = document.createElement("audio");
  audio.controls = true;
  audio.autoplay = true;

  const source = document.createElement("source");
  source.src = "data:audio/mpeg;base64," + base64Audio;
  source.type = "audio/mpeg";
  audio.appendChild(source);
  audioPlayer.appendChild(audio);

  const textPreview = document.createElement("div");
  textPreview.className = "yt-condense-text";
  textPreview.textContent = text.length > 200 ? text.substring(0, 200) + "..." : text;
  audioPlayer.appendChild(textPreview);

  const copyBtn = document.createElement("button");
  copyBtn.className = "yt-condense-copy";
  copyBtn.textContent = "Copy full text";
  copyBtn.onclick = async () => {
    await navigator.clipboard.writeText(text);
    showToast("Copied to the clipboard.");
  };
  audioPlayer.appendChild(copyBtn);

  document.body.appendChild(audioPlayer);

  const ytVideo = document.querySelector("video");
  if (ytVideo) ytVideo.pause();
}

function showToast(message, isError) {
  const existing = document.getElementById("yt-condense-toast");
  if (existing) existing.remove();

  const toast = document.createElement("div");
  toast.id = "yt-condense-toast";
  if (isError) toast.className = "error";
  toast.textContent = message;
  document.body.appendChild(toast);

  setTimeout(() => toast.remove(), isError ? 12000 : 6000);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

init();

let lastUrl = location.href;
new MutationObserver(() => {
  if (location.href !== lastUrl) {
    lastUrl = location.href;
    if (location.href.includes("youtube.com/watch")) {
      setTimeout(init, 1000);
    }
  }
}).observe(document.body, { subtree: true, childList: true });
