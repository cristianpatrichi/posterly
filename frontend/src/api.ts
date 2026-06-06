// Thin API client. All URLs are RELATIVE (`/api/...`) so the same code works in
// dev (Vite proxies /api -> :8787) and in the Docker prod build (FastAPI serves
// the SPA, so /api is same-origin). Every request sends the session cookie
// (credentials: "include"); a 401 broadcasts "auth:unauthorized" so the app
// drops back to the login screen.

import type {
  ExportFormat,
  ExportResponse,
  LayoutItem,
  Project,
  ProjectSummary,
  Settings,
} from "./types";

const BASE = "/api";
const JSON_HEADERS = { "Content-Type": "application/json" };

/** Error carrying the HTTP status so callers can surface concise messages. */
export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function cfetch(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(`${BASE}${path}`, { credentials: "include", ...init });
}

async function parseError(res: Response): Promise<never> {
  let detail = res.statusText;
  try {
    const body: unknown = await res.json();
    if (
      body &&
      typeof body === "object" &&
      "detail" in body &&
      typeof (body as { detail: unknown }).detail === "string"
    ) {
      detail = (body as { detail: string }).detail;
    }
  } catch {
    // Non-JSON error body — keep the status text.
  }
  throw new ApiError(res.status, detail);
}

async function asJson<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    window.dispatchEvent(new Event("auth:unauthorized"));
  }
  if (!res.ok) {
    return parseError(res);
  }
  return (await res.json()) as T;
}

// --------------------------------------------------------------------------- //
// Auth
// --------------------------------------------------------------------------- //
export async function me(): Promise<{ email: string }> {
  return asJson(await cfetch("/auth/me"));
}

export async function googleLogin(
  credential: string,
  turnstileToken?: string | null,
): Promise<{ email: string }> {
  return asJson(
    await cfetch("/auth/google", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ credential, turnstile_token: turnstileToken }),
    }),
  );
}

export async function otpRequest(
  email: string,
  turnstileToken?: string | null,
): Promise<{ ok: boolean }> {
  return asJson(
    await cfetch("/auth/otp/request", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ email, turnstile_token: turnstileToken }),
    }),
  );
}

export async function otpVerify(
  email: string,
  code: string,
): Promise<{ email: string }> {
  return asJson(
    await cfetch("/auth/otp/verify", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ email, code }),
    }),
  );
}

export async function logout(): Promise<void> {
  await cfetch("/auth/logout", { method: "POST" });
}

// --------------------------------------------------------------------------- //
// Projects
// --------------------------------------------------------------------------- //
export async function health(): Promise<{ status: string }> {
  return asJson(await cfetch("/health"));
}

export async function createProject(): Promise<Project> {
  return asJson(await cfetch("/projects", { method: "POST" }));
}

export async function listProjects(): Promise<ProjectSummary[]> {
  return asJson(await cfetch("/projects"));
}

export async function getProject(id: string): Promise<Project> {
  return asJson(await cfetch(`/projects/${id}`));
}

export async function deleteProject(id: string): Promise<void> {
  await cfetch(`/projects/${id}`, { method: "DELETE" });
}

export async function uploadImages(id: string, files: File[]): Promise<Project> {
  const form = new FormData();
  // Multipart field name MUST be `files` (matches the backend's
  // `files: list[UploadFile] = File(...)`); supports one or many.
  for (const file of files) {
    form.append("files", file);
  }
  return asJson(
    await cfetch(`/projects/${id}/images`, { method: "POST", body: form }),
  );
}

export async function updateProject(
  id: string,
  body: { settings?: Settings; layout?: LayoutItem[]; image_order?: string[] },
): Promise<Project> {
  return asJson(
    await cfetch(`/projects/${id}`, {
      method: "PUT",
      headers: JSON_HEADERS,
      body: JSON.stringify(body),
    }),
  );
}

export async function deleteImages(
  id: string,
  imageIds: string[],
): Promise<Project> {
  return asJson(
    await cfetch(`/projects/${id}/images/delete`, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ image_ids: imageIds }),
    }),
  );
}

export async function autoLayout(id: string, settings?: Settings): Promise<Project> {
  return asJson(
    await cfetch(`/projects/${id}/auto-layout`, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(settings ? { settings } : {}),
    }),
  );
}

export async function exportProject(
  id: string,
  format: ExportFormat,
): Promise<ExportResponse> {
  return asJson(
    await cfetch(`/projects/${id}/export`, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ format }),
    }),
  );
}

/** Download URL for an exported file (relative, ready for an <a download>). */
export function downloadUrl(id: string, format: "png" | "pdf"): string {
  return `${BASE}/projects/${id}/download/${format}`;
}
