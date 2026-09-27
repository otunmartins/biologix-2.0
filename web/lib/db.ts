import 'server-only';
import { Pool } from 'pg';

// One pool for the web app: Auth.js's adapter and the password sign-in share it.
// Neon's POOLED string, same as the API's. A handful of connections is plenty:
// the web app only touches the database on sign-in and on page loads.
export const pool = new Pool({ connectionString: process.env.DATABASE_URL, max: 5 });
