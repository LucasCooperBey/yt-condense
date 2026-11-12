// Bookmarklet source. install.html fills the two placeholders in the browser.
// This file has no worker address and no secret.

globalThis.YT_CONDENSE_TEMPLATE = `(async () => {
  const WORKER_URL = __WORKER_URL__;
  const API_SECRET = __API_SECRET__;
  const toast = document.createElement("div");
  Object.assign(toast.style, {
    position: "fixed",
    top: "20px",
    right: "20px",
    padding: "16px 24px",
    background: "#1a1a1a",
    color: "#fff",
    borderRadius: "8px",
    zIndex: "99999",
    fontFamily: "system-ui",
    fontSize: "14px",
    boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
    maxWidth: "420px"
  });
  toast.textContent = "Opening the transcript...";
  document.body.appendChild(toast);
  const fail = (message) => {
    toast.textContent = message;
    toast.style.background = "#dc2626";
    setTimeout(() => toast.remove(), 12000);
  };
  try {
    const video = document.querySelector("video");
    const currentTime = video ? Math.floor(video.currentTime) : 0;
    const page = new URL(location.href);
    let videoId = page.searchParams.get("v");
    const parts = page.pathname.split("/").filter(Boolean);
    if (!videoId && (parts[0] === "shorts" || parts[0] === "live" || parts[0] === "embed") && parts[1]) {
      videoId = parts[1];
    }
    if (!videoId && page.hostname === "youtu.be" && parts[0]) {
      videoId = parts[0];
    }
    const watchUrl = videoId ? "https://www.youtube.com/watch?v=" + videoId : location.href.split("#")[0];
    const command = 'uv run yt_condense.py "' + watchUrl + '"';
    let segments = document.querySelectorAll("ytd-transcript-segment-renderer");
    if (!segments.length) {
      const showTranscriptBtn = document.querySelector('button[aria-label="Show transcript"]');
      if (showTranscriptBtn) {
        showTranscriptBtn.click();
        await new Promise((resolve) => setTimeout(resolve, 1500));
        segments = document.querySelectorAll("ytd-transcript-segment-renderer");
      }
    }
    if (!segments.length) {
      const menuBtn = document.querySelector('#button-shape button[aria-label="More actions"]')
        || document.querySelector('button[aria-label="More actions"]')
        || document.querySelector("ytd-menu-renderer button");
      if (menuBtn) {
        menuBtn.click();
        await new Promise((resolve) => setTimeout(resolve, 500));
        const menuItems = document.querySelectorAll("ytd-menu-service-item-renderer, tp-yt-paper-item");
        for (const item of menuItems) {
          if ((item.textContent || "").toLowerCase().includes("transcript")) {
            item.click();
            await new Promise((resolve) => setTimeout(resolve, 1500));
            break;
          }
        }
        segments = document.querySelectorAll("ytd-transcript-segment-renderer");
      }
    }
    if (!segments.length) {
      const expandBtn = document.querySelector("tp-yt-paper-button#expand")
        || document.querySelector("#description-inline-expander #expand");
      if (expandBtn) {
        expandBtn.click();
        await new Promise((resolve) => setTimeout(resolve, 500));
        const transcriptBtn = document.querySelector('button[aria-label="Show transcript"]');
        if (transcriptBtn) {
          transcriptBtn.click();
          await new Promise((resolve) => setTimeout(resolve, 1500));
          segments = document.querySelectorAll("ytd-transcript-segment-renderer");
        }
      }
    }
    if (!segments.length) {
      fail("No transcript panel on this page. Run the command-line tool: " + command);
      return;
    }
    toast.textContent = "Reading the transcript...";
    let transcript = "";
    segments.forEach((seg) => {
      const timeEl = seg.querySelector(".segment-timestamp");
      const textEl = seg.querySelector(".segment-text, yt-formatted-string");
      if (!timeEl || !textEl) {
        return;
      }
      const timeParts = timeEl.textContent.trim().split(":").map(Number);
      let seconds = 0;
      if (timeParts.length === 2) {
        seconds = timeParts[0] * 60 + timeParts[1];
      } else if (timeParts.length === 3) {
        seconds = timeParts[0] * 3600 + timeParts[1] * 60 + timeParts[2];
      }
      if (seconds >= currentTime) {
        transcript += textEl.textContent.trim() + " ";
      }
    });
    if (transcript.length < 100) {
      fail("The transcript from this point is shorter than 100 characters. Rewind and try again, or run: " + command);
      return;
    }
    toast.textContent = "Sending the transcript...";
    const response = await fetch(WORKER_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer " + API_SECRET
      },
      body: JSON.stringify({
        transcript: transcript,
        videoId: videoId || "unknown",
        title: document.title,
        url: watchUrl,
        fromTimestamp: currentTime,
        length: 20,
        outputFormat: "text"
      })
    });
    if (!response.ok) {
      fail(await response.text());
      return;
    }
    const condensed = await response.text();
    await navigator.clipboard.writeText(condensed);
    toast.textContent = "Copied to the clipboard.";
    toast.style.background = "#16a34a";
    setTimeout(() => toast.remove(), 6000);
  } catch (err) {
    fail(err && err.message ? err.message : "The bookmarklet failed.");
  }
})();`;
