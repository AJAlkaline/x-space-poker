import { useEffect, useState } from "react";

const KEY = "spaces-poker:handle";

export function useHandle() {
  const [handle, setHandle] = useState<string | null>(null);

  useEffect(() => {
    setHandle(localStorage.getItem(KEY));
  }, []);

  const save = (h: string) => {
    localStorage.setItem(KEY, h);
    setHandle(h);
  };

  const clear = () => {
    localStorage.removeItem(KEY);
    setHandle(null);
  };

  return { handle, save, clear };
}
