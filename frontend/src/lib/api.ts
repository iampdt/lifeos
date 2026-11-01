// Fetch wrapper for /api/v1. Same-origin requests carry the session cookie; anything that
// changes state also sends the session's CSRF token (backend/app/api/security.py).

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public retryable = false,
  ) {
    super(message);
  }
}

let csrfToken: string | null = null;

export function setCsrfToken(token: string | null) {
  csrfToken = token;
}

function headers(method: string, hasBody: boolean): Record<string, string> {
  const out: Record<string, string> = {};
  if (hasBody) out["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) out["X-CSRF-Token"] = csrfToken;
  return out;
}

async function toError(response: Response): Promise<ApiError> {
  let body: { error?: { code?: string; message?: string; retryable?: boolean } } | undefined;
  try {
    body = await response.json();
  } catch {
    body = undefined;
  }
  const error = body?.error;
  return new ApiError(
    response.status,
    error?.code ?? "http_error",
    error?.message ?? response.statusText,
    error?.retryable ?? false,
  );
}

/** A change made before the session check finished still needs the CSRF token. */
async function ensureCsrf(method: string, path: string) {
  if (method === "GET" || csrfToken || path === "/auth/login") return;
  const response = await fetch("/api/v1/auth/me", { credentials: "same-origin", cache: "no-store" });
  if (response.ok) csrfToken = ((await response.json()) as { csrf_token: string | null }).csrf_token;
}

export async function api<T>(
  path: string,
  init: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const method = init.method ?? "GET";
  await ensureCsrf(method, path);
  const response = await fetch(`/api/v1${path}`, {
    method,
    headers: headers(method, init.body !== undefined),
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    credentials: "same-origin",
    cache: "no-store",
    signal: init.signal,
  });
  if (!response.ok) throw await toError(response);
  return response.json() as Promise<T>;
}

export type StreamEvent = { type: string; [key: string]: unknown };

/** POST and read a server-sent-events response, calling `onEvent` as each event arrives. */
export async function stream(
  path: string,
  body: unknown,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  await ensureCsrf("POST", path);
  const response = await fetch(`/api/v1${path}`, {
    method: "POST",
    headers: headers("POST", true),
    body: JSON.stringify(body),
    credentials: "same-origin",
    signal,
  });
  if (!response.ok || !response.body) throw await toError(response);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      for (const line of block.split("\n")) {
        if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)) as StreamEvent);
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}
