import type { NextConfig } from "next";

const config: NextConfig = {
  // A self-contained server in .next/standalone, for a small Docker image.
  output: "standalone",
  reactStrictMode: true,
  // Slide images and the video come from presigned storage URLs, so the browser loads them
  // directly with plain <img> and <video> tags; nothing goes through Next's image optimiser.
  images: { unoptimized: true },
};

export default config;
