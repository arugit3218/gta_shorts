import { useCallback, useEffect, useRef, useState } from "react";

/** Fetch data and optionally poll it. ``interval`` may change at runtime (null = stop polling). */
export function usePoll<T>(fn: () => Promise<T>, deps: unknown[], interval: number | null = null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const reload = useCallback(async () => {
    try {
      const d = await fnRef.current();
      setData(d);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    if (interval == null) return;
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") reload();
    }, interval);
    return () => window.clearInterval(id);
  }, [interval, reload]);

  return { data, error, loading, reload, setData };
}

export function useTheme() {
  const [theme, setTheme] = useState<"system" | "light" | "dark">(() => {
    try {
      return (localStorage.getItem("theme") as "light" | "dark" | null) ?? "system";
    } catch {
      return "system";
    }
  });
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("theme", theme);
    } catch {
      /* storage unavailable */
    }
  }, [theme]);
  return { theme, setTheme };
}
