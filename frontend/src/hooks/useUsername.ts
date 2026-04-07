import { useState, useCallback } from "react";

const STORAGE_KEY = "digilekha_username";

function getInitial(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) || "user";
  } catch {
    return "user";
  }
}

export function useUsername() {
  const [username, setUsernameState] = useState(getInitial);

  const setUsername = useCallback((name: string) => {
    setUsernameState(name);
    try {
      localStorage.setItem(STORAGE_KEY, name);
    } catch {
      // storage unavailable
    }
  }, []);

  return { username, setUsername };
}
