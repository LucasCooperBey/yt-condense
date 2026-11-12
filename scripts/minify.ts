// Check the bookmarklet source. The install page builds the bookmark.
// Do not put a worker address or a secret in bookmarklet.js.

const source = await Bun.file("bookmarklet.js").text();

if (source.includes("workers.dev") || /api[_-]?key\s*[:=]\s*['"][A-Za-z0-9]{12,}/i.test(source)) {
  throw new Error("bookmarklet.js must not contain a worker address or a secret");
}

if (!source.includes("globalThis.YT_CONDENSE_TEMPLATE")) {
  throw new Error("bookmarklet.js is missing YT_CONDENSE_TEMPLATE");
}

if (!source.includes("__WORKER_URL__") || !source.includes("__API_SECRET__")) {
  throw new Error("bookmarklet.js is missing the install placeholders");
}

console.log("bookmarklet.js has no baked-in address or secret.");
console.log("Open install.html and enter a worker address and secret.");
console.log("The page builds the bookmarklet in the browser and does not send them anywhere.");
