import { useEffect, useRef } from "react";

export function useEventSource(
  url: string,
  handlers: Record<string, (data: unknown) => void>,
) {
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    const es = new EventSource(url);
    const types = Object.keys(handlersRef.current);

    for (const type of types) {
      es.addEventListener(type, (ev) => {
        try {
          const data = JSON.parse((ev as MessageEvent).data);
          handlersRef.current[type]?.(data);
        } catch {
          // ignore malformed
        }
      });
    }

    return () => es.close();
  }, [url]);
}
