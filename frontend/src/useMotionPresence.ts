import { useEffect, useState } from "react";

// Keep a surface mounted while its exit transition finishes.
export function useMotionPresence(open: boolean, durationVariable: string) {
  const [present, setPresent] = useState(open);
  const [phase, setPhase] = useState<"enter" | "open" | "exit">("enter");

  useEffect(() => {
    let frame = 0;
    let nextFrame = 0;
    let timer = 0;
    if (open) {
      setPresent(true);
      setPhase("enter");
      frame = requestAnimationFrame(() => {
        nextFrame = requestAnimationFrame(() => setPhase("open"));
      });
    } else if (present) {
      setPhase("exit");
      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const value = getComputedStyle(document.documentElement).getPropertyValue(durationVariable).trim();
      const parsed = parseFloat(value);
      const duration = Number.isFinite(parsed) ? parsed * (value.endsWith("ms") ? 1 : 1000) : 150;
      timer = window.setTimeout(() => setPresent(false), reduce ? 0 : duration);
    }
    return () => { cancelAnimationFrame(frame); cancelAnimationFrame(nextFrame); window.clearTimeout(timer); };
  }, [open, durationVariable, present]);

  return { present, phase };
}
