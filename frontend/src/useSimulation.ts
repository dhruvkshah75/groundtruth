import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiRequestError } from "./api";
import type { SimulationRunIdentity, SimulationStateResponse } from "./simulationTypes";

export const POLL_MS = 250;
type Connection = "loading" | "live" | "hidden" | "refreshing" | "disconnected";
type Command = (expected: SimulationRunIdentity, options: { sessionId: string; signal: AbortSignal }) =>
  Promise<{ state: SimulationStateResponse; message: string }>;

export function useSimulation(sessionId: string) {
  const [state, setState] = useState<SimulationStateResponse | null>(null);
  const [connection, setConnection] = useState<Connection>("loading");
  const [connectionError, setConnectionError] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);
  const latest = useRef<SimulationStateResponse | null>(null);
  const mutation = useRef<AbortController | null>(null);

  const accept = useCallback((next: SimulationStateResponse) => {
    const current = latest.current;
    if (current && next.generation <= current.generation) {
      if (next.generation < current.generation || next.run_id !== current.run_id) return false;
      if (next.revision <= current.revision) return next.revision === current.revision;
    }
    if (current && next.generation !== current.generation) {
      setNotice((previous) => previous?.error ? previous : null);
    }
    latest.current = next;
    setState(next);
    return true;
  }, []);

  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    let request: AbortController | undefined;
    let failures = 0;

    async function poll() {
      if (!active || document.hidden || request) return;
      const started = performance.now();
      const baseline = latest.current;
      const controller = new AbortController();
      request = controller;
      const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(1_500)]);
      try {
        const next = await api.simulationState({ sessionId, signal });
        if (!active || controller.signal.aborted) return;
        if (!next || typeof next.generation !== "number") throw new Error("The backend did not return simulation state.");
        if (accept(next)) {
          failures = 0;
          setConnection("live");
        } else if (latest.current === baseline) {
          throw new Error("The backend returned an older run or revision.");
        }
      } catch (cause) {
        if (!active || controller.signal.aborted) return;
        failures++;
        setConnection("disconnected");
        setConnectionError(signal.aborted ? "The state request timed out."
          : cause instanceof Error ? cause.message : "Could not read simulation state.");
      } finally {
        request = undefined;
        if (active && !document.hidden) {
          const delay = controller.signal.aborted ? 0 : failures
            ? Math.min(2_000, POLL_MS * 2 ** Math.min(failures, 3))
            : Math.max(0, POLL_MS - (performance.now() - started));
          timer = window.setTimeout(poll, delay);
        }
      }
    }

    function visibilityChanged() {
      window.clearTimeout(timer);
      if (document.hidden) {
        setConnection("hidden");
        request?.abort();
      } else {
        setConnection("refreshing");
        void poll();
      }
    }

    document.addEventListener("visibilitychange", visibilityChanged);
    if (document.hidden) setConnection("hidden");
    else void poll();
    return () => {
      active = false;
      document.removeEventListener("visibilitychange", visibilityChanged);
      window.clearTimeout(timer);
      request?.abort();
      mutation.current?.abort();
      mutation.current = null;
    };
  }, [accept, sessionId]);

  async function run(label: string, command: Command) {
    const current = latest.current;
    if (!current || connection !== "live" || mutation.current) return;
    const controller = new AbortController();
    mutation.current = controller;
    setPending(label);
    setNotice(null);
    try {
      const expected = { run_id: current.run_id, generation: current.generation };
      const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(10_000)]);
      const result = await command(expected, { sessionId, signal });
      if (controller.signal.aborted) return;
      accept(result.state);
      const currentRun = latest.current;
      const sameRun = currentRun?.run_id === result.state.run_id
        && currentRun?.generation === result.state.generation;
      setNotice(sameRun ? { text: result.message, error: false }
        : { text: "The run changed before the command response arrived.", error: true });
    } catch (cause) {
      if (controller.signal.aborted) return;
      if (cause instanceof ApiRequestError && cause.code === "stale_run") setConnection("refreshing");
      setNotice({ text: cause instanceof Error ? cause.message : "The command could not be confirmed.", error: true });
    } finally {
      if (!controller.signal.aborted) {
        mutation.current = null;
        setPending(null);
      }
    }
  }

  return { state, connection, connectionError, pending, notice, run };
}
