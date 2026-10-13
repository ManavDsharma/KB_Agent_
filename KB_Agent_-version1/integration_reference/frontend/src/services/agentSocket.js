import { BASE_URL } from "./apiClient";

const WS_URL = `${BASE_URL.replace(/^http/, "ws")}/ws/agent`;

const MAX_BACKOFF_MS = 10000;

// Thin wrapper around the native WebSocket so components never touch the
// transport directly — same convention as the fetch-based services.
//
// Reconnects automatically (1s, 2s, 4s ... capped at 10s) so a backend
// restart or a dropped connection doesn't leave the chat offline until the
// page is reloaded. close() stops reconnecting.
export function createAgentSocket({ onMessage, onOpen, onClose, onError }) {
  let socket = null;
  let closedByUser = false;
  let attempt = 0;
  let retryTimer = null;

  const connect = () => {
    socket = new WebSocket(WS_URL);

    socket.onopen = () => {
      attempt = 0;
      onOpen?.();
    };
    socket.onclose = () => {
      onClose?.();
      if (closedByUser) return;
      const delay = Math.min(1000 * 2 ** attempt, MAX_BACKOFF_MS);
      attempt += 1;
      retryTimer = setTimeout(connect, delay);
    };
    socket.onerror = (event) => onError?.(event);
    socket.onmessage = (event) => {
      try {
        onMessage(JSON.parse(event.data));
      } catch {
        // ignore malformed frames rather than crashing the chat
      }
    };
  };

  connect();

  return {
    sendPrompt(prompt, sessionId) {
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ prompt, session_id: sessionId }));
        return true;
      }
      return false;
    },
    close() {
      closedByUser = true;
      clearTimeout(retryTimer);
      socket?.close();
    },
  };
}
