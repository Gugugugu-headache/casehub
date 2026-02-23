const apiBase = (import.meta.env.VITE_API_BASE as string) || "http://localhost:8000";

export const request = async <T>(path: string, options?: RequestInit): Promise<T> => {
  const resp = await fetch(`${apiBase}${path}`, {
    headers: { "Content-Type": "application/json", ...(options?.headers || {}) },
    ...options,
  });
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.detail || `请求失败: ${resp.status}`);
  }
  return resp.json();
};

export const getApiBase = () => apiBase;
