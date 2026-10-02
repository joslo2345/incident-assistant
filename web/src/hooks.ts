import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

export function useGet<T>(path: string | null): { data: T | null; error: unknown; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!path) return;
    let live = true;
    api.get<T>(path).then(
      (d) => live && (setData(d), setError(null)),
      (e) => live && setError(e),
    );
    return () => {
      live = false;
    };
  }, [path, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, reload };
}
