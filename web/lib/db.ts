import 'server-only';
import { Pool } from 'pg';

// One pool for the web app: Auth.js's adapter and the password sign-in share it.
// Neon's POOLED string, same as the API's. A handful of connections is plenty:
// the web app only touches the database on sign-in and on page loads.
export const pool = new Pool({ connectionString: pinSslMode(process.env.DATABASE_URL), max: 5 });

// node-postgres treats sslmode=prefer/require/verify-ca as verify-full (the
// server's certificate is checked), warns that pg v9 will weaken them to libpq's
// meaning, and says so on every start. Pin what it does today, verify-full, so
// the security cannot quietly drop in an upgrade. Here rather than in .env: the
// Python API reads the same URL through libpq, where "require" is right as is.
function pinSslMode(url: string | undefined): string | undefined {
  return url?.replace(/([?&]sslmode=)(prefer|require|verify-ca)\b/, '$1verify-full');
}
