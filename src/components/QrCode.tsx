import qrcode from "qrcode-generator";
import { FC, useMemo } from "react";

/**
 * A QR code of `text` (a sign-in link) for a phone's camera: dark squares on white with the quiet margin round them
 * that readers need, drawn as one SVG path so it's sharp at any size.
 */
export const QrCode: FC<{ text: string; size: number }> = ({ text, size }) => {
  const { path, count } = useMemo(() => {
    const qr = qrcode(0, "M");
    qr.addData(text);
    qr.make();
    const n = qr.getModuleCount();
    let d = "";
    for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) if (qr.isDark(y, x)) d += `M${x} ${y}h1v1h-1z`;
    return { path: d, count: n };
  }, [text]);
  const margin = 4;
  return (
    <svg
      width={size}
      height={size}
      viewBox={`${-margin} ${-margin} ${count + margin * 2} ${count + margin * 2}`}
      shapeRendering="crispEdges"
      style={{ display: "block", borderRadius: 8 }}
    >
      <rect x={-margin} y={-margin} width={count + margin * 2} height={count + margin * 2} fill="#ffffff" />
      <path d={path} fill="#000000" />
    </svg>
  );
};
