// Local-development cip_token issuer for the Dockerized Apollo stack.
// Generates a keypair once (reused on later runs), writes jwks.json for the
// apollo-jwks sidecar, and prints a freshly signed 30-day platform-admin
// cip_token. Never used outside localhost; Apollo's real verifier and env
// vars do all the work, no application code is bypassed.
import { generateKeyPair, exportJWK, exportPKCS8, importPKCS8, SignJWT } from 'jose';
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const dir = process.env.OUT_DIR ?? join(homedir(), '.apollo-local-dev');
const pemPath = join(dir, 'private-key.pem');
const KID = 'apollo-local-dev-1';

let privateKey;
if (existsSync(pemPath)) {
  privateKey = await importPKCS8(readFileSync(pemPath, 'utf8'), 'RS256', { extractable: true });
} else {
  const pair = await generateKeyPair('RS256', { extractable: true });
  privateKey = pair.privateKey;
  writeFileSync(pemPath, await exportPKCS8(pair.privateKey), { mode: 0o600 });
}

const privJwk = await exportJWK(privateKey);
// Public JWK = RSA private JWK minus the private members.
const publicJwk = { kty: privJwk.kty, n: privJwk.n, e: privJwk.e };
writeFileSync(join(dir, 'jwks.json'), JSON.stringify({
  keys: [{ ...publicJwk, kid: KID, alg: 'RS256', use: 'sig' }],
}, null, 2));

const token = await new SignJWT({
  actor_id: 'local-dev-operator',
  tenant_id: 'local-dev',
  preferred_username: 'local-dev@tekstream.com',
  roles: ['cip.admin'],
  cosmos_role: 'platform_admin',
  customer_scope: ['*'],
  purposes_granted: [],
  amr: ['pwd', 'mfa'],
})
  .setProtectedHeader({ alg: 'RS256', kid: KID })
  .setIssuer('https://cip-auth.tekstream/v1')
  .setAudience('cip-spokes')
  .setSubject('local-dev-operator')
  .setIssuedAt()
  .setExpirationTime('30d')
  .sign(privateKey);

writeFileSync(join(dir, 'cip_token.txt'), token);
console.log('jwks.json and cip_token.txt written to', dir);
