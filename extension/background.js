chrome.action.onClicked.addListener(async (tab) => {
  const stored = await chrome.storage.local.get(["workerUrl", "apiSecret"]);
  if (!stored.workerUrl || !stored.apiSecret) {
    await chrome.runtime.openOptionsPage();
    return;
  }
  if (!tab.id) {
    return;
  }
  if (tab.url && tab.url.includes("youtube.com/watch")) {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => {
        const button = document.getElementById("yt-condense-btn");
        if (button) {
          button.click();
        } else {
          alert("YT Condense: wait for the page to finish loading, then try again.");
        }
      },
    });
    return;
  }
  await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => {
      alert("YT Condense: open a YouTube video first.");
    },
  });
});
