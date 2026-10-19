/** @type {import('next').NextConfig} */
const backend = (process.env.BACKEND_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

const nextConfig = {
  // Forward /api/* to the Django project so the browser only ever talks to one origin
  // (first-party cookies, no CORS). Next normalises away a trailing "/" on rewrites, but every
  // Django API URL ends with "/" (a redirect would break POSTs), so the destination re-adds it.
  skipTrailingSlashRedirect: true,
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*/` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "same-origin" },
        ],
      },
    ];
  },
};
export default nextConfig;
