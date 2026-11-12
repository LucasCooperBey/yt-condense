const workerInput = document.getElementById("worker");
const secretInput = document.getElementById("secret");
const status = document.getElementById("status");

chrome.storage.local.get(["workerUrl", "apiSecret"], (stored) => {
  workerInput.value = stored.workerUrl || "";
  secretInput.value = stored.apiSecret || "";
});

document.getElementById("save").addEventListener("click", () => {
  const workerUrl = workerInput.value.trim();
  const apiSecret = secretInput.value.trim();
  if (!workerUrl || !apiSecret) {
    status.textContent = "Enter both the worker address and the secret.";
    return;
  }
  chrome.storage.local.set({ workerUrl, apiSecret }, () => {
    status.textContent = "Saved in this browser.";
  });
});
