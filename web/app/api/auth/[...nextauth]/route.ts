// Auth.js's own endpoints: /api/auth/signin, /callback/google, /signout, ...
// Caddy sends /api/* to this container, not the FastAPI one; only /screen,
// /design and /health go to the API.
import { handlers } from '@/auth';

export const { GET, POST } = handlers;
