import type { NextConfig } from "next";

/**
 * Static export.
 *
 * The demo runs entirely on fixtures (see lib/fixtures.ts), so it needs no server,
 * no API key and no database — a visitor can click through the whole policy engine
 * story on a static host. That also makes deployment a single upload of `out/`.
 *
 * When pointed at the real FastAPI backend instead, drop `output: "export"` and set
 * NEXT_PUBLIC_API_URL; nothing else in the app changes.
 */
const nextConfig: NextConfig = {
  output: "export",
  reactStrictMode: true,
  images: { unoptimized: true },
  trailingSlash: true,
};

export default nextConfig;
