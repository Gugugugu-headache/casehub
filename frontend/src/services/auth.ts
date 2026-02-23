export type Role = "admin" | "teacher" | "student";

export interface AuthState {
  role: Role;
  id: number;
  name: string;
  admin_no?: string;
  teacher_no?: string;
  student_no?: string;
}

const STORAGE_KEY = "casehub_auth";

export const getAuth = (): AuthState | null => {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as AuthState;
  } catch {
    return null;
  }
};

export const setAuth = (state: AuthState) => {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
};

export const clearAuth = () => {
  localStorage.removeItem(STORAGE_KEY);
};
