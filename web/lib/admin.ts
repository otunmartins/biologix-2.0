// Server-side only: ADMIN_EMAILS is not a NEXT_PUBLIC_ variable, so a browser bundle
// would read it as empty and refuse everyone rather than leak the list.
// Who may open /admin. The same ADMIN_EMAILS the API checks (api/users.py), read
// here on the server so the page itself is refused, not merely hidden. The API
// still checks every admin request on its own: this only decides what to render.
export function isAdminEmail(email: string | null | undefined): boolean {
  if (!email) return false;
  const admins = (process.env.ADMIN_EMAILS ?? '')
    .split(',')
    .map((e) => e.trim().toLowerCase())
    .filter(Boolean);
  return admins.includes(email.trim().toLowerCase());
}
