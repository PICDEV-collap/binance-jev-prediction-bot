/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  swcMinify: true,
  // Allow deploying to Vercel or running as standalone static or node app
  output: process.env.VERCEL ? undefined : 'standalone',
};

module.exports = nextConfig;
