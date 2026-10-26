import { createApp } from './app.js';

const env = process.env;
const config = {
  port: Number(env.PORT || 3000),
  host: env.HOST || '127.0.0.1',
  dataDir: env.DATA_DIR || './data',
  adminToken: env.ADMIN_TOKEN || '',
  trustProxy: env.TRUST_PROXY === '1' || env.TRUST_PROXY === 'true',
  quoteRateLimit: Number(env.QUOTE_RATE_LIMIT || 5),
};

if (env.NODE_ENV === 'production' && !config.adminToken) {
  console.warn('ADMIN_TOKEN is not set: GET /api/quotes is disabled.');
}

const server = createApp(config);
server.headersTimeout = 15_000;
server.requestTimeout = 30_000;
server.listen(config.port, config.host, () => {
  console.log(JSON.stringify({ level: 'info', event: 'listening', host: config.host, port: config.port }));
});

const shutdown = (signal) => {
  console.log(JSON.stringify({ level: 'info', event: 'shutdown', signal }));
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(1), 10_000).unref();
};
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
