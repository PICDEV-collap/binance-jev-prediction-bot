const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const standalone = path.join(root, '.next', 'standalone');
const server = path.join(standalone, 'server.js');
if (!fs.existsSync(server)) {
  throw new Error('Build this dashboard with npm run build before starting it.');
}

// Keep production confined to this project's reserved port.
process.env.PORT = '3888';
process.env.HOSTNAME = '0.0.0.0';
for (const [source, destination] of [
  [path.join(root, '.next', 'static'), path.join(standalone, '.next', 'static')],
  [path.join(root, 'public'), path.join(standalone, 'public')],
]) {
  if (fs.existsSync(source)) fs.cpSync(source, destination, { recursive: true });
}
require(server);
