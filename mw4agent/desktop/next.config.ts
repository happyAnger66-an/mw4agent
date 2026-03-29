import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  // Move dev-only “Issue / build activity” pill from default bottom-left to bottom-right.
  devIndicators: {
    position: "bottom-right",
  },
};

export default nextConfig;
