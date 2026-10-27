import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { mergeConfig } from 'vite';
import base from './vite.config';

const tls = join(
  process.env.LOCALAPPDATA!,
  'Anastylos', 'receiver', 'tls'
);

export default mergeConfig(base, {
  server: {
    host: '192.168.0.110',
    port: 5173,
    strictPort: true,
    https: {
      cert: readFileSync(join(tls, 'cert.pem')),
      key: readFileSync(join(tls, 'key.pem'))
    }
  }
});
