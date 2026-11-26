import { useCallback, useEffect, useRef, useState } from "react";

/** Fetch data and optionally poll it. ``interval`` may change at runtime (null = stop polling). */
export function usePoll<T>(fn: () => Promise<T>, deps: unknown[], interval: number | null = null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const gen = useRef(0); // bumped by local updates: responses requested before them are dropped

  const reload = useCallback(async () => {
    const g = gen.current;
    try {
      const d = await fnRef.current();
      if (g !== gen.current) return;
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

  /** Fetch now and win over anything requested earlier (polls or other refreshes still in flight). */
  const refresh = useCallback(async () => {
    const g = ++gen.current;
    try {
      const d = await fnRef.current();
      if (g !== gen.current) return;
      setData(d);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  const setLocal = useCallback((d: T | null | ((x: T | null) => T | null)) => {
    gen.current++;
    setData(d);
  }, []);
  return { data, error, loading, reload, refresh, setData: setLocal };
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

/** Keyboard shortcuts. Keys: "j", "shift+a", "mod+s" (mod = Cmd on Mac, Ctrl elsewhere), "space", "?", "arrowleft".
 *  Plain keys are ignored while typing in a field or while a dialog is open; "mod+" keys always work. */
export function useHotkeys(map: Record<string, (e: KeyboardEvent) => void>, enabled = true) {
  const ref = useRef(map);
  ref.current = map;
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing = !!t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName));
      const mod = e.metaKey || e.ctrlKey;
      const dialog = !!document.querySelector(".modal-back");
      let key = e.key === " " ? "space" : e.key.toLowerCase();
      if (key === "?" || (e.shiftKey && key === "/")) key = "?";
      const name = `${mod ? "mod+" : ""}${e.shiftKey && key !== "?" ? "shift+" : ""}${key}`;
      const fn = ref.current[name];
      if (!fn) return;
      if (!mod && (typing || dialog)) return;
      // Enter / Space already activate a focused button, link or video player: don't double up
      if ((key === "enter" || key === "space") && t && ["BUTTON", "A", "VIDEO", "SUMMARY"].includes(t.tagName)) return;
      if (dialog && mod) return;
      e.preventDefault();
      fn(e);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled]);
}

/** Order of clips last shown on a project page (so the editor's Previous / Next follow the same order). */
export const clipOrder = {
  save(projectId: string, ids: string[]) {
    try {
      sessionStorage.setItem(`clipOrder:${projectId}`, JSON.stringify(ids));
    } catch {
      /* storage unavailable */
    }
  },
  load(projectId: string): string[] | null {
    try {
      const v = sessionStorage.getItem(`clipOrder:${projectId}`);
      return v ? (JSON.parse(v) as string[]) : null;
    } catch {
      return null;
    }
  },
};
