// Minimal SSE parser for fetch ReadableStream reader
// Yields text from concatenated data: lines per SSE event

export async function* iterateSSE(reader, decoder = new TextDecoder("utf-8")) {
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    for (const evt of events) {
      const lines = evt.split("\n");
      const datas = [];
      for (const line of lines) {
        if (line.startsWith("data:")) {
          datas.push(line.slice(5).replace(/^\s*/, ""));
        }
      }
      if (datas.length) {
        yield datas.join("\n");
      }
    }
  }
  // Flush any trailing event without a terminating blank line
  if (buffer) {
    const lines = buffer.split("\n");
    const datas = [];
    for (const line of lines) {
      if (line.startsWith("data:")) {
        datas.push(line.slice(5).replace(/^\s*/, ""));
      }
    }
    if (datas.length) {
      yield datas.join("\n");
    }
  }
}

export function tryParseJSON(text) {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}
