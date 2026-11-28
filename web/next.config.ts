import type { NextConfig } from "next";

// The console is a client-side app (all data comes from the FastAPI service with a per-user
// API key held in the browser), so prerendering features like Cache Components / Partial
// Prefetching add nothing here and are left off.
const nextConfig: NextConfig = {
  output: "standalone",
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
