/** @type {import('next').NextConfig} */

const FASTAPI_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// Extract hostname from the FastAPI URL for the images remote pattern
function fastapiHostname() {
  try {
    return new URL(FASTAPI_URL).hostname;
  } catch {
    return "localhost";
  }
}

function fastapiPort() {
  try {
    return new URL(FASTAPI_URL).port;
  } catch {
    return "";
  }
}

const nextConfig = {
  // ── Dev proxy: forward /api/v1/* to the FastAPI server ─────────────────
  // This means the browser can call /api/v1/... without CORS issues in dev.
  // In production, configure your reverse proxy (nginx / Caddy) instead.
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${FASTAPI_URL}/api/v1/:path*`,
      },
    ];
  },

  // ── Allow Next/Image to load images from the FastAPI host ───────────────
  images: {
    remotePatterns: [
      {
        protocol: FASTAPI_URL.startsWith("https") ? "https" : "http",
        hostname: fastapiHostname(),
        port: fastapiPort(),
        pathname: "/**",
      },
    ],
  },
};

export default nextConfig;
