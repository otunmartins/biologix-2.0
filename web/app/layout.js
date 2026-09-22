import './globals.css';

export const metadata = {
  title: 'Excipient Screen',
  description: 'Chemistry-only excipient triage for biologic formulations.',
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <div className="wrap">{children}</div>
      </body>
    </html>
  );
}
