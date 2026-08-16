/* SSE over fetch.
 *
 * EventSource cannot POST, and the chat request carries a body, so the stream
 * is read straight off the response with a reader and parsed here. Frames are
 * "event: <name>\ndata: <json>\n\n".
 */

export async function sseFetch(url, { body, signal, onEvent } = {}) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
    signal,
  });

  if (!res.ok || !res.body) {
    // FastAPI puts validation problems in the body; a bare status code tells
    // the user nothing they can act on.
    let detail = "";
    try {
      const payload = await res.json();
      detail =
        typeof payload?.detail === "string"
          ? payload.detail
          : Array.isArray(payload?.detail)
          ? payload.detail.map((d) => d.msg).join("; ")
          : "";
    } catch {
      /* not JSON — fall back to the status line */
    }
    const known = {
      413: "That message is too long for the server to accept.",
      429: "Too many requests — give the server a moment.",
      503: "The model is not loaded yet.",
    }[res.status];
    throw new Error(detail || known || `The server returned ${res.status} ${res.statusText}.`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let split;
    while ((split = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      let name = "message";
      const data = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trim());
      }
      if (!data.length) continue;
      let payload;
      try {
        payload = JSON.parse(data.join("\n"));
      } catch {
        payload = { raw: data.join("\n") };
      }
      onEvent?.(name, payload);
    }
  }
}
