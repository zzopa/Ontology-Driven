import type { NextConfig } from "next";

const config: NextConfig = {
  output: process.env.NODE_ENV === "development" ? undefined : "export",
  poweredByHeader: false,
  images: { unoptimized: true },
  ...(process.env.NODE_ENV === "development" ? {
    async rewrites() { return [{ source: "/api/:path*", destination: "http://127.0.0.1:8088/api/:path*" }]; },
  } : {}),
};

export default config;
