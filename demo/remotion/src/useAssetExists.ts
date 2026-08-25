import { useEffect, useState } from "react";
import { continueRender, delayRender, staticFile } from "remotion";

// Checks whether a public/ asset actually exists (used to decide whether to
// play the real manim clip or fall back to a styled title card). Uses a HEAD
// request against the same local server Remotion serves staticFile() from,
// which works both in Studio preview and during `remotion render`.
export const useAssetExists = (relativePath: string): boolean | null => {
  const [exists, setExists] = useState<boolean | null>(null);

  useEffect(() => {
    const handle = delayRender(`check-asset-${relativePath}`);
    let cancelled = false;

    fetch(staticFile(relativePath), { method: "HEAD" })
      .then((res) => {
        if (cancelled) return;
        setExists(res.ok);
      })
      .catch(() => {
        if (cancelled) return;
        setExists(false);
      })
      .finally(() => {
        continueRender(handle);
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [relativePath]);

  return exists;
};
